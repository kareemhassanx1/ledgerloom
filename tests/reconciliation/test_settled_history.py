import pytest

from ledgerloom.application import CatalogError, JournalCatalog
from ledgerloom.domain import BatchId
from ledgerloom.reconciliation import (
    CandidateIndex,
    ReconciliationClaimError,
    ReconciliationClaims,
    build_reconciliation_report,
)


def _report(reconciler, name, anchors, candidates):
    return build_reconciliation_report(
        reconciler.reconcile(BatchId(name), anchors, CandidateIndex(candidates))
    )


@pytest.mark.parametrize("previous_role", ["anchor", "candidate"])
@pytest.mark.parametrize("later_state", ["unmatched", "ambiguous", "conflict"])
def test_settled_participant_cannot_receive_a_later_nonmatched_decision(
    make_entry,
    batch_reconciler,
    previous_role,
    later_state,
):
    original = {name: make_entry(name) for name in ("anchor", "candidate")}
    first = _report(batch_reconciler, "first", (original["anchor"],), (original["candidate"],))
    reused = original[previous_role]
    fresh_a, fresh_b = make_entry("fresh-a"), make_entry("fresh-b")
    if later_state == "unmatched":
        later = _report(batch_reconciler, "later", (reused,), ())
    elif later_state == "ambiguous":
        later = _report(batch_reconciler, "later", (reused,), (fresh_a, fresh_b))
    else:
        later = _report(batch_reconciler, "later", (reused, fresh_a), (fresh_b,))
    assert later.decision_for(reused.entry_id).status.value == later_state

    with pytest.raises(ReconciliationClaimError) as captured:
        ReconciliationClaims.from_reports((first, later))

    assert captured.value.code == "claim.history_anchor_already_settled"
    assert captured.value.path.startswith("$.reports[1].decisions[")
    assert captured.value.path.endswith(".anchor_id")


def test_catalog_refuses_a_later_report_that_hides_a_committed_match(make_entry, batch_reconciler):
    anchor, candidate = make_entry("a"), make_entry("b")
    first = _report(batch_reconciler, "first", (anchor,), (candidate,))
    later = _report(batch_reconciler, "later", (anchor,), ())

    with pytest.raises(CatalogError) as captured:
        JournalCatalog((), (first, later))

    assert captured.value.code == "catalog.claim_history"
    assert captured.value.__cause__.code == "claim.history_anchor_already_settled"


def test_unmatched_anchor_can_later_be_settled(make_entry, batch_reconciler):
    anchor, candidate = make_entry("a"), make_entry("b")
    first = _report(batch_reconciler, "first", (anchor,), ())
    later = _report(batch_reconciler, "later", (anchor,), (candidate,))

    claims = ReconciliationClaims.from_reports((first, later))

    assert claims.claimed_entry_ids == {anchor.entry_id, candidate.entry_id}
