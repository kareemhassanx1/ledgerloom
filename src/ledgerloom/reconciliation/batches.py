"""Deterministic orchestration and immutable reconciliation batches."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from ledgerloom.domain.identifiers import BatchId, EntryId
from ledgerloom.domain.records import LedgerEntry
from ledgerloom.reconciliation.candidates import CandidateIndex
from ledgerloom.reconciliation.decisions import (
    ConflictPolicy,
    DecisionPolicy,
    DecisionStatus,
    MatchDecision,
)
from ledgerloom.reconciliation.errors import ReconciliationBatchError
from ledgerloom.reconciliation.scoring import MatchScorer


def _entry_order(entry: LedgerEntry) -> tuple[object, EntryId]:
    return (entry.occurred_at, entry.entry_id)


def _decision_order(decision: MatchDecision) -> tuple[object, EntryId]:
    return _entry_order(decision.anchor)


def _claimed_candidate_id(decision: MatchDecision) -> EntryId | None:
    if decision.status is DecisionStatus.MATCHED:
        assert decision.selection is not None
        return decision.selection.candidate.entry_id
    if decision.status is DecisionStatus.CONFLICT:
        return decision.contenders[0].candidate.entry_id
    return None


def _require_window(value: object) -> timedelta:
    if not isinstance(value, timedelta):
        raise ReconciliationBatchError(
            code="batch.window_type",
            path="$.selection_policy.max_time_difference",
            message="maximum time difference must be a timedelta",
        )
    if value < timedelta(0):
        raise ReconciliationBatchError(
            code="batch.window_negative",
            path="$.selection_policy.max_time_difference",
            message="maximum time difference must not be negative",
        )
    return value


def _require_tolerance(value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise ReconciliationBatchError(
            code="batch.amount_tolerance_type",
            path="$.selection_policy.max_amount_difference",
            message="maximum amount difference must be a Decimal",
        )
    if not value.is_finite() or value < 0:
        raise ReconciliationBatchError(
            code="batch.amount_tolerance_range",
            path="$.selection_policy.max_amount_difference",
            message="maximum amount difference must be finite and non-negative",
        )
    _, digits, exponent = value.as_tuple()
    integer_digits = max(len(digits) + exponent, 0)
    if len(digits) > 38 or max(-exponent, 0) > 18 or integer_digits > 38:
        raise ReconciliationBatchError(
            code="batch.amount_tolerance_precision",
            path="$.selection_policy.max_amount_difference",
            message="maximum amount difference exceeds the supported decimal precision",
        )
    return value


@dataclass(frozen=True, slots=True)
class CandidateSelectionPolicy:
    """Configure the indexed candidate search performed for every anchor."""

    max_time_difference: timedelta
    max_amount_difference: Decimal = Decimal("0")
    require_reference: bool = False

    def __post_init__(self) -> None:
        _require_window(self.max_time_difference)
        _require_tolerance(self.max_amount_difference)
        if not isinstance(self.require_reference, bool):
            raise ReconciliationBatchError(
                code="batch.require_reference_type",
                path="$.selection_policy.require_reference",
                message="require_reference must be a boolean",
            )


@dataclass(frozen=True, slots=True)
class ReconciliationBatch:
    """One immutable, conflict-resolved set of per-anchor decisions."""

    batch_id: BatchId
    decisions: tuple[MatchDecision, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.batch_id, BatchId):
            raise ReconciliationBatchError(
                code="batch.id_type",
                path="$.batch.batch_id",
                message="batch_id must be a BatchId",
            )
        if not isinstance(self.decisions, tuple):
            raise ReconciliationBatchError(
                code="batch.decisions_type",
                path="$.batch.decisions",
                message="decisions must be a tuple",
            )
        if not self.decisions:
            raise ReconciliationBatchError(
                code="batch.decisions_empty",
                path="$.batch.decisions",
                message="batch must contain at least one decision",
            )
        if not all(isinstance(item, MatchDecision) for item in self.decisions):
            raise ReconciliationBatchError(
                code="batch.decision_type",
                path="$.batch.decisions",
                message="decisions must contain only MatchDecision values",
            )
        ordered = tuple(sorted(self.decisions, key=_decision_order))
        object.__setattr__(self, "decisions", ordered)
        anchor_ids = tuple(item.anchor.entry_id for item in ordered)
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ReconciliationBatchError(
                code="batch.anchor_duplicate",
                path="$.batch.decisions",
                message="anchor identifiers must be unique within a batch",
            )
        selected_ids = tuple(
            item.selection.candidate.entry_id
            for item in ordered
            if item.status is DecisionStatus.MATCHED and item.selection is not None
        )
        if len(selected_ids) != len(set(selected_ids)):
            raise ReconciliationBatchError(
                code="batch.unresolved_conflict",
                path="$.batch.decisions",
                message="matched decisions must not reuse a candidate entry",
            )
        claim_groups: dict[EntryId, list[EntryId]] = {}
        for decision in ordered:
            candidate_id = _claimed_candidate_id(decision)
            if candidate_id is not None:
                claim_groups.setdefault(candidate_id, []).append(decision.anchor.entry_id)
        if set(anchor_ids).intersection(claim_groups):
            raise ReconciliationBatchError(
                code="batch.entry_role_overlap",
                path="$.batch.decisions",
                message="one entry must not be both an anchor and a claimed candidate",
            )
        if any(
            decision.status is DecisionStatus.CONFLICT
            and len(claim_groups[_claimed_candidate_id(decision)]) < 2
            for decision in ordered
        ):
            raise ReconciliationBatchError(
                code="batch.orphan_conflict",
                path="$.batch.decisions",
                message="every conflict must identify another claim on its candidate",
            )

    def decision_for(self, anchor_id: EntryId) -> MatchDecision:
        """Return one anchor decision, raising ``KeyError`` when absent."""

        if not isinstance(anchor_id, EntryId):
            raise ReconciliationBatchError(
                code="batch.anchor_id_type",
                path="$.anchor_id",
                message="anchor_id must be an EntryId",
            )
        for decision in self.decisions:
            if decision.anchor.entry_id == anchor_id:
                return decision
        raise KeyError(anchor_id)

    @property
    def matched_pairs(self) -> tuple[tuple[EntryId, EntryId], ...]:
        """Return conflict-free anchor/candidate identifiers in batch order."""

        return tuple(
            (decision.anchor.entry_id, decision.selection.candidate.entry_id)
            for decision in self.decisions
            if decision.status is DecisionStatus.MATCHED and decision.selection is not None
        )


@dataclass(frozen=True, slots=True)
class BatchReconciler:
    """Run selection, scoring, decisions, and conflict resolution as one workflow."""

    scorer: MatchScorer
    decision_policy: DecisionPolicy
    selection_policy: CandidateSelectionPolicy
    conflict_policy: ConflictPolicy = field(default_factory=ConflictPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.scorer, MatchScorer):
            raise ReconciliationBatchError(
                code="batch.scorer_type",
                path="$.reconciler.scorer",
                message="scorer must be a MatchScorer",
            )
        if not isinstance(self.decision_policy, DecisionPolicy):
            raise ReconciliationBatchError(
                code="batch.decision_policy_type",
                path="$.reconciler.decision_policy",
                message="decision_policy must be a DecisionPolicy",
            )
        if not isinstance(self.selection_policy, CandidateSelectionPolicy):
            raise ReconciliationBatchError(
                code="batch.selection_policy_type",
                path="$.reconciler.selection_policy",
                message="selection_policy must be a CandidateSelectionPolicy",
            )
        if not isinstance(self.conflict_policy, ConflictPolicy):
            raise ReconciliationBatchError(
                code="batch.conflict_policy_type",
                path="$.reconciler.conflict_policy",
                message="conflict_policy must be a ConflictPolicy",
            )

    def reconcile(
        self,
        batch_id: BatchId,
        anchors: Iterable[LedgerEntry],
        candidate_index: CandidateIndex,
    ) -> ReconciliationBatch:
        """Reconcile anchors against one immutable candidate-index snapshot."""

        if not isinstance(batch_id, BatchId):
            raise ReconciliationBatchError(
                code="batch.id_type",
                path="$.batch_id",
                message="batch_id must be a BatchId",
            )
        if isinstance(anchors, str | bytes) or not isinstance(anchors, Iterable):
            raise ReconciliationBatchError(
                code="batch.anchors_type",
                path="$.anchors",
                message="anchors must be an iterable of LedgerEntry values",
            )
        materialized = tuple(anchors)
        if not materialized:
            raise ReconciliationBatchError(
                code="batch.anchors_empty",
                path="$.anchors",
                message="anchors must contain at least one entry",
            )
        if not all(isinstance(anchor, LedgerEntry) for anchor in materialized):
            raise ReconciliationBatchError(
                code="batch.anchor_type",
                path="$.anchors",
                message="anchors must contain only LedgerEntry values",
            )
        anchor_ids = tuple(anchor.entry_id for anchor in materialized)
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ReconciliationBatchError(
                code="batch.anchor_duplicate",
                path="$.anchors",
                message="anchor identifiers must be unique",
            )
        if not isinstance(candidate_index, CandidateIndex):
            raise ReconciliationBatchError(
                code="batch.candidate_index_type",
                path="$.candidate_index",
                message="candidate_index must be a CandidateIndex",
            )

        decisions: list[MatchDecision] = []
        for anchor in sorted(materialized, key=_entry_order):
            candidates = tuple(
                candidate
                for candidate in candidate_index.candidates_for(
                    anchor,
                    max_time_difference=self.selection_policy.max_time_difference,
                    max_amount_difference=self.selection_policy.max_amount_difference,
                    require_reference=self.selection_policy.require_reference,
                )
                if candidate.entry_id not in anchor_ids
            )
            ranking = self.scorer.rank(anchor, candidates)
            decisions.append(self.decision_policy.decide(anchor, ranking))
        resolved = self.conflict_policy.resolve(decisions)
        return ReconciliationBatch(batch_id, resolved)
