from dataclasses import replace

import pytest

from ledgerloom.domain import (
    BatchId,
    Diagnostic,
    DiagnosticSeverity,
    LedgerBatch,
    SourceId,
    ValidationResult,
)
from ledgerloom.reconciliation import (
    CandidateIndex,
    InputReconciliationReport,
    ReconciliationRecordStatus,
    ReconciliationReportError,
    build_input_reconciliation_report,
)


def _invalid_amount() -> Diagnostic:
    return Diagnostic(
        "record.amount_invalid",
        "amount",
        "must be canonical decimal text",
    )


def _input_batch(*results) -> LedgerBatch:
    return LedgerBatch(BatchId("input-2026-08-30"), SourceId("bank-primary"), results)


def test_input_report_integrates_all_four_operational_statuses(
    make_entry,
    batch_reconciler,
):
    matched = make_entry(
        "input-match",
        amount="10",
        reference="invoice-match",
        occurred_at="2026-08-30T10:01:00Z",
    )
    unmatched = make_entry(
        "input-unmatched",
        amount="20",
        reference="invoice-missing",
        occurred_at="2026-08-30T10:02:00Z",
    )
    ambiguous = make_entry(
        "input-ambiguous",
        amount="30",
        reference=None,
        occurred_at="2026-08-30T10:03:00Z",
    )
    warning = Diagnostic(
        "record.reference_missing",
        "reference",
        "reference was not supplied",
        DiagnosticSeverity.WARNING,
    )
    input_batch = _input_batch(
        ValidationResult.success(unmatched),
        ValidationResult.failure(_invalid_amount()),
        ValidationResult.success(matched),
        ValidationResult.success(ambiguous, warning),
    )
    candidates = CandidateIndex(
        (
            make_entry(
                "candidate-match",
                source_id="processor",
                amount="10",
                reference="invoice-match",
                occurred_at="2026-08-30T10:00:30Z",
            ),
            make_entry(
                "candidate-ambiguous-a",
                source_id="processor",
                amount="30",
                reference=None,
                occurred_at="2026-08-30T10:02:30Z",
            ),
            make_entry(
                "candidate-ambiguous-b",
                source_id="processor",
                amount="30",
                reference=None,
                occurred_at="2026-08-30T10:03:30Z",
            ),
        )
    )
    reconciliation_batch = batch_reconciler.reconcile(
        BatchId("reconciliation-2026-08-30"),
        input_batch.entries,
        candidates,
    )

    report = build_input_reconciliation_report(input_batch, reconciliation_batch)

    assert report.summary.total == 4
    assert report.summary.matched == 1
    assert report.summary.unmatched == 1
    assert report.summary.ambiguous == 1
    assert report.summary.invalid == 1
    assert report.summary.coverage_basis_points == 2_500
    assert tuple(item.position for item in report.outcomes) == (1, 2, 3, 4)
    assert tuple(item.status for item in report.outcomes) == (
        ReconciliationRecordStatus.UNMATCHED,
        ReconciliationRecordStatus.INVALID,
        ReconciliationRecordStatus.MATCHED,
        ReconciliationRecordStatus.AMBIGUOUS,
    )
    assert report.outcomes[1].reason_codes == ("record.amount_invalid",)
    assert report.outcomes[3].diagnostics == (warning,)
    assert report.outcomes[3].decision is not None
    assert report.outcomes[3].decision.status.value == "ambiguous"
    assert report.policy_snapshot == reconciliation_batch.policy_snapshot


def test_conflict_is_actionable_as_ambiguous_without_losing_detail(mixed_batch):
    conflict_entries = tuple(
        decision.anchor
        for decision in mixed_batch.decisions
        if decision.status.value == "conflict"
    )
    input_batch = _input_batch(
        *(ValidationResult.success(entry) for entry in conflict_entries)
    )
    conflict_batch = replace(
        mixed_batch,
        decisions=tuple(
            decision
            for decision in mixed_batch.decisions
            if decision.status.value == "conflict"
        ),
        selection_traces=tuple(
            trace
            for trace in mixed_batch.selection_traces
            if trace.anchor_id in {entry.entry_id for entry in conflict_entries}
        ),
    )

    report = build_input_reconciliation_report(input_batch, conflict_batch)

    assert all(
        item.status is ReconciliationRecordStatus.AMBIGUOUS
        for item in report.outcomes
    )
    assert all(item.decision is not None for item in report.outcomes)
    assert {item.decision.status.value for item in report.outcomes if item.decision} == {
        "conflict"
    }
    assert all(
        item.decision.competing_anchor_ids
        for item in report.outcomes
        if item.decision
    )


