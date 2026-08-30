"""Input-aware reports joining validation and reconciliation outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ledgerloom.domain import (
    BatchId,
    Diagnostic,
    DiagnosticSeverity,
    EntryId,
    LedgerBatch,
    SourceId,
)
from ledgerloom.reconciliation.batches import (
    ReconciliationBatch,
    ReconciliationPolicySnapshot,
)
from ledgerloom.reconciliation.decisions import DecisionStatus
from ledgerloom.reconciliation.errors import ReconciliationReportError
from ledgerloom.reconciliation.reports import (
    DecisionReport,
    ReconciliationReport,
    build_reconciliation_report,
)
from ledgerloom.reconciliation.scoring import BASIS_POINT_SCALE


def _require_count(value: object, *, path: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReconciliationReportError(
            code="input_report.count_type",
            path=path,
            message=f"{label} must be an integer",
        )
    if value < 0:
        raise ReconciliationReportError(
            code="input_report.count_range",
            path=path,
            message=f"{label} must not be negative",
        )
    return value


class ReconciliationRecordStatus(StrEnum):
    """Operational status for one original input record."""

    MATCHED = "matched"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"


def _record_status(status: DecisionStatus) -> ReconciliationRecordStatus:
    if status is DecisionStatus.MATCHED:
        return ReconciliationRecordStatus.MATCHED
    if status is DecisionStatus.UNMATCHED:
        return ReconciliationRecordStatus.UNMATCHED
    return ReconciliationRecordStatus.AMBIGUOUS


@dataclass(frozen=True, slots=True)
class InputOutcomeReport:
    """Explain one input position without retaining its rejected value."""

    position: int
    status: ReconciliationRecordStatus
    entry_id: EntryId | None
    decision: DecisionReport | None
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        _require_count(self.position, path="$.outcome.position", label="position")
        if self.position < 1:
            raise ReconciliationReportError(
                code="input_report.position_range",
                path="$.outcome.position",
                message="position must be at least one",
            )
        if not isinstance(self.status, ReconciliationRecordStatus):
            raise ReconciliationReportError(
                code="input_report.status_type",
                path="$.outcome.status",
                message="status must be a ReconciliationRecordStatus",
            )
        if not isinstance(self.diagnostics, tuple) or not all(
            isinstance(item, Diagnostic) for item in self.diagnostics
        ):
            raise ReconciliationReportError(
                code="input_report.diagnostics_type",
                path="$.outcome.diagnostics",
                message="diagnostics must be a tuple of Diagnostic values",
            )

        errors = tuple(
            item
            for item in self.diagnostics
            if item.severity is DiagnosticSeverity.ERROR
        )
        if self.status is ReconciliationRecordStatus.INVALID:
            if self.entry_id is not None or self.decision is not None:
                raise ReconciliationReportError(
                    code="input_report.invalid_shape",
                    path="$.outcome",
                    message="an invalid outcome must not contain an entry or decision",
                )
            if not errors:
                raise ReconciliationReportError(
                    code="input_report.invalid_diagnostic_missing",
                    path="$.outcome.diagnostics",
                    message="an invalid outcome must contain at least one error diagnostic",
                )
            return

        if not isinstance(self.entry_id, EntryId):
            raise ReconciliationReportError(
                code="input_report.entry_id_type",
                path="$.outcome.entry_id",
                message="a valid outcome must contain an EntryId",
            )
        if not isinstance(self.decision, DecisionReport):
            raise ReconciliationReportError(
                code="input_report.decision_type",
                path="$.outcome.decision",
                message="a valid outcome must contain a DecisionReport",
            )
        if errors:
            raise ReconciliationReportError(
                code="input_report.valid_error_diagnostic",
                path="$.outcome.diagnostics",
                message="a valid outcome must not contain error diagnostics",
            )
        if self.entry_id != self.decision.anchor_id:
            raise ReconciliationReportError(
                code="input_report.decision_anchor_mismatch",
                path="$.outcome.decision.anchor_id",
                message="decision anchor must match the accepted entry",
            )
        if self.status is not _record_status(self.decision.status):
            raise ReconciliationReportError(
                code="input_report.status_mismatch",
                path="$.outcome.status",
                message="record status does not match the reconciliation decision",
            )

    @property
    def reason_codes(self) -> tuple[str, ...]:
        """Return stable diagnostic or reconciliation reasons in report order."""

        if self.decision is not None:
            return self.decision.reason_codes
        return tuple(dict.fromkeys(item.code for item in self.diagnostics))


@dataclass(frozen=True, slots=True)
class InputReconciliationSummary:
    """Counts for every original input, including rejected records."""

    total: int
    matched: int
    unmatched: int
    ambiguous: int
    invalid: int
    evaluated_candidates: int
    coverage_basis_points: int

    def __post_init__(self) -> None:
        for name, value in (
            ("total", self.total),
            ("matched", self.matched),
            ("unmatched", self.unmatched),
            ("ambiguous", self.ambiguous),
            ("invalid", self.invalid),
            ("evaluated_candidates", self.evaluated_candidates),
            ("coverage_basis_points", self.coverage_basis_points),
        ):
            _require_count(value, path=f"$.input_summary.{name}", label=name)
        if self.total < 1:
            raise ReconciliationReportError(
                code="input_report.summary_empty",
                path="$.input_summary.total",
                message="summary total must be at least one",
            )
        if self.matched + self.unmatched + self.ambiguous + self.invalid != self.total:
            raise ReconciliationReportError(
                code="input_report.summary_count_mismatch",
                path="$.input_summary",
                message="matched, unmatched, ambiguous, and invalid must sum to total",
            )
        expected_coverage = (self.matched * BASIS_POINT_SCALE) // self.total
        if self.coverage_basis_points != expected_coverage:
            raise ReconciliationReportError(
                code="input_report.summary_coverage_mismatch",
                path="$.input_summary.coverage_basis_points",
                message="coverage basis points do not match matched and total counts",
            )

    @classmethod
    def from_outcomes(
        cls,
        outcomes: tuple[InputOutcomeReport, ...],
    ) -> InputReconciliationSummary:
        total = len(outcomes)
        matched = sum(
            item.status is ReconciliationRecordStatus.MATCHED for item in outcomes
        )
        return cls(
            total=total,
            matched=matched,
            unmatched=sum(
                item.status is ReconciliationRecordStatus.UNMATCHED
                for item in outcomes
            ),
            ambiguous=sum(
                item.status is ReconciliationRecordStatus.AMBIGUOUS
                for item in outcomes
            ),
            invalid=sum(
                item.status is ReconciliationRecordStatus.INVALID for item in outcomes
            ),
            evaluated_candidates=sum(
                len(item.decision.candidates)
                for item in outcomes
                if item.decision is not None
            ),
            coverage_basis_points=(matched * BASIS_POINT_SCALE) // total,
        )


@dataclass(frozen=True, slots=True)
class InputReconciliationReport:
    """Join input validation and matching results in original input order."""

    input_batch_id: BatchId
    source_id: SourceId
    summary: InputReconciliationSummary
    outcomes: tuple[InputOutcomeReport, ...]
    reconciliation_report: ReconciliationReport | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.input_batch_id, BatchId):
            raise ReconciliationReportError(
                code="input_report.batch_id_type",
                path="$.input_report.input_batch_id",
                message="input_batch_id must be a BatchId",
            )
        if not isinstance(self.source_id, SourceId):
            raise ReconciliationReportError(
                code="input_report.source_id_type",
                path="$.input_report.source_id",
                message="source_id must be a SourceId",
            )
        if not isinstance(self.summary, InputReconciliationSummary):
            raise ReconciliationReportError(
                code="input_report.summary_type",
                path="$.input_report.summary",
                message="summary must be an InputReconciliationSummary",
            )
        if not isinstance(self.outcomes, tuple) or not self.outcomes or not all(
            isinstance(item, InputOutcomeReport) for item in self.outcomes
        ):
            raise ReconciliationReportError(
                code="input_report.outcomes_type",
                path="$.input_report.outcomes",
                message="outcomes must be a non-empty tuple of InputOutcomeReport values",
            )
        expected_positions = tuple(range(1, len(self.outcomes) + 1))
        if tuple(item.position for item in self.outcomes) != expected_positions:
            raise ReconciliationReportError(
                code="input_report.position_order",
                path="$.input_report.outcomes",
                message="outcomes must retain contiguous one-based input positions",
            )
        expected_summary = InputReconciliationSummary.from_outcomes(self.outcomes)
        if self.summary != expected_summary:
            raise ReconciliationReportError(
                code="input_report.summary_mismatch",
                path="$.input_report.summary",
                message="summary does not match input outcomes",
            )

        decisions = tuple(
            item.decision for item in self.outcomes if item.decision is not None
        )
        if decisions:
            if not isinstance(self.reconciliation_report, ReconciliationReport):
                raise ReconciliationReportError(
                    code="input_report.reconciliation_missing",
                    path="$.input_report.reconciliation_report",
                    message="accepted inputs require a ReconciliationReport",
                )
            decisions_by_id = {item.anchor_id: item for item in decisions}
            report_decisions_by_id = {
                item.anchor_id: item for item in self.reconciliation_report.decisions
            }
            if set(decisions_by_id) != set(report_decisions_by_id):
                raise ReconciliationReportError(
                    code="input_report.decision_set_mismatch",
                    path="$.input_report.reconciliation_report.decisions",
                    message="report decisions must cover exactly the accepted inputs",
                )
            if any(
                decision != report_decisions_by_id[anchor_id]
                for anchor_id, decision in decisions_by_id.items()
            ):
                raise ReconciliationReportError(
                    code="input_report.decision_mismatch",
                    path="$.input_report.outcomes.decision",
                    message="outcome decisions must equal the detailed report decisions",
                )
        elif self.reconciliation_report is not None:
            raise ReconciliationReportError(
                code="input_report.reconciliation_unexpected",
                path="$.input_report.reconciliation_report",
                message="an all-invalid input batch must not contain reconciliation results",
            )

    @property
    def policy_snapshot(self) -> ReconciliationPolicySnapshot | None:
        if self.reconciliation_report is None:
            return None
        return self.reconciliation_report.policy_snapshot

    def outcomes_with_status(
        self,
        status: ReconciliationRecordStatus,
    ) -> tuple[InputOutcomeReport, ...]:
        if not isinstance(status, ReconciliationRecordStatus):
            raise ReconciliationReportError(
                code="input_report.status_filter_type",
                path="$.status",
                message="status must be a ReconciliationRecordStatus",
            )
        return tuple(item for item in self.outcomes if item.status is status)

    def render_text(self) -> str:
        """Render statuses and safe reason codes without rejected source values."""

        summary = self.summary
        reconciliation_id = (
            "none"
            if self.reconciliation_report is None
            else str(self.reconciliation_report.batch_id)
        )
        lines = [
            (
                f"Input batch {self.input_batch_id} source={self.source_id} "
                f"reconciliation={reconciliation_id}"
            ),
            (
                "Summary: "
                f"total={summary.total} matched={summary.matched} "
                f"unmatched={summary.unmatched} ambiguous={summary.ambiguous} "
                f"invalid={summary.invalid} candidates={summary.evaluated_candidates} "
                f"coverage_bp={summary.coverage_basis_points}"
            ),
        ]
        for outcome in self.outcomes:
            reasons = ",".join(outcome.reason_codes)
            if outcome.status is ReconciliationRecordStatus.INVALID:
                fields = ",".join(item.field for item in outcome.diagnostics)
                lines.append(
                    f"{outcome.position}: invalid reasons={reasons} fields={fields}"
                )
                continue
            assert outcome.entry_id is not None
            assert outcome.decision is not None
            lines.append(
                f"{outcome.position}: {outcome.status} entry={outcome.entry_id} "
                f"decision={outcome.decision.status} reasons={reasons}"
            )
        return "\n".join(lines) + "\n"


def build_input_reconciliation_report(
    input_batch: LedgerBatch,
    reconciliation_batch: ReconciliationBatch | None,
) -> InputReconciliationReport:
    """Join a validated input batch to decisions for exactly its accepted entries."""

    if not isinstance(input_batch, LedgerBatch):
        raise ReconciliationReportError(
            code="input_report.input_batch_type",
            path="$.input_batch",
            message="input_batch must be a LedgerBatch",
        )
    if reconciliation_batch is not None and not isinstance(
        reconciliation_batch, ReconciliationBatch
    ):
        raise ReconciliationReportError(
            code="input_report.reconciliation_batch_type",
            path="$.reconciliation_batch",
            message="reconciliation_batch must be a ReconciliationBatch or None",
        )

    accepted_entries = input_batch.entries
    if not accepted_entries:
        if reconciliation_batch is not None:
            raise ReconciliationReportError(
                code="input_report.reconciliation_unexpected",
                path="$.reconciliation_batch",
                message="an all-invalid input batch must not have reconciliation decisions",
            )
        outcomes = tuple(
            InputOutcomeReport(
                position=position,
                status=ReconciliationRecordStatus.INVALID,
                entry_id=None,
                decision=None,
                diagnostics=result.diagnostics,
            )
            for position, result in enumerate(input_batch.results, start=1)
        )
        return InputReconciliationReport(
            input_batch.batch_id,
            input_batch.source_id,
            InputReconciliationSummary.from_outcomes(outcomes),
            outcomes,
        )

    if reconciliation_batch is None:
        raise ReconciliationReportError(
            code="input_report.reconciliation_missing",
            path="$.reconciliation_batch",
            message="accepted inputs require a reconciliation batch",
        )

    accepted_by_id = {item.entry_id: item for item in accepted_entries}
    decisions_by_id = {
        item.anchor.entry_id: item for item in reconciliation_batch.decisions
    }
    if set(accepted_by_id) != set(decisions_by_id):
        raise ReconciliationReportError(
            code="input_report.anchor_set_mismatch",
            path="$.reconciliation_batch.decisions",
            message="decisions must cover exactly the accepted input entries",
        )
    if any(
        decisions_by_id[entry_id].anchor != entry
        for entry_id, entry in accepted_by_id.items()
    ):
        raise ReconciliationReportError(
            code="input_report.anchor_content_mismatch",
            path="$.reconciliation_batch.decisions.anchor",
            message="decision anchors must equal the accepted input entries",
        )

    reconciliation_report = build_reconciliation_report(reconciliation_batch)
    reports_by_id = {
        item.anchor_id: item for item in reconciliation_report.decisions
    }
    outcomes_list: list[InputOutcomeReport] = []
    for position, result in enumerate(input_batch.results, start=1):
        if result.value is None:
            outcomes_list.append(
                InputOutcomeReport(
                    position,
                    ReconciliationRecordStatus.INVALID,
                    None,
                    None,
                    result.diagnostics,
                )
            )
            continue
        decision = reports_by_id[result.value.entry_id]
        outcomes_list.append(
            InputOutcomeReport(
                position,
                _record_status(decision.status),
                result.value.entry_id,
                decision,
                result.diagnostics,
            )
        )

    outcomes = tuple(outcomes_list)
    return InputReconciliationReport(
        input_batch.batch_id,
        input_batch.source_id,
        InputReconciliationSummary.from_outcomes(outcomes),
        outcomes,
        reconciliation_report,
    )
