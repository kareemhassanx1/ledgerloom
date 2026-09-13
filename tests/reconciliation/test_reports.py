from dataclasses import FrozenInstanceError, replace

import pytest

from ledgerloom.domain.identifiers import BatchId, EntryId
from ledgerloom.reconciliation import (
    CandidateReport,
    DecisionReport,
    DecisionStatus,
    ReasonCount,
    ReconciliationReport,
    ReconciliationReportError,
    ReconciliationSummary,
    build_reconciliation_report,
)


def test_report_summarizes_every_batch_outcome(mixed_batch):
    report = build_reconciliation_report(mixed_batch)

    assert report.batch_id == BatchId("reconcile-2026-08-30")
    assert report.summary == ReconciliationSummary(
        total=5,
        matched=1,
        unmatched=1,
        ambiguous=1,
        conflict=2,
        evaluated_candidates=5,
        coverage_basis_points=2_000,
    )
    assert report.matched_pairs == ((EntryId("anchor-match"), EntryId("candidate-match")),)
    assert report.decision_reason_counts == (
        ReasonCount("conflict.shared_candidate", 2),
        ReasonCount("decision.insufficient_margin", 1),
        ReasonCount("decision.no_candidates", 1),
        ReasonCount("decision.unique_leader", 1),
    )
    assert report.policy_snapshot == mixed_batch.policy_snapshot
    assert all(item.selection_report is not None for item in report.decisions)
    assert report.decision_for(EntryId("anchor-match")).selection_report.evaluated == 1


def test_report_filters_final_status_and_reason_without_reordering(mixed_batch):
    report = build_reconciliation_report(mixed_batch)

    conflicts = report.decisions_with_status(DecisionStatus.CONFLICT)
    missing = report.decisions_with_reason("decision.no_candidates")

    assert tuple(item.anchor_id for item in conflicts) == (
        EntryId("anchor-conflict-a"),
        EntryId("anchor-conflict-b"),
    )
    assert tuple(item.anchor_id for item in missing) == (EntryId("anchor-unmatched"),)


def test_report_filters_reject_untyped_status_and_invalid_reason(mixed_batch):
    report = build_reconciliation_report(mixed_batch)

    with pytest.raises(ReconciliationReportError) as status_error:
        report.decisions_with_status("conflict")  # type: ignore[arg-type]
    with pytest.raises(ReconciliationReportError) as reason_error:
        report.decisions_with_reason("NOT_VALID")

    assert status_error.value.code == "report.status_filter_type"
    assert reason_error.value.code == "report.reason_filter_format"


def test_decision_report_retains_ranked_scores_contenders_and_selection(mixed_batch):
    report = build_reconciliation_report(mixed_batch)
    matched = report.decision_for(EntryId("anchor-match"))
    ambiguous = report.decision_for(EntryId("anchor-ambiguous"))
    conflict = report.decision_for(EntryId("anchor-conflict-a"))

    assert matched.status is DecisionStatus.MATCHED
    assert matched.selected_candidate_id == EntryId("candidate-match")
    assert matched.contender_ids == (EntryId("candidate-match"),)
    assert ambiguous.status is DecisionStatus.AMBIGUOUS
    assert ambiguous.contender_ids == (
        EntryId("candidate-ambiguous-a"),
        EntryId("candidate-ambiguous-b"),
    )
    assert conflict.status is DecisionStatus.CONFLICT
    assert conflict.selected_candidate_id is None
    assert conflict.contender_ids == (EntryId("candidate-shared"),)
    assert conflict.competing_anchor_ids == (EntryId("anchor-conflict-b"),)
    assert report.decision_for(EntryId("anchor-conflict-b")).competing_anchor_ids == (
        EntryId("anchor-conflict-a"),
    )


def test_report_links_a_preferred_conflict_winner_to_the_displaced_anchor(
    preferred_conflict_batch,
):
    report = build_reconciliation_report(preferred_conflict_batch)
    winner = report.decision_for(EntryId("anchor-strong"))
    displaced = report.decision_for(EntryId("anchor-weak"))

    assert winner.status is DecisionStatus.MATCHED
    assert winner.competing_anchor_ids == (EntryId("anchor-weak"),)
    assert displaced.status is DecisionStatus.CONFLICT
    assert displaced.competing_anchor_ids == (EntryId("anchor-strong"),)


def test_candidate_report_preserves_structured_rule_explanations(mixed_batch):
    report = build_reconciliation_report(mixed_batch)
    decision = report.decision_for(EntryId("anchor-match"))
    candidate = decision.candidate(EntryId("candidate-match"))

    assert candidate.score == 100
    assert candidate.maximum_score == 100
    assert candidate.confidence_basis_points == 10_000
    assert candidate.required_rules_satisfied
    assert candidate.required_rule_ids == ("ledgerloom.same_account",)
    assert candidate.rejected_required_rule_ids == ()
    assert candidate.indeterminate_required_rule_ids == ()
    assert tuple(item.rule_id for item in candidate.evidence) == (
        "ledgerloom.same_account",
        "ledgerloom.amount",
        "ledgerloom.reference",
    )
    assert candidate.reason_codes == (
        "score.rules_evaluated",
        "account.same",
        "amount.within_tolerance",
        "reference.same",
    )
    assert decision.reason_codes[0] == "decision.unique_leader"