def test_all_invalid_batch_produces_a_complete_report_without_reconciliation():
    input_batch = _input_batch(
        ValidationResult.failure(_invalid_amount()),
        ValidationResult.failure(
            Diagnostic("record.timestamp_invalid", "occurred_at", "must include an offset")
        ),
    )

    report = build_input_reconciliation_report(input_batch, None)

    assert report.reconciliation_report is None
    assert report.summary.invalid == 2
    assert report.summary.coverage_basis_points == 0
    assert tuple(item.reason_codes for item in report.outcomes) == (
        ("record.amount_invalid",),
        ("record.timestamp_invalid",),
    )


def test_text_report_is_deterministic_and_omits_rejected_values():
    rejected_secret = "4111111111111111"
    input_batch = _input_batch(
        ValidationResult.failure(
            Diagnostic("record.account_invalid", "account", "must be recognized")
        )
    )

    report = build_input_reconciliation_report(input_batch, None)
    rendered = report.render_text()

    assert rendered == report.render_text()
    assert "matched=0 unmatched=0 ambiguous=0 invalid=1" in rendered
    assert "record.account_invalid" in rendered
    assert "fields=account" in rendered
    assert rejected_secret not in rendered


def test_input_report_rejects_missing_decisions_for_accepted_entries(make_entry):
    input_batch = _input_batch(ValidationResult.success(make_entry("accepted")))

    with pytest.raises(ReconciliationReportError) as captured:
        build_input_reconciliation_report(input_batch, None)

    assert captured.value.code == "input_report.reconciliation_missing"


def test_input_report_rejects_decisions_for_a_different_input(
    make_entry,
    batch_reconciler,
):
    accepted = make_entry("accepted", amount="10")
    other = make_entry("other", amount="10")
    candidate = make_entry("candidate", source_id="processor", amount="10")
    input_batch = _input_batch(ValidationResult.success(accepted))
    reconciliation_batch = batch_reconciler.reconcile(
        BatchId("wrong-input"),
        (other,),
        CandidateIndex((candidate,)),
    )

    with pytest.raises(ReconciliationReportError) as captured:
        build_input_reconciliation_report(input_batch, reconciliation_batch)

    assert captured.value.code == "input_report.anchor_set_mismatch"


def test_report_constructor_rechecks_summary(make_entry, batch_reconciler):
    accepted = make_entry("accepted", amount="10")
    candidate = make_entry("candidate", source_id="processor", amount="10")
    input_batch = _input_batch(ValidationResult.success(accepted))
    reconciliation_batch = batch_reconciler.reconcile(
        BatchId("summary-check"),
        input_batch.entries,
        CandidateIndex((candidate,)),
    )
    report = build_input_reconciliation_report(input_batch, reconciliation_batch)

    with pytest.raises(ReconciliationReportError) as captured:
        InputReconciliationReport(
            report.input_batch_id,
            report.source_id,
            replace(
                report.summary,
                evaluated_candidates=report.summary.evaluated_candidates + 1,
            ),
            report.outcomes,
            report.reconciliation_report,
        )

    assert captured.value.code == "input_report.summary_mismatch"


@pytest.mark.parametrize("value", [None, "matched", object()])
def test_status_filter_requires_typed_status(value, make_entry, batch_reconciler):
    accepted = make_entry("accepted", amount="10")
    candidate = make_entry("candidate", source_id="processor", amount="10")
    input_batch = _input_batch(ValidationResult.success(accepted))
    reconciliation_batch = batch_reconciler.reconcile(
        BatchId("filter-check"),
        input_batch.entries,
        CandidateIndex((candidate,)),
    )
    report = build_input_reconciliation_report(input_batch, reconciliation_batch)

    with pytest.raises(ReconciliationReportError) as captured:
        report.outcomes_with_status(value)  # type: ignore[arg-type]

    assert captured.value.code == "input_report.status_filter_type"
