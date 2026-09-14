"""Structured, deterministic reports for reconciliation batch outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from ledgerloom.domain.identifiers import BatchId, EntryId
from ledgerloom.reconciliation.batches import (
    BatchSelectionTrace,
    ReconciliationBatch,
    ReconciliationPolicySnapshot,
)
from ledgerloom.reconciliation.decisions import DecisionStatus, MatchDecision
from ledgerloom.reconciliation.errors import ReconciliationReportError
from ledgerloom.reconciliation.scoring import BASIS_POINT_SCALE, MAX_TOTAL_SCORE, CandidateScore
from ledgerloom.rules import MatchVerdict, RuleEvidence
from ledgerloom.rules.evidence import validate_rule_code


def _require_count(value: object, *, path: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReconciliationReportError(
            code="report.count_type",
            path=path,
            message=f"{label} must be an integer",
        )
    if value < 0:
        raise ReconciliationReportError(
            code="report.count_range",
            path=path,
            message=f"{label} must not be negative",
        )
    return value


@dataclass(frozen=True, slots=True)
class CandidateReport:
    """Portable explanation of one candidate without retaining its entry object."""

    candidate_id: EntryId
    score: int
    maximum_score: int
    confidence_basis_points: int
    required_rule_ids: tuple[str, ...]
    required_rules_satisfied: bool
    reason_code: str
    evidence: tuple[RuleEvidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, EntryId):
            raise ReconciliationReportError(
                code="report.candidate_id_type",
                path="$.candidate.candidate_id",
                message="candidate_id must be an EntryId",
            )
        _require_count(self.maximum_score, path="$.candidate.maximum_score", label="maximum score")
        if not 1 <= self.maximum_score <= MAX_TOTAL_SCORE:
            raise ReconciliationReportError(
                code="report.maximum_score_range",
                path="$.candidate.maximum_score",
                message=f"maximum score must be between 1 and {MAX_TOTAL_SCORE}",
            )
        _require_count(self.score, path="$.candidate.score", label="score")
        if self.score > self.maximum_score:
            raise ReconciliationReportError(
                code="report.score_range",
                path="$.candidate.score",
                message="score must not exceed maximum score",
            )
        _require_count(
            self.confidence_basis_points,
            path="$.candidate.confidence_basis_points",
            label="confidence basis points",
        )
        expected_confidence = (self.score * BASIS_POINT_SCALE) // self.maximum_score
        if self.confidence_basis_points != expected_confidence:
            raise ReconciliationReportError(
                code="report.confidence_mismatch",
                path="$.candidate.confidence_basis_points",
                message="confidence basis points do not match score and maximum score",
            )
        if not isinstance(self.required_rules_satisfied, bool):
            raise ReconciliationReportError(
                code="report.required_status_type",
                path="$.candidate.required_rules_satisfied",
                message="required rule status must be a boolean",
            )
        validate_rule_code(
            self.reason_code,
            path="$.candidate.reason_code",
            kind="reason_code",
        )
        if not isinstance(self.evidence, tuple) or not self.evidence:
            raise ReconciliationReportError(
                code="report.evidence_type",
                path="$.candidate.evidence",
                message="evidence must be a non-empty tuple",
            )
        if not all(isinstance(item, RuleEvidence) for item in self.evidence):
            raise ReconciliationReportError(
                code="report.evidence_item_type",
                path="$.candidate.evidence",
                message="evidence must contain only RuleEvidence values",
            )
        rule_ids = tuple(item.rule_id for item in self.evidence)
        if len(rule_ids) != len(set(rule_ids)):
            raise ReconciliationReportError(
                code="report.evidence_duplicate",
                path="$.candidate.evidence",
                message="evidence rule identifiers must be unique",
            )
        if not isinstance(self.required_rule_ids, tuple) or not all(
            isinstance(item, str) for item in self.required_rule_ids
        ):
            raise ReconciliationReportError(
                code="report.required_rule_ids_type",
                path="$.candidate.required_rule_ids",
                message="required_rule_ids must be a tuple of strings",
            )
        if len(self.required_rule_ids) != len(set(self.required_rule_ids)):
            raise ReconciliationReportError(
                code="report.required_rule_id_duplicate",
                path="$.candidate.required_rule_ids",
                message="required rule identifiers must be unique",
            )
        if any(item not in rule_ids for item in self.required_rule_ids):
            raise ReconciliationReportError(
                code="report.required_rule_unknown",
                path="$.candidate.required_rule_ids",
                message="every required rule must occur in evidence",
            )
        expected_required_ids = tuple(item for item in rule_ids if item in self.required_rule_ids)
        if self.required_rule_ids != expected_required_ids:
            raise ReconciliationReportError(
                code="report.required_rule_order",
                path="$.candidate.required_rule_ids",
                message="required rule identifiers must preserve evidence order",
            )
        expected_status = not (
            self.rejected_required_rule_ids or self.indeterminate_required_rule_ids
        )
        if self.required_rules_satisfied is not expected_status:
            raise ReconciliationReportError(
                code="report.required_status_mismatch",
                path="$.candidate.required_rules_satisfied",
                message="required rule status does not match evidence",
            )
        if self.rejected_required_rule_ids:
            expected_reason = "score.required_rule_rejected"
        elif self.indeterminate_required_rule_ids:
            expected_reason = "score.required_rule_indeterminate"
        else:
            expected_reason = "score.rules_evaluated"
        if self.reason_code != expected_reason:
            raise ReconciliationReportError(
                code="report.score_reason_mismatch",
                path="$.candidate.reason_code",
                message="score reason does not match required rule evidence",
            )

    @classmethod
    def from_score(cls, score: CandidateScore) -> CandidateReport:
        if not isinstance(score, CandidateScore):
            raise ReconciliationReportError(
                code="report.score_type",
                path="$.score",
                message="score must be a CandidateScore",
            )
        return cls(
            score.candidate.entry_id,
            score.score,
            score.maximum_score,
            score.confidence_basis_points,
            score.required_rule_ids,
            score.required_rules_satisfied,
            score.reason_code,
            score.evidence,
        )

    @property
    def rejected_required_rule_ids(self) -> tuple[str, ...]:
        return tuple(
            item.rule_id
            for item in self.evidence
            if item.rule_id in self.required_rule_ids and item.verdict is MatchVerdict.NO_MATCH
        )

    @property
    def indeterminate_required_rule_ids(self) -> tuple[str, ...]:
        return tuple(
            item.rule_id
            for item in self.evidence
            if item.rule_id in self.required_rule_ids and item.verdict is MatchVerdict.INDETERMINATE
        )

    @property
    def reason_codes(self) -> tuple[str, ...]:
        """Return score and nested rule reasons in deterministic traversal order."""

        codes = [self.reason_code]
        codes.extend(item.reason_code for evidence in self.evidence for item in evidence.walk())
        return tuple(dict.fromkeys(codes))


@dataclass(frozen=True, slots=True)
class SelectionReport:
    """Privacy-safe candidate counts retained for one anchor selection."""

    indexed: int
    account: int
    amount: int
    time: int
    reference: int
    excluded_batch_anchors: int
    evaluated: int

    def __post_init__(self) -> None:
        stage_counts = (
            self.indexed,
            self.account,
            self.amount,
            self.time,
            self.reference,
        )
        for name, value in zip(
            ("indexed", "account", "amount", "time", "reference"),
            stage_counts,
            strict=True,
        ):
            _require_count(value, path=f"$.selection.{name}", label=name)
        if any(left < right for left, right in pairwise(stage_counts)):
            raise ReconciliationReportError(
                code="report.selection_order",
                path="$.selection",
                message="selection stage counts must not increase after a filter",
            )
        _require_count(
            self.excluded_batch_anchors,
            path="$.selection.excluded_batch_anchors",
            label="excluded batch anchors",
        )
        _require_count(
            self.evaluated,
            path="$.selection.evaluated",
            label="evaluated candidates",
        )
        if self.excluded_batch_anchors + self.evaluated != self.reference:
            raise ReconciliationReportError(
                code="report.selection_count_mismatch",
                path="$.selection",
                message="excluded and evaluated counts must partition selected candidates",
            )

    @classmethod
    def from_batch_trace(cls, trace: BatchSelectionTrace) -> SelectionReport:
        if not isinstance(trace, BatchSelectionTrace):
            raise ReconciliationReportError(
                code="report.selection_trace_type",
                path="$.selection_trace",
                message="trace must be a BatchSelectionTrace",
            )
        return cls(
            indexed=trace.index_trace.indexed,
            account=trace.index_trace.account,
            amount=trace.index_trace.amount,
            time=trace.index_trace.time,
            reference=trace.index_trace.reference,
            excluded_batch_anchors=trace.excluded_batch_anchors,
            evaluated=trace.scored_candidates,
        )


@dataclass(frozen=True, slots=True)
class DecisionReport:
    """Explain one final anchor status and every candidate considered for it."""

    anchor_id: EntryId
    status: DecisionStatus
    reason_code: str
    selected_candidate_id: EntryId | None
    contender_ids: tuple[EntryId, ...]
    competing_anchor_ids: tuple[EntryId, ...]
    candidates: tuple[CandidateReport, ...]
    selection_report: SelectionReport | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.anchor_id, EntryId):
            raise ReconciliationReportError(
                code="report.anchor_id_type",
                path="$.decision.anchor_id",
                message="anchor_id must be an EntryId",
            )
        if not isinstance(self.status, DecisionStatus):
            raise ReconciliationReportError(
                code="report.status_type",
                path="$.decision.status",
                message="status must be a DecisionStatus",
            )
        validate_rule_code(
            self.reason_code,
            path="$.decision.reason_code",
            kind="reason_code",
        )
        if self.selected_candidate_id is not None and not isinstance(
            self.selected_candidate_id, EntryId
        ):
            raise ReconciliationReportError(
                code="report.selected_id_type",
                path="$.decision.selected_candidate_id",
                message="selected_candidate_id must be an EntryId or None",
            )
        if not isinstance(self.contender_ids, tuple) or not all(
            isinstance(item, EntryId) for item in self.contender_ids
        ):
            raise ReconciliationReportError(
                code="report.contender_ids_type",
                path="$.decision.contender_ids",
                message="contender_ids must be a tuple of EntryId values",
            )
        if len(self.contender_ids) != len(set(self.contender_ids)):
            raise ReconciliationReportError(
                code="report.contender_duplicate",
                path="$.decision.contender_ids",
                message="contender identifiers must be unique",
            )
        if not isinstance(self.competing_anchor_ids, tuple) or not all(
            isinstance(item, EntryId) for item in self.competing_anchor_ids
        ):
            raise ReconciliationReportError(
                code="report.competing_anchor_ids_type",
                path="$.decision.competing_anchor_ids",
                message="competing_anchor_ids must be a tuple of EntryId values",
            )
        if len(self.competing_anchor_ids) != len(set(self.competing_anchor_ids)):
            raise ReconciliationReportError(
                code="report.competing_anchor_duplicate",
                path="$.decision.competing_anchor_ids",
                message="competing anchor identifiers must be unique",
            )
        if self.anchor_id in self.competing_anchor_ids:
            raise ReconciliationReportError(
                code="report.competing_anchor_self",
                path="$.decision.competing_anchor_ids",
                message="an anchor must not compete with itself",
            )
        if self.competing_anchor_ids and self.status not in {
            DecisionStatus.MATCHED,
            DecisionStatus.CONFLICT,
        }:
            raise ReconciliationReportError(
                code="report.competing_anchor_status",
                path="$.decision.competing_anchor_ids",
                message="only matched or conflict reports may name competing anchors",
            )
        if not isinstance(self.candidates, tuple) or not all(
            isinstance(item, CandidateReport) for item in self.candidates
        ):
            raise ReconciliationReportError(
                code="report.candidates_type",
                path="$.decision.candidates",
                message="candidates must be a tuple of CandidateReport values",
            )
        if self.selection_report is not None and not isinstance(
            self.selection_report, SelectionReport
        ):
            raise ReconciliationReportError(
                code="report.selection_type",
                path="$.decision.selection_report",
                message="selection_report must be a SelectionReport or None",
            )
        if self.selection_report is not None and self.selection_report.evaluated != len(
            self.candidates
        ):
            raise ReconciliationReportError(
                code="report.selection_evaluated_mismatch",
                path="$.decision.selection_report.evaluated",
                message="evaluated count must match candidate reports",
            )
        candidate_ids = tuple(item.candidate_id for item in self.candidates)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ReconciliationReportError(
                code="report.candidate_duplicate",
                path="$.decision.candidates",
                message="candidate identifiers must be unique",
            )
        if any(item not in candidate_ids for item in self.contender_ids):
            raise ReconciliationReportError(
                code="report.contender_unknown",
                path="$.decision.contender_ids",
                message="every contender must occur in candidates",
            )
        expected_contenders = tuple(item for item in candidate_ids if item in self.contender_ids)
        if self.contender_ids != expected_contenders:
            raise ReconciliationReportError(
                code="report.contender_order",
                path="$.decision.contender_ids",
                message="contenders must preserve candidate ranking order",
            )
        if (
            self.selected_candidate_id is not None
            and self.selected_candidate_id not in candidate_ids
        ):
            raise ReconciliationReportError(
                code="report.selection_unknown",
                path="$.decision.selected_candidate_id",
                message="selected candidate must occur in candidates",
            )
        if any(
            left.score < right.score
            for left, right in zip(self.candidates, self.candidates[1:], strict=False)
        ):
            raise ReconciliationReportError(
                code="report.candidate_order",
                path="$.decision.candidates",
                message="candidate scores must be in descending order",
            )
        if self.status is DecisionStatus.MATCHED:
            if self.selected_candidate_id is None or self.contender_ids != (
                self.selected_candidate_id,
            ):
                raise ReconciliationReportError(
                    code="report.matched_shape",
                    path="$.decision",
                    message="matched report must select its sole contender",
                )
        elif self.selected_candidate_id is not None:
            raise ReconciliationReportError(
                code="report.unselected_shape",
                path="$.decision.selected_candidate_id",
                message="only a matched report may contain a selection",
            )
        if self.status is DecisionStatus.AMBIGUOUS and len(self.contender_ids) < 2:
            raise ReconciliationReportError(
                code="report.ambiguous_shape",
                path="$.decision.contender_ids",
                message="ambiguous report must contain at least two contenders",
            )
        if self.status is DecisionStatus.UNMATCHED and self.contender_ids:
            raise ReconciliationReportError(
                code="report.unmatched_shape",
                path="$.decision.contender_ids",
                message="unmatched report must not contain contenders",
            )
        if self.status is DecisionStatus.CONFLICT and len(self.contender_ids) != 1:
            raise ReconciliationReportError(
                code="report.conflict_shape",
                path="$.decision.contender_ids",
                message="conflict report must retain one disputed contender",
            )
        if self.status is DecisionStatus.CONFLICT and not self.competing_anchor_ids:
            raise ReconciliationReportError(
                code="report.conflict_peer_missing",
                path="$.decision.competing_anchor_ids",
                message="conflict report must name at least one competing anchor",
            )

    @classmethod
    def from_decision(
        cls,
        decision: MatchDecision,
        *,
        competing_anchor_ids: tuple[EntryId, ...] = (),
        selection_report: SelectionReport | None = None,
    ) -> DecisionReport:
        if not isinstance(decision, MatchDecision):
            raise ReconciliationReportError(
                code="report.decision_type",
                path="$.decision",
                message="decision must be a MatchDecision",
            )
        return cls(
            anchor_id=decision.anchor.entry_id,
            status=decision.status,
            reason_code=decision.reason_code,
            selected_candidate_id=(
                None if decision.selection is None else decision.selection.candidate.entry_id
            ),
            contender_ids=tuple(item.candidate.entry_id for item in decision.contenders),
            competing_anchor_ids=competing_anchor_ids,
            candidates=tuple(CandidateReport.from_score(item) for item in decision.ranking),
            selection_report=selection_report,
        )

    def candidate(self, candidate_id: EntryId) -> CandidateReport:
        """Return one candidate explanation, raising ``KeyError`` when absent."""

        if not isinstance(candidate_id, EntryId):
            raise ReconciliationReportError(
                code="report.candidate_id_type",
                path="$.candidate_id",
                message="candidate_id must be an EntryId",
            )
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise KeyError(candidate_id)

    @property
    def reason_codes(self) -> tuple[str, ...]:
        """Return decision, score, and rule reasons without duplicates."""

        codes = [self.reason_code]
        codes.extend(code for candidate in self.candidates for code in candidate.reason_codes)
        return tuple(dict.fromkeys(codes))


@dataclass(frozen=True, slots=True)
class ReconciliationSummary:
    """Exact aggregate counts for one reconciliation batch."""

    total: int
    matched: int
    unmatched: int
    ambiguous: int
    conflict: int
    evaluated_candidates: int
    coverage_basis_points: int

    def __post_init__(self) -> None:
        for field_name in (
            "total",
            "matched",
            "unmatched",
            "ambiguous",
            "conflict",
            "evaluated_candidates",
            "coverage_basis_points",
        ):
            _require_count(
                getattr(self, field_name),
                path=f"$.summary.{field_name}",
                label=field_name.replace("_", " "),
            )
        if self.total < 1:
            raise ReconciliationReportError(
                code="report.summary_empty",
                path="$.summary.total",
                message="summary total must be at least one",
            )
        if self.matched + self.unmatched + self.ambiguous + self.conflict != self.total:
            raise ReconciliationReportError(
                code="report.summary_count_mismatch",
                path="$.summary",
                message="status counts must sum to total",
            )
        expected_coverage = (self.matched * BASIS_POINT_SCALE) // self.total
        if self.coverage_basis_points != expected_coverage:
            raise ReconciliationReportError(
                code="report.summary_coverage_mismatch",
                path="$.summary.coverage_basis_points",
                message="coverage basis points do not match matched and total counts",
            )

    @classmethod
    def from_decisions(cls, decisions: tuple[MatchDecision, ...]) -> ReconciliationSummary:
        total = len(decisions)
        matched = sum(item.status is DecisionStatus.MATCHED for item in decisions)
        return cls(
            total=total,
            matched=matched,
            unmatched=sum(item.status is DecisionStatus.UNMATCHED for item in decisions),
            ambiguous=sum(item.status is DecisionStatus.AMBIGUOUS for item in decisions),
            conflict=sum(item.status is DecisionStatus.CONFLICT for item in decisions),
            evaluated_candidates=sum(len(item.ranking) for item in decisions),
            coverage_basis_points=(matched * 10_000) // total,
        )


@dataclass(frozen=True, slots=True, order=True)
class ReasonCount:
    """Count one stable final decision reason within a report."""

    reason_code: str
    count: int

    def __post_init__(self) -> None:
        validate_rule_code(
            self.reason_code,
            path="$.reason_count.reason_code",
            kind="reason_code",
        )
        _require_count(self.count, path="$.reason_count.count", label="reason count")
        if self.count < 1:
            raise ReconciliationReportError(
                code="report.reason_count_empty",
                path="$.reason_count.count",
                message="reason count must be at least one",
            )


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """A stable summary and ordered explanation snapshot for one batch."""

    batch_id: BatchId
    summary: ReconciliationSummary
    decisions: tuple[DecisionReport, ...]
    policy_snapshot: ReconciliationPolicySnapshot | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.batch_id, BatchId):
            raise ReconciliationReportError(
                code="report.batch_id_type",
                path="$.report.batch_id",
                message="batch_id must be a BatchId",
            )
        if not isinstance(self.summary, ReconciliationSummary):
            raise ReconciliationReportError(
                code="report.summary_type",
                path="$.report.summary",
                message="summary must be a ReconciliationSummary",
            )
        if not isinstance(self.decisions, tuple) or not self.decisions:
            raise ReconciliationReportError(
                code="report.decisions_type",
                path="$.report.decisions",
                message="decisions must be a non-empty tuple",
            )
        if self.policy_snapshot is not None and not isinstance(
            self.policy_snapshot, ReconciliationPolicySnapshot
        ):
            raise ReconciliationReportError(
                code="report.policy_snapshot_type",
                path="$.report.policy_snapshot",
                message="policy_snapshot must be a ReconciliationPolicySnapshot or None",
            )
        if not all(isinstance(item, DecisionReport) for item in self.decisions):
            raise ReconciliationReportError(
                code="report.decision_item_type",
                path="$.report.decisions",
                message="decisions must contain only DecisionReport values",
            )
        anchor_ids = tuple(item.anchor_id for item in self.decisions)
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ReconciliationReportError(
                code="report.anchor_duplicate",
                path="$.report.decisions",
                message="anchor identifiers must be unique",
            )
        actual_counts = {
            status: sum(item.status is status for item in self.decisions)
            for status in DecisionStatus
        }
        if (
            self.summary.total != len(self.decisions)
            or self.summary.matched != actual_counts[DecisionStatus.MATCHED]
            or self.summary.unmatched != actual_counts[DecisionStatus.UNMATCHED]
            or self.summary.ambiguous != actual_counts[DecisionStatus.AMBIGUOUS]
            or self.summary.conflict != actual_counts[DecisionStatus.CONFLICT]
            or self.summary.evaluated_candidates
            != sum(len(item.candidates) for item in self.decisions)
        ):
            raise ReconciliationReportError(
                code="report.summary_mismatch",
                path="$.report.summary",
                message="summary does not match report decisions",
            )
        selected_ids = tuple(
            item.selected_candidate_id
            for item in self.decisions
            if item.selected_candidate_id is not None
        )
        if len(selected_ids) != len(set(selected_ids)):
            raise ReconciliationReportError(
                code="report.selection_duplicate",
                path="$.report.decisions",
                message="matched reports must not reuse a candidate",
            )
        self._require_policy_evidence()

    def _require_policy_evidence(self) -> None:
        policy = self.policy_snapshot
        if policy is None:
            return
        rule_ids = tuple(rule.rule_id for rule in policy.scoring_rules)
        weights = {rule.rule_id: rule.weight for rule in policy.scoring_rules}
        for decision_index, decision in enumerate(self.decisions):
            for candidate_index, candidate in enumerate(decision.candidates):
                path = f"$.report.decisions[{decision_index}].candidates[{candidate_index}]"
                if tuple(item.rule_id for item in candidate.evidence) != rule_ids:
                    raise ReconciliationReportError(
                        code="report.policy_rule_mismatch",
                        path=f"{path}.evidence",
                        message="candidate evidence must match policy rule identities and order",
                    )
                if candidate.required_rule_ids != policy.required_rule_ids:
                    raise ReconciliationReportError(
                        code="report.policy_required_mismatch",
                        path=f"{path}.required_rule_ids",
                        message="candidate required rules must match the retained policy",
                    )
                if candidate.maximum_score != policy.maximum_score:
                    raise ReconciliationReportError(
                        code="report.policy_maximum_mismatch",
                        path=f"{path}.maximum_score",
                        message="candidate maximum score must match the sum of policy weights",
                    )
                expected_score = sum(
                    weights[item.rule_id]
                    for item in candidate.evidence
                    if item.verdict is MatchVerdict.MATCH
                )
                if candidate.score != expected_score:
                    raise ReconciliationReportError(
                        code="report.policy_score_mismatch",
                        path=f"{path}.score",
                        message="candidate score must match policy weights and recorded verdicts",
                    )

    def decision_for(self, anchor_id: EntryId) -> DecisionReport:
        if not isinstance(anchor_id, EntryId):
            raise ReconciliationReportError(
                code="report.anchor_id_type",
                path="$.anchor_id",
                message="anchor_id must be an EntryId",
            )
        for decision in self.decisions:
            if decision.anchor_id == anchor_id:
                return decision
        raise KeyError(anchor_id)

    def decisions_with_status(
        self,
        status: DecisionStatus,
    ) -> tuple[DecisionReport, ...]:
        """Select decisions with one typed final status in report order."""

        if not isinstance(status, DecisionStatus):
            raise ReconciliationReportError(
                code="report.status_filter_type",
                path="$.status",
                message="status must be a DecisionStatus",
            )
        return tuple(item for item in self.decisions if item.status is status)

    def decisions_with_reason(self, reason_code: str) -> tuple[DecisionReport, ...]:
        """Select decisions with one exact final reason in report order."""

        try:
            validate_rule_code(
                reason_code,
                path="$.reason_code",
                kind="reason_code",
            )
        except (TypeError, ValueError) as error:
            raise ReconciliationReportError(
                code="report.reason_filter_format",
                path="$.reason_code",
                message="reason_code must be a dotted lowercase identifier",
            ) from error
        return tuple(item for item in self.decisions if item.reason_code == reason_code)

    @property
    def decision_reason_counts(self) -> tuple[ReasonCount, ...]:
        """Count final reasons and order them lexically for stable aggregation."""

        counts: dict[str, int] = {}
        for decision in self.decisions:
            counts[decision.reason_code] = counts.get(decision.reason_code, 0) + 1
        return tuple(ReasonCount(code, counts[code]) for code in sorted(counts))

    @property
    def matched_pairs(self) -> tuple[tuple[EntryId, EntryId], ...]:
        pairs: list[tuple[EntryId, EntryId]] = []
        for decision in self.decisions:
            if decision.selected_candidate_id is not None:
                pairs.append((decision.anchor_id, decision.selected_candidate_id))
        return tuple(pairs)

    def render_text(self) -> str:
        """Render a deterministic, concise report suitable for logs or inspection."""

        summary = self.summary
        lines = [
            f"Reconciliation batch {self.batch_id}",
            (
                "Summary: "
                f"total={summary.total} matched={summary.matched} "
                f"unmatched={summary.unmatched} ambiguous={summary.ambiguous} "
                f"conflict={summary.conflict} candidates={summary.evaluated_candidates} "
                f"coverage_bp={summary.coverage_basis_points}"
            ),
        ]
        if self.policy_snapshot is not None:
            policy = self.policy_snapshot
            rules = ",".join(
                f"{item.rule_id}:{item.weight}:{str(item.required).lower()}"
                for item in policy.scoring_rules
            )
            lines.append(
                "Policy: "
                f"minimum_score={policy.minimum_score} "
                f"minimum_margin={policy.minimum_margin} "
                f"time_window={policy.max_time_difference} "
                f"amount_tolerance={policy.max_amount_difference} "
                f"require_reference={str(policy.require_reference).lower()} "
                f"conflict_strategy={policy.conflict_strategy} rules={rules}"
            )
        for decision in self.decisions:
            selected = (
                "none"
                if decision.selected_candidate_id is None
                else str(decision.selected_candidate_id)
            )
            contenders = ",".join(str(item) for item in decision.contender_ids) or "none"
            competitors = ",".join(str(item) for item in decision.competing_anchor_ids) or "none"
            lines.append(
                f"{decision.anchor_id}: {decision.status} "
                f"reason={decision.reason_code} selected={selected} "
                f"contenders={contenders} competitors={competitors}"
            )
            if decision.selection_report is not None:
                selection = decision.selection_report
                lines.append(
                    "  Selection: "
                    f"indexed={selection.indexed} account={selection.account} "
                    f"amount={selection.amount} time={selection.time} "
                    f"reference={selection.reference} "
                    f"excluded_anchors={selection.excluded_batch_anchors} "
                    f"evaluated={selection.evaluated}"
                )
            lines.extend(
                f"  {candidate.candidate_id}: score={candidate.score}/{candidate.maximum_score} "
                f"eligible={str(candidate.required_rules_satisfied).lower()} "
                f"reason={candidate.reason_code}"
                for candidate in decision.candidates
            )
        return "\n".join(lines) + "\n"


def build_reconciliation_report(batch: ReconciliationBatch) -> ReconciliationReport:
    """Snapshot a verified batch without re-running any reconciliation logic."""

    if not isinstance(batch, ReconciliationBatch):
        raise ReconciliationReportError(
            code="report.batch_type",
            path="$.batch",
            message="batch must be a ReconciliationBatch",
        )
    claims: dict[EntryId, list[EntryId]] = {}
    for decision in batch.decisions:
        candidate_id: EntryId | None = None
        if decision.status is DecisionStatus.MATCHED:
            assert decision.selection is not None
            candidate_id = decision.selection.candidate.entry_id
        elif decision.status is DecisionStatus.CONFLICT:
            candidate_id = decision.contenders[0].candidate.entry_id
        if candidate_id is not None:
            claims.setdefault(candidate_id, []).append(decision.anchor.entry_id)

    traces = {item.anchor_id: item for item in batch.selection_traces}
    reports: list[DecisionReport] = []
    for decision in batch.decisions:
        candidate_id = None
        if decision.status is DecisionStatus.MATCHED:
            assert decision.selection is not None
            candidate_id = decision.selection.candidate.entry_id
        elif decision.status is DecisionStatus.CONFLICT:
            candidate_id = decision.contenders[0].candidate.entry_id
        competing_anchor_ids = tuple(
            anchor_id
            for anchor_id in (() if candidate_id is None else claims[candidate_id])
            if anchor_id != decision.anchor.entry_id
        )
        reports.append(
            DecisionReport.from_decision(
                decision,
                competing_anchor_ids=competing_anchor_ids,
                selection_report=(
                    None
                    if decision.anchor.entry_id not in traces
                    else SelectionReport.from_batch_trace(traces[decision.anchor.entry_id])
                ),
            )
        )

    return ReconciliationReport(
        batch.batch_id,
        ReconciliationSummary.from_decisions(batch.decisions),
        tuple(reports),
        batch.policy_snapshot,
    )