def test_report_lookup_requires_typed_present_identifiers(mixed_batch):
    report = build_reconciliation_report(mixed_batch)
    decision = report.decision_for(EntryId("anchor-match"))

    with pytest.raises(KeyError):
        report.decision_for(EntryId("missing"))
    with pytest.raises(KeyError):
        decision.candidate(EntryId("missing"))
    with pytest.raises(ReconciliationReportError) as anchor_error:
        report.decision_for("anchor-match")  # type: ignore[arg-type]
    with pytest.raises(ReconciliationReportError) as candidate_error:
        decision.candidate("candidate-match")  # type: ignore[arg-type]

    assert anchor_error.value.code == "report.anchor_id_type"
    assert candidate_error.value.code == "report.candidate_id_type"


def test_text_render_is_deterministic_and_exposes_no_reference_values(mixed_batch):
    report = build_reconciliation_report(mixed_batch)

    first = report.render_text()
    second = build_reconciliation_report(mixed_batch).render_text()

    assert second == first
    assert first.startswith(
        "Reconciliation batch reconcile-2026-08-30\n"
        "Summary: total=5 matched=1 unmatched=1 ambiguous=1 conflict=2 "
        "candidates=5 coverage_bp=2000\n"
        "Policy: minimum_score=60 minimum_margin=10 "
    )
    assert (
        "anchor-match: matched reason=decision.unique_leader "
        "selected=candidate-match contenders=candidate-match competitors=none"
    ) in first
    assert "invoice-match" not in first
    assert "Selection: indexed=" in first


def test_report_build_does_not_change_or_recompute_batch(mixed_batch):
    before = mixed_batch

    first = build_reconciliation_report(mixed_batch)
    second = build_reconciliation_report(mixed_batch)

    assert mixed_batch == before
    assert second == first


def test_candidate_report_rejects_inconsistent_confidence(mixed_batch):
    candidate = build_reconciliation_report(mixed_batch).decisions[0].candidates[0]

    with pytest.raises(ReconciliationReportError) as captured:
        replace(candidate, confidence_basis_points=9_999)

    assert captured.value.code == "report.confidence_mismatch"


def test_candidate_report_rejects_unknown_required_rule(mixed_batch):
    candidate = build_reconciliation_report(mixed_batch).decisions[0].candidates[0]

    with pytest.raises(ReconciliationReportError) as captured:
        replace(candidate, required_rule_ids=("unknown.rule",))

    assert captured.value.code == "report.required_rule_unknown"


def test_decision_report_rejects_selection_outside_candidates(mixed_batch):
    decision = build_reconciliation_report(mixed_batch).decisions[0]

    with pytest.raises(ReconciliationReportError) as captured:
        replace(decision, selected_candidate_id=EntryId("unknown"))

    assert captured.value.code == "report.selection_unknown"


def test_decision_report_rejects_self_as_competing_anchor(mixed_batch):
    decision = build_reconciliation_report(mixed_batch).decision_for(EntryId("anchor-conflict-a"))

    with pytest.raises(ReconciliationReportError) as captured:
        replace(decision, competing_anchor_ids=(decision.anchor_id,))

    assert captured.value.code == "report.competing_anchor_self"


def test_summary_rejects_counts_that_do_not_sum_to_total():
    with pytest.raises(ReconciliationReportError) as captured:
        ReconciliationSummary(5, 1, 1, 1, 1, 0, 2_000)

    assert captured.value.code == "report.summary_count_mismatch"


@pytest.mark.parametrize("count", [0, -1, True, 1.5])
def test_reason_count_requires_a_positive_integer(count):
    with pytest.raises(ReconciliationReportError):
        ReasonCount("decision.example", count)  # type: ignore[arg-type]


def test_report_rejects_a_summary_inconsistent_with_decisions(mixed_batch):
    report = build_reconciliation_report(mixed_batch)
    changed_summary = replace(
        report.summary,
        matched=2,
        conflict=1,
        coverage_basis_points=4_000,
    )

    with pytest.raises(ReconciliationReportError) as captured:
        replace(report, summary=changed_summary)

    assert captured.value.code == "report.summary_mismatch"


@pytest.mark.parametrize("value", [object(), None, "batch"])
def test_report_builder_rejects_non_batch_values(value):
    with pytest.raises(ReconciliationReportError) as captured:
        build_reconciliation_report(value)  # type: ignore[arg-type]

    assert captured.value.code == "report.batch_type"


def test_report_models_are_immutable(mixed_batch):
    report = build_reconciliation_report(mixed_batch)
    decision: DecisionReport = report.decisions[0]
    candidate: CandidateReport = decision.candidates[0]

    with pytest.raises(FrozenInstanceError):
        report.batch_id = BatchId("changed")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.status = DecisionStatus.UNMATCHED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        candidate.score = 0  # type: ignore[misc]


def test_report_constructor_rejects_duplicate_selected_candidate(mixed_batch):
    report = build_reconciliation_report(mixed_batch)
    matched = report.decision_for(EntryId("anchor-match"))
    duplicate = replace(matched, anchor_id=EntryId("another-anchor"))
    summary = ReconciliationSummary(2, 2, 0, 0, 0, 2, 10_000)

    with pytest.raises(ReconciliationReportError) as captured:
        ReconciliationReport(BatchId("duplicate-selection"), summary, (matched, duplicate))

    assert captured.value.code == "report.selection_duplicate"
