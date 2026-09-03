"""Deterministic orchestration and immutable reconciliation batches."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from ledgerloom.domain.identifiers import BatchId, EntryId
from ledgerloom.domain.records import LedgerEntry
from ledgerloom.reconciliation.candidates import CandidateIndex, CandidateSelectionTrace
from ledgerloom.reconciliation.decisions import (
    ConflictPolicy,
    ConflictStrategy,
    DecisionPolicy,
    DecisionStatus,
    MatchDecision,
)
from ledgerloom.reconciliation.errors import ReconciliationBatchError
from ledgerloom.reconciliation.scoring import MAX_RULE_WEIGHT, MAX_TOTAL_SCORE, MatchScorer
from ledgerloom.rules import MatchVerdict
from ledgerloom.rules.evidence import validate_rule_code


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


def _require_window(
    value: object,
    *,
    path: str = "$.selection_policy.max_time_difference",
) -> timedelta:
    if not isinstance(value, timedelta):
        raise ReconciliationBatchError(
            code="batch.window_type",
            path=path,
            message="maximum time difference must be a timedelta",
        )
    if value < timedelta(0):
        raise ReconciliationBatchError(
            code="batch.window_negative",
            path=path,
            message="maximum time difference must not be negative",
        )
    return value


def _require_tolerance(
    value: object,
    *,
    path: str = "$.selection_policy.max_amount_difference",
) -> Decimal:
    if not isinstance(value, Decimal):
        raise ReconciliationBatchError(
            code="batch.amount_tolerance_type",
            path=path,
            message="maximum amount difference must be a Decimal",
        )
    if not value.is_finite() or value < 0:
        raise ReconciliationBatchError(
            code="batch.amount_tolerance_range",
            path=path,
            message="maximum amount difference must be finite and non-negative",
        )
    _, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)
    integer_digits = max(len(digits) + exponent, 0)
    if len(digits) > 38 or max(-exponent, 0) > 18 or integer_digits > 38:
        raise ReconciliationBatchError(
            code="batch.amount_tolerance_precision",
            path=path,
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
class ScoringRuleSnapshot:
    """Auditable rule identity, weight, and required status."""

    rule_id: str
    weight: int
    required: bool

    def __post_init__(self) -> None:
        validate_rule_code(
            self.rule_id,
            path="$.policy.scoring_rules.rule_id",
            kind="id",
        )
        if isinstance(self.weight, bool) or not isinstance(self.weight, int):
            raise ReconciliationBatchError(
                code="batch.policy_weight_type",
                path="$.policy.scoring_rules.weight",
                message="scoring rule weight must be an integer",
            )
        if not 1 <= self.weight <= MAX_RULE_WEIGHT:
            raise ReconciliationBatchError(
                code="batch.policy_weight_range",
                path="$.policy.scoring_rules.weight",
                message=f"scoring rule weight must be between 1 and {MAX_RULE_WEIGHT}",
            )
        if not isinstance(self.required, bool):
            raise ReconciliationBatchError(
                code="batch.policy_required_type",
                path="$.policy.scoring_rules.required",
                message="required must be a boolean",
            )


@dataclass(frozen=True, slots=True)
class ReconciliationPolicySnapshot:
    """Exact non-sensitive configuration that produced a batch."""

    scoring_rules: tuple[ScoringRuleSnapshot, ...]
    minimum_score: int
    minimum_margin: int
    max_time_difference: timedelta
    max_amount_difference: Decimal
    require_reference: bool
    conflict_strategy: ConflictStrategy

    def __post_init__(self) -> None:
        if not isinstance(self.scoring_rules, tuple) or not self.scoring_rules:
            raise ReconciliationBatchError(
                code="batch.policy_rules_type",
                path="$.policy.scoring_rules",
                message="scoring_rules must be a non-empty tuple",
            )
        if not all(isinstance(item, ScoringRuleSnapshot) for item in self.scoring_rules):
            raise ReconciliationBatchError(
                code="batch.policy_rule_type",
                path="$.policy.scoring_rules",
                message="scoring_rules must contain only ScoringRuleSnapshot values",
            )
        rule_ids = tuple(item.rule_id for item in self.scoring_rules)
        if len(rule_ids) != len(set(rule_ids)):
            raise ReconciliationBatchError(
                code="batch.policy_rule_duplicate",
                path="$.policy.scoring_rules",
                message="scoring rule identifiers must be unique",
            )
        for name, value in (
            ("minimum_score", self.minimum_score),
            ("minimum_margin", self.minimum_margin),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ReconciliationBatchError(
                    code="batch.policy_threshold_type",
                    path=f"$.policy.{name}",
                    message=f"{name} must be an integer",
                )
            if not 0 <= value <= MAX_TOTAL_SCORE:
                raise ReconciliationBatchError(
                    code="batch.policy_threshold_range",
                    path=f"$.policy.{name}",
                    message=f"{name} must be between 0 and {MAX_TOTAL_SCORE}",
                )
        _require_window(
            self.max_time_difference,
            path="$.policy.max_time_difference",
        )
        _require_tolerance(
            self.max_amount_difference,
            path="$.policy.max_amount_difference",
        )
        if not isinstance(self.require_reference, bool):
            raise ReconciliationBatchError(
                code="batch.policy_reference_type",
                path="$.policy.require_reference",
                message="require_reference must be a boolean",
            )
        if not isinstance(self.conflict_strategy, ConflictStrategy):
            raise ReconciliationBatchError(
                code="batch.policy_conflict_strategy_type",
                path="$.policy.conflict_strategy",
                message="conflict_strategy must be a ConflictStrategy",
            )

    @property
    def maximum_score(self) -> int:
        """Return the exact score scale implied by the retained rule weights."""

        return sum(item.weight for item in self.scoring_rules)

    @property
    def required_rule_ids(self) -> tuple[str, ...]:
        """Return required rule identifiers in evaluation order."""

        return tuple(item.rule_id for item in self.scoring_rules if item.required)


@dataclass(frozen=True, slots=True)
class BatchSelectionTrace:
    """Selection counts for one anchor, including batch-role exclusions."""

    anchor_id: EntryId
    index_trace: CandidateSelectionTrace
    excluded_batch_anchors: int
    scored_candidates: int

    def __post_init__(self) -> None:
        if not isinstance(self.anchor_id, EntryId):
            raise ReconciliationBatchError(
                code="batch.trace_anchor_type",
                path="$.selection_trace.anchor_id",
                message="anchor_id must be an EntryId",
            )
        if not isinstance(self.index_trace, CandidateSelectionTrace):
            raise ReconciliationBatchError(
                code="batch.trace_index_type",
                path="$.selection_trace.index_trace",
                message="index_trace must be a CandidateSelectionTrace",
            )
        for name, value in (
            ("excluded_batch_anchors", self.excluded_batch_anchors),
            ("scored_candidates", self.scored_candidates),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ReconciliationBatchError(
                    code="batch.trace_count_type",
                    path=f"$.selection_trace.{name}",
                    message=f"{name} must be an integer",
                )
            if value < 0:
                raise ReconciliationBatchError(
                    code="batch.trace_count_range",
                    path=f"$.selection_trace.{name}",
                    message=f"{name} must not be negative",
                )
        if (
            self.excluded_batch_anchors + self.scored_candidates
            != self.index_trace.selected
        ):
            raise ReconciliationBatchError(
                code="batch.trace_count_mismatch",
                path="$.selection_trace",
                message="excluded and scored counts must partition selected candidates",
            )


@dataclass(frozen=True, slots=True)
class ReconciliationBatch:
    """One immutable, conflict-resolved set of per-anchor decisions."""

    batch_id: BatchId
    decisions: tuple[MatchDecision, ...]
    policy_snapshot: ReconciliationPolicySnapshot | None = None
    selection_traces: tuple[BatchSelectionTrace, ...] = ()

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
        if self.policy_snapshot is not None and not isinstance(
            self.policy_snapshot, ReconciliationPolicySnapshot
        ):
            raise ReconciliationBatchError(
                code="batch.policy_snapshot_type",
                path="$.batch.policy_snapshot",
                message="policy_snapshot must be a ReconciliationPolicySnapshot or None",
            )
        if not isinstance(self.selection_traces, tuple) or not all(
            isinstance(item, BatchSelectionTrace) for item in self.selection_traces
        ):
            raise ReconciliationBatchError(
                code="batch.selection_traces_type",
                path="$.batch.selection_traces",
                message="selection_traces must be a tuple of BatchSelectionTrace values",
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
        if self.selection_traces:
            trace_anchor_ids = tuple(item.anchor_id for item in self.selection_traces)
            if trace_anchor_ids != anchor_ids:
                raise ReconciliationBatchError(
                    code="batch.selection_trace_order",
                    path="$.batch.selection_traces",
                    message="selection traces must match decision anchors in batch order",
                )
            for decision, trace in zip(ordered, self.selection_traces, strict=True):
                if trace.scored_candidates != len(decision.ranking):
                    raise ReconciliationBatchError(
                        code="batch.selection_trace_ranking_mismatch",
                        path="$.batch.selection_traces.scored_candidates",
                        message="scored candidate count must match the decision ranking",
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
        for decision in ordered:
            if decision.status is DecisionStatus.CONFLICT:
                candidate_id = _claimed_candidate_id(decision)
                assert candidate_id is not None
                if len(claim_groups[candidate_id]) < 2:
                    raise ReconciliationBatchError(
                        code="batch.orphan_conflict",
                        path="$.batch.decisions",
                        message="every conflict must identify another claim on its candidate",
                    )
        if self.policy_snapshot is not None:
            expected_rule_ids = tuple(
                item.rule_id for item in self.policy_snapshot.scoring_rules
            )
            expected_required_ids = self.policy_snapshot.required_rule_ids
            expected_maximum = self.policy_snapshot.maximum_score
            weights = {
                item.rule_id: item.weight for item in self.policy_snapshot.scoring_rules
            }
            for decision in ordered:
                for candidate in decision.ranking:
                    evidence_rule_ids = tuple(item.rule_id for item in candidate.evidence)
                    if evidence_rule_ids != expected_rule_ids:
                        raise ReconciliationBatchError(
                            code="batch.policy_rule_mismatch",
                            path="$.batch.decisions.ranking.evidence",
                            message="candidate evidence must match policy rules and order",
                        )
                    if candidate.required_rule_ids != expected_required_ids:
                        raise ReconciliationBatchError(
                            code="batch.policy_required_mismatch",
                            path="$.batch.decisions.ranking.required_rule_ids",
                            message="candidate required rules must match the policy snapshot",
                        )
                    if candidate.maximum_score != expected_maximum:
                        raise ReconciliationBatchError(
                            code="batch.policy_maximum_mismatch",
                            path="$.batch.decisions.ranking.maximum_score",
                            message="candidate maximum score must match policy rule weights",
                        )
                    expected_score = sum(
                        weights[item.rule_id]
                        for item in candidate.evidence
                        if item.verdict is MatchVerdict.MATCH
                    )
                    if candidate.score != expected_score:
                        raise ReconciliationBatchError(
                            code="batch.policy_score_mismatch",
                            path="$.batch.decisions.ranking.score",
                            message="candidate score must match policy weights and evidence",
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

    @property
    def policy_snapshot(self) -> ReconciliationPolicySnapshot:
        """Return the exact configuration safe to retain with a batch."""

        return ReconciliationPolicySnapshot(
            scoring_rules=tuple(
                ScoringRuleSnapshot(item.rule_id, item.weight, item.required)
                for item in self.scorer.rules
            ),
            minimum_score=self.decision_policy.minimum_score,
            minimum_margin=self.decision_policy.minimum_margin,
            max_time_difference=self.selection_policy.max_time_difference,
            max_amount_difference=self.selection_policy.max_amount_difference,
            require_reference=self.selection_policy.require_reference,
            conflict_strategy=self.conflict_policy.strategy,
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
        selection_traces: list[BatchSelectionTrace] = []
        for anchor in sorted(materialized, key=_entry_order):
            selection = candidate_index.select_for(
                anchor,
                max_time_difference=self.selection_policy.max_time_difference,
                max_amount_difference=self.selection_policy.max_amount_difference,
                require_reference=self.selection_policy.require_reference,
            )
            candidates = tuple(
                candidate
                for candidate in selection.candidates
                if candidate.entry_id not in anchor_ids
            )
            selection_traces.append(
                BatchSelectionTrace(
                    anchor.entry_id,
                    selection.trace,
                    excluded_batch_anchors=len(selection.candidates) - len(candidates),
                    scored_candidates=len(candidates),
                )
            )
            ranking = self.scorer.rank(anchor, candidates)
            decisions.append(self.decision_policy.decide(anchor, ranking))
        resolved = self.conflict_policy.resolve(decisions)
        return ReconciliationBatch(
            batch_id,
            resolved,
            self.policy_snapshot,
            tuple(selection_traces),
        )
