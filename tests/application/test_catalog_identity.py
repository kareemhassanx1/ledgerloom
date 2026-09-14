from dataclasses import replace

import pytest

from ledgerloom.application import CatalogError, JournalCatalog
from ledgerloom.domain import BatchId, Diagnostic, EntryId, LedgerBatch, SourceId, ValidationResult
from ledgerloom.journal import JournalEntry
from ledgerloom.serialization import domain_v1_registry


def _batch(name, entry):
    return LedgerBatch(BatchId(name), entry.source_id, (ValidationResult.success(entry),))


def _journal(*batches):
    previous = None
    events = []
    for sequence, batch in enumerate(batches, 1):
        event = JournalEntry.create(sequence, domain_v1_registry().encode(batch), previous)
        events.append(event)
        previous = event.fingerprint
    return events


@pytest.mark.parametrize("kind", ["identical", "changed_amount", "reused_source_record"])
@pytest.mark.parametrize("decode", [False, True])
def test_catalog_rejects_reused_import_identity(make_entry, kind, decode):
    first = make_entry("entry-1")
    if kind == "changed_amount":
        later = make_entry("entry-1", amount="999.00")
    elif kind == "reused_source_record":
        later = replace(first, entry_id=EntryId("different-entry"))
    else:
        later = first
    batches = (_batch("first-import", first), _batch("later-import", later))

    with pytest.raises(CatalogError) as captured:
        if decode:
            JournalCatalog.from_entries(_journal(*batches))
        else:
            JournalCatalog(batches, ())

    expected = "source_record_duplicate" if kind == "reused_source_record" else "entry_duplicate"
    assert captured.value.code == f"catalog.{expected}"
    assert captured.value.path.startswith("$.batches[1].results[0].")


def test_source_record_identity_is_scoped_to_its_source(make_entry):
    bank = make_entry("bank-entry", source_id="bank")
    processor = replace(bank, entry_id=EntryId("processor-entry"), source_id=SourceId("processor"))

    catalog = JournalCatalog.from_entries(
        _journal(_batch("bank", bank), _batch("processor", processor))
    )

    assert len(catalog.batches) == 2


def test_rejected_duplicate_import_is_retained_without_claiming_identity(make_entry):
    accepted = _batch("accepted", make_entry("entry"))
    rejected = LedgerBatch(
        BatchId("rejected"),
        accepted.source_id,
        (
            ValidationResult.failure(
                Diagnostic("cli.import_duplicate", "record_id", "already imported")
            ),
        ),
    )

    catalog = JournalCatalog.from_entries(_journal(accepted, rejected))

    assert catalog.batches == (accepted, rejected)
    assert catalog.batches[1].rejected_count == 1
