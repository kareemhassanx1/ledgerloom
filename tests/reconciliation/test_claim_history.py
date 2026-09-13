import pytest

from ledgerloom.application import CatalogError, JournalCatalog
from ledgerloom.domain import BatchId
from ledgerloom.reconciliation import (
    CandidateIndex,
    ReconciliationClaimError,
    ReconciliationClaims,
    build_reconciliation_report,
)


def _report(reconciler, name, anchor, candidate):
    return build_reconciliation_report(
        reconciler.reconcile(BatchId(name), (anchor,), CandidateIndex((candidate,)))
    )


@pytest.mark.parametrize(
    ("later_anchor", "later_candidate"),
    [
        ("anchor", "candidate"),
        ("anchor", "fresh"),
        ("fresh", "candidate"),
        ("candidate", "fresh"),
        ("fresh", "anchor"),
    ],
)
def test_committed_history_rejects_reuse_in_either_role(
    make_entry,
    batch_reconciler,
    later_anchor,
    later_candidate,
):
    records = {name: make_entry(name) for name in ("anchor", "candidate", "fresh")}
    first = _report(batch_reconciler, "first", records["anchor"], records["candidate"])
    later = _report(batch_reconciler, "later", records[later_anchor], records[later_candidate])
    assert len(first.matched_pairs) == len(later.matched_pairs) == 1

    with pytest.raises(ReconciliationClaimError) as captured:
        ReconciliationClaims.from_reports(iter((first, later)))

    assert captured.value.code == "claim.history_entry_reused"
    assert captured.value.path.startswith("$.reports[1].decisions[0].")


def test_recorded_conflict_does_not_claim_the_rejected_pair(make_entry, batch_reconciler):
    anchor, candidate, later_anchor = (make_entry(name) for name in ("a", "b", "c"))
    first = _report(batch_reconciler, "first", anchor, candidate)
    claims = ReconciliationClaims.from_reports((first,))
    conflict = claims.resolve(_report(batch_reconciler, "later", later_anchor, candidate))

    assert not conflict.matched_pairs
    assert ReconciliationClaims.from_reports((first, conflict)) == claims


def test_independent_matches_remain_replayable(make_entry, batch_reconciler):
    first = _report(batch_reconciler, "first", make_entry("a"), make_entry("b"))
    second = _report(batch_reconciler, "second", make_entry("c"), make_entry("d"))

    claims = ReconciliationClaims.from_reports((first, second))

    assert len(claims.claimed_entry_ids) == 4


def test_application_catalog_refuses_double_settled_history(make_entry, batch_reconciler):
    first = _report(batch_reconciler, "first", make_entry("a"), make_entry("b"))
    later = _report(batch_reconciler, "later", make_entry("c"), make_entry("b"))

    with pytest.raises(CatalogError) as captured:
        JournalCatalog((), (first, later))

    assert captured.value.code == "catalog.claim_history"
    assert captured.value.__cause__.code == "claim.history_entry_reused"
