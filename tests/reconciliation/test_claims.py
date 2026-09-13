import pytest

from ledgerloom.domain import BatchId
from ledgerloom.reconciliation import (
    CandidateIndex,
    DecisionStatus,
    ReconciliationClaimError,
    ReconciliationClaims,
    build_reconciliation_report,
)


def _report(batch_reconciler, batch_id, anchor, candidate):
    batch = batch_reconciler.reconcile(
        BatchId(batch_id),
        (anchor,),
        CandidateIndex((candidate,)),
    )
    return build_reconciliation_report(batch)


def test_committed_match_claims_both_participants(make_entry, batch_reconciler):
    anchor = make_entry("anchor")
    candidate = make_entry("candidate", source_id="processor")
    report = _report(batch_reconciler, "first", anchor, candidate)

    claims = ReconciliationClaims.from_reports((report,))

    assert claims.claimed_entry_ids == {anchor.entry_id, candidate.entry_id}
    assert claims.owners_for(anchor.entry_id) == (anchor.entry_id,)
    assert claims.owners_for(candidate.entry_id) == (anchor.entry_id,)


def test_later_selection_of_claimed_candidate_becomes_conflict(
    make_entry,
    batch_reconciler,
):
    earlier_anchor = make_entry("earlier")
    shared = make_entry("shared", source_id="processor")
    later_anchor = make_entry("later")
    first = _report(batch_reconciler, "first", earlier_anchor, shared)
    second = _report(batch_reconciler, "second", later_anchor, shared)

    resolved = ReconciliationClaims.from_reports((first,)).resolve(second)

    decision = resolved.decisions[0]
    assert decision.status is DecisionStatus.CONFLICT
    assert decision.reason_code == "conflict.candidate_already_claimed"
    assert decision.selected_candidate_id is None
    assert decision.contender_ids == (shared.entry_id,)
    assert decision.competing_anchor_ids == (earlier_anchor.entry_id,)
    assert resolved.summary.matched == 0
    assert resolved.summary.conflict == 1
    assert resolved.summary.coverage_basis_points == 0


def test_previously_matched_participant_cannot_be_reused_as_anchor(
    make_entry,
    batch_reconciler,
):
    anchor = make_entry("anchor")
    candidate = make_entry("candidate", source_id="processor")
    claims = ReconciliationClaims.from_reports(
        (_report(batch_reconciler, "first", anchor, candidate),)
    )

    with pytest.raises(ReconciliationClaimError) as captured:
        claims.require_available_anchors((candidate,))

    assert captured.value.code == "claim.anchor_already_matched"
    assert captured.value.path == "$.anchors[0].entry_id"


def test_empty_claims_leave_a_report_unchanged(make_entry, batch_reconciler):
    report = _report(
        batch_reconciler,
        "first",
        make_entry("anchor"),
        make_entry("candidate", source_id="processor"),
    )

    assert ReconciliationClaims().resolve(report) is report


def test_claims_validate_report_and_anchor_inputs():
    with pytest.raises(ReconciliationClaimError) as captured:
        ReconciliationClaims.from_reports((object(),))  # type: ignore[arg-type]
    assert captured.value.code == "claim.report_type"

    with pytest.raises(ReconciliationClaimError) as captured:
        ReconciliationClaims().require_available_anchors((object(),))  # type: ignore[arg-type]
    assert captured.value.code == "claim.anchor_type"


@pytest.mark.parametrize(
    ("anchor_name", "candidate_name"),
    [("anchor", "fresh"), ("candidate", "fresh"), ("anchor", "candidate")],
)
def test_resolve_itself_rejects_an_already_settled_anchor(
    make_entry,
    batch_reconciler,
    anchor_name,
    candidate_name,
):
    records = {name: make_entry(name) for name in ("anchor", "candidate", "fresh")}
    first = _report(batch_reconciler, "first", records["anchor"], records["candidate"])
    later = _report(batch_reconciler, "later", records[anchor_name], records[candidate_name])
    claims = ReconciliationClaims.from_reports((first,))
    before_pairs = later.matched_pairs

    with pytest.raises(ReconciliationClaimError) as captured:
        claims.resolve(later)

    assert captured.value.code == "claim.anchor_already_matched"
    assert captured.value.path == "$.report.decisions[0].anchor_id"
    assert later.matched_pairs == before_pairs
    assert claims == ReconciliationClaims.from_reports((first,))


def test_resolve_cannot_reclassify_a_committed_anchor_as_unmatched(make_entry, batch_reconciler):
    anchor, candidate = make_entry("anchor"), make_entry("candidate")
    first = _report(batch_reconciler, "first", anchor, candidate)
    later = build_reconciliation_report(
        batch_reconciler.reconcile(BatchId("later"), (anchor,), CandidateIndex(()))
    )
    assert later.summary.unmatched == 1

    with pytest.raises(ReconciliationClaimError, match="already participates"):
        ReconciliationClaims.from_reports((first,)).resolve(later)
