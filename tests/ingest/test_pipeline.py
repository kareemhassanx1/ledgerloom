from io import StringIO

import pytest

from ledgerloom.domain import SourceId
from ledgerloom.ingest import (
    CsvImporter,
    IngestError,
    SourceRecordMapper,
    reject_duplicate_source_records,
)


def _csv() -> CsvImporter:
    return CsvImporter(SourceRecordMapper(SourceId("bank")))


def test_duplicate_filter_rejects_later_source_record_and_continues():
    source = StringIO(
        "record_id,account_id,amount,currency,occurred_at\n"
        "txn-1,assets:cash,10,USD,2026-08-31T08:00:00Z\n"
        "txn-1,assets:cash,20,USD,2026-08-31T09:00:00Z\n"
        "txn-2,assets:cash,30,USD,2026-08-31T10:00:00Z\n"
    )

    first, duplicate, third = tuple(
        reject_duplicate_source_records(_csv().import_rows(source))
    )

    assert first.is_valid
    assert not duplicate.is_valid
    assert duplicate.location.record_number == 2
    assert duplicate.diagnostics[0].code == "ingest.duplicate_source_record"
    assert "txn-1" not in repr(duplicate.diagnostics)
    assert third.is_valid


def test_invalid_rows_pass_through_and_do_not_claim_an_identity():
    source = StringIO(
        "record_id,account_id,amount,currency,occurred_at\n"
        "txn-1,assets:cash,invalid,USD,2026-08-31T08:00:00Z\n"
        "txn-1,assets:cash,20,USD,2026-08-31T09:00:00Z\n"
    )

    invalid, accepted = tuple(
        reject_duplicate_source_records(_csv().import_rows(source))
    )

    assert invalid.diagnostics[0].code == "money.parse_format"
    assert accepted.is_valid


def test_duplicate_filter_is_lazy():
    consumed = 0

    def rows():
        nonlocal consumed
        for row in _csv().import_rows(
            StringIO(
                "record_id,account_id,amount,currency,occurred_at\n"
                "txn-1,assets:cash,10,USD,2026-08-31T08:00:00Z\n"
                "txn-2,assets:cash,20,USD,2026-08-31T09:00:00Z\n"
            )
        ):
            consumed += 1
            yield row

    filtered = reject_duplicate_source_records(rows())

    assert consumed == 0
    assert next(filtered).is_valid
    assert consumed == 1


def test_duplicate_filter_validates_iterable_and_items():
    with pytest.raises(IngestError) as collection_error:
        tuple(reject_duplicate_source_records("rows"))  # type: ignore[arg-type]
    with pytest.raises(IngestError) as item_error:
        tuple(reject_duplicate_source_records((object(),)))  # type: ignore[arg-type]

    assert collection_error.value.code == "ingest.results_type"
    assert item_error.value.code == "ingest.result_type"
