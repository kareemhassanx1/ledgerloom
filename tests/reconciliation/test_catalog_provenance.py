import pytest

from ledgerloom.application import CatalogError, JournalCatalog
from ledgerloom.domain import BatchId, LedgerBatch, ValidationResult
from ledgerloom.journal import JournalEntry, verify_journal_entries
from ledgerloom.reconciliation import (
    CandidateIndex,
    build_reconciliation_report,
    build_reconciliation_report_document,
)
from ledgerloom.serialization import domain_v1_registry
from ledgerloom.serialization.schema import SchemaDocument, SchemaRef
from ledgerloom.storage import SQLiteJournal


def _batch(name, *records):
    return domain_v1_registry().encode(
        LedgerBatch(
            BatchId(name),
            records[0].source_id,
            tuple(ValidationResult.success(record) for record in records),
        )
    )


def _report(reconciler, anchor, *candidates):
    return build_reconciliation_report_document(
        build_reconciliation_report(
            reconciler.reconcile(BatchId("review"), (anchor,), CandidateIndex(candidates))
        )
    )


def _chain(*documents):
    entries = []
    previous = None
    for sequence, document in enumerate(documents, 1):
        entry = JournalEntry.create(sequence, document, previous)
        entries.append(entry)
        previous = entry.fingerprint
    verify_journal_entries(entries)
    return tuple(entries)


@pytest.mark.parametrize("missing", ["anchor", "candidate"])
def test_valid_hash_chain_does_not_make_an_orphan_report_usable(
    make_entry,
    batch_reconciler,
    missing,
):
    anchor, candidate = make_entry("anchor"), make_entry("candidate", source_id="processor")
    imported = candidate if missing == "anchor" else anchor
    entries = _chain(_batch("imported", imported), _report(batch_reconciler, anchor, candidate))

    with pytest.raises(CatalogError) as captured:
        JournalCatalog.from_entries(entries)

    assert captured.value.code == "catalog.report_entry_missing"
    assert captured.value.path.startswith("$.entries[1].document.payload.decisions[0].")
    assert missing in captured.value.message


def test_later_import_cannot_retroactively_back_a_report(make_entry, batch_reconciler):
    anchor, candidate = make_entry("anchor"), make_entry("candidate")
    entries = _chain(
        _batch("bank", anchor),
        _report(batch_reconciler, anchor, candidate),
        _batch("late-import", candidate),
    )

    with pytest.raises(CatalogError, match="not present in preceding imported batches"):
        JournalCatalog.from_entries(entries)


def test_even_unselected_candidates_require_import_evidence(make_entry, batch_reconciler):
    anchor, first, second = (make_entry(name) for name in ("anchor", "candidate-1", "candidate-2"))
    report = _report(batch_reconciler, anchor, first, second)

    with pytest.raises(CatalogError) as captured:
        JournalCatalog.from_entries(_chain(_batch("imported", anchor, first), report))

    assert captured.value.path.endswith(".candidates[1].candidate_id")
    assert "candidate-2" in captured.value.message


def test_unmatched_report_still_requires_its_anchor(make_entry, batch_reconciler):
    report = _report(batch_reconciler, make_entry("missing-anchor"))

    with pytest.raises(CatalogError) as captured:
        JournalCatalog.from_entries(_chain(report))

    assert captured.value.path.endswith(".anchor_id")


def test_backed_reports_and_unrelated_documents_reopen_normally(
    make_entry,
    batch_reconciler,
    tmp_path,
):
    anchor, candidate = make_entry("anchor"), make_entry("candidate", source_id="processor")
    marker = SchemaDocument.build(SchemaRef.parse("vendor.marker", "1.0"), {"month": "September"})
    report = _report(batch_reconciler, anchor, candidate)
    path = tmp_path / "journal.sqlite"
    with SQLiteJournal(path) as journal:
        journal.append_many(
            (_batch("bank", anchor), marker, _batch("processor", candidate), report)
        )
    with SQLiteJournal(path) as journal:
        catalog = JournalCatalog.from_entries(journal.read())

    assert len(catalog.batches) == 2
    assert catalog.reports[0].matched_pairs == ((anchor.entry_id, candidate.entry_id),)
    assert catalog.unrecognized_entries[0].document == marker


def test_rejecting_orphan_history_does_not_repair_or_rewrite_storage(
    make_entry,
    batch_reconciler,
    tmp_path,
):
    report = _report(batch_reconciler, make_entry("anchor"), make_entry("candidate"))
    with SQLiteJournal(tmp_path / "journal.sqlite") as journal:
        journal.append(report)
        before = journal.read()
        with pytest.raises(CatalogError):
            JournalCatalog.from_entries(before)
        assert journal.read() == before
