import pytest

from ledgerloom.application import CatalogError, JournalCatalog
from ledgerloom.domain import BatchId, LedgerBatch, SourceId, ValidationResult
from ledgerloom.journal import JournalEntry
from ledgerloom.serialization import LEDGER_BATCH_V1, domain_v1_registry
from ledgerloom.serialization.schema import SchemaDocument, SchemaRef


def _batch(make_entry, batch_id: str, entry_id: str = "entry-1") -> LedgerBatch:
    entry = make_entry(entry_id, source_id="bank")
    return LedgerBatch(
        BatchId(batch_id),
        SourceId("bank"),
        (ValidationResult.success(entry),),
    )


def _chain(*documents: SchemaDocument) -> tuple[JournalEntry, ...]:
    entries = []
    previous = None
    for sequence, document in enumerate(documents, start=1):
        entry = JournalEntry.create(sequence, document, previous)
        entries.append(entry)
        previous = entry.fingerprint
    return tuple(entries)


def test_catalog_decodes_supported_batches_and_retains_unknown_entries(make_entry):
    batch = _batch(make_entry, "batch-1")
    known = domain_v1_registry().encode(batch)
    unknown = SchemaDocument.build(
        SchemaRef.parse("vendor.audit_marker", "1.0"),
        {"marker": "month-end"},
    )

    catalog = JournalCatalog.from_entries(_chain(known, unknown))

    assert catalog.batches == (batch,)
    assert catalog.reports == ()
    assert catalog.unrecognized_entries[0].document == unknown
    assert tuple(map(str, catalog.unrecognized_schema_names)) == ("vendor.audit_marker",)
    assert catalog.contains_batch(BatchId("batch-1"))


def test_catalog_fails_closed_for_a_future_version_of_a_known_schema():
    future = SchemaDocument.build(
        SchemaRef.parse(str(LEDGER_BATCH_V1.name), "2.0"),
        {"batch_id": "future"},
    )

    with pytest.raises(CatalogError) as captured:
        JournalCatalog.from_entries(_chain(future))

    assert captured.value.code == "catalog.schema_version_unsupported"
    assert captured.value.path == "$.entries[0].document.schema.version"


def test_catalog_rejects_duplicate_batch_ids_across_history(make_entry):
    registry = domain_v1_registry()
    first = registry.encode(_batch(make_entry, "duplicate", "entry-1"))
    second = registry.encode(_batch(make_entry, "duplicate", "entry-2"))

    with pytest.raises(CatalogError) as captured:
        JournalCatalog.from_entries(_chain(first, second))

    assert captured.value.code == "catalog.batch_duplicate"


def test_catalog_validates_public_arguments():
    with pytest.raises(CatalogError) as captured:
        JournalCatalog.from_entries("not entries")
    assert captured.value.code == "catalog.entries_type"

    with pytest.raises(CatalogError) as captured:
        JournalCatalog((), ()).contains_batch("batch")  # type: ignore[arg-type]
    assert captured.value.code == "catalog.batch_id_type"
