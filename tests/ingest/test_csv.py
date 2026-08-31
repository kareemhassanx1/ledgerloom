from io import StringIO

import pytest

from ledgerloom.domain import SourceId
from ledgerloom.ingest import (
    CsvHeaderPolicy,
    CsvImporter,
    IngestError,
    SourceRecordMapper,
)


def _importer() -> CsvImporter:
    return CsvImporter(SourceRecordMapper(SourceId("bank")))


def test_csv_import_streams_valid_records_with_quoted_fields():
    source = StringIO(
        "record_id,account_id,amount,currency,occurred_at,reference\n"
        'txn-1,assets:cash,10.50,USD,2026-08-31T08:00:00Z,"invoice, one"\n'
        "txn-2,assets:cash,-2,USD,2026-08-31T09:00:00Z,\n"
    )

    results = tuple(_importer().import_rows(source))

    assert tuple(item.is_valid for item in results) == (True, True)
    assert tuple(item.location.line_number for item in results) == (2, 3)
    assert results[0].record.reference == "invoice, one"
    assert results[1].record.reference is None


def test_csv_import_continues_after_a_row_validation_failure():
    source = StringIO(
        "record_id,account_id,amount,currency,occurred_at,reference\n"
        "bad id,assets:cash,nope,USD,yesterday,private-value\n"
        "txn-2,assets:cash,2,USD,2026-08-31T09:00:00Z,invoice-2\n"
    )

    first, second = tuple(_importer().import_rows(source))

    assert not first.is_valid
    assert second.is_valid
    assert tuple(item.field for item in first.diagnostics) == (
        "source_record_id",
        "amount",
        "timestamp",
    )
    assert "private-value" not in repr(first.diagnostics)


def test_csv_multiline_record_reports_its_starting_physical_line():
    source = StringIO(
        "record_id,account_id,amount,currency,occurred_at,reference\n"
        'txn-1,assets:cash,10,USD,2026-08-31T08:00:00Z,"two\nlines"\n'
        "txn-2,assets:cash,20,USD,2026-08-31T09:00:00Z,next\n"
    )

    results = tuple(_importer().import_rows(source))

    assert tuple(item.location.line_number for item in results) == (2, 4)
    assert not results[0].is_valid
    assert results[0].diagnostics[0].code == "source_record.reference_format"
    assert results[1].is_valid


def test_csv_shape_failure_does_not_hide_later_rows():
    source = StringIO(
        "record_id,account_id,amount,currency,occurred_at\n"
        "txn-1,assets:cash,10,USD\n"
        "txn-2,assets:cash,20,USD,2026-08-31T09:00:00Z\n"
    )

    first, second = tuple(_importer().import_rows(source))

    assert first.diagnostics[0].code == "ingest.csv_row_shape"
    assert second.is_valid


def test_csv_iterator_is_lazy_and_does_not_materialize_input():
    consumed = 0

    def lines():
        nonlocal consumed
        for line in (
            "record_id,account_id,amount,currency,occurred_at\n",
            "txn-1,assets:cash,10,USD,2026-08-31T08:00:00Z\n",
            "txn-2,assets:cash,20,USD,2026-08-31T09:00:00Z\n",
        ):
            consumed += 1
            yield line

    rows = _importer().import_rows(lines())

    assert consumed == 0
    assert next(rows).record.record_id.value == "txn-1"
    assert consumed == 2


@pytest.mark.parametrize(
    "content,code",
    [
        ("", "ingest.csv_empty"),
        ("\n", "ingest.csv_header_empty"),
        (
            "record_id,record_id,account_id,amount,currency,occurred_at\n",
            "ingest.csv_header_duplicate",
        ),
        ("record_id,amount,currency,occurred_at\n", "ingest.csv_header_missing"),
    ],
)
def test_csv_rejects_unusable_headers(content, code):
    with pytest.raises(IngestError) as captured:
        tuple(_importer().import_rows(StringIO(content)))

    assert captured.value.code == code


def test_csv_validates_stream_and_delimiter_configuration():
    with pytest.raises(IngestError) as lines_error:
        tuple(_importer().import_rows("row"))
    with pytest.raises(IngestError) as delimiter_error:
        CsvImporter(SourceRecordMapper(SourceId("bank")), delimiter="||")

    assert lines_error.value.code == "ingest.lines_type"
    assert delimiter_error.value.code == "ingest.csv_delimiter"


def test_csv_accepts_and_removes_a_utf8_bom_by_default():
    source = StringIO(
        "\ufeffrecord_id,account_id,amount,currency,occurred_at\n"
        "txn-1,assets:cash,10,USD,2026-08-31T08:00:00Z\n"
    )

    result = next(_importer().import_rows(source))

    assert result.is_valid
    assert result.record.record_id.value == "txn-1"


def test_csv_can_reject_a_bom_under_a_strict_header_policy():
    importer = CsvImporter(
        SourceRecordMapper(SourceId("bank")),
        header_policy=CsvHeaderPolicy(allow_utf8_bom=False),
    )

    with pytest.raises(IngestError) as captured:
        tuple(importer.import_rows(StringIO("\ufeffrecord_id\n")))

    assert captured.value.code == "ingest.csv_header_bom"


@pytest.mark.parametrize("header", [" record_id", "record_id ", "", "bad\x00name"])
def test_csv_rejects_ambiguous_header_names(header):
    content = (
        f"{header},account_id,amount,currency,occurred_at\n"
        "txn-1,assets:cash,10,USD,2026-08-31T08:00:00Z\n"
    )

    with pytest.raises(IngestError) as captured:
        tuple(_importer().import_rows(StringIO(content)))

    assert captured.value.code in {"ingest.csv_header_empty", "ingest.csv_header_name"}


def test_csv_extra_columns_are_policy_controlled():
    content = (
        "record_id,account_id,amount,currency,occurred_at,vendor_status\n"
        "txn-1,assets:cash,10,USD,2026-08-31T08:00:00Z,settled\n"
    )
    permissive = _importer()
    strict = CsvImporter(
        SourceRecordMapper(SourceId("bank")),
        header_policy=CsvHeaderPolicy(allow_extra_columns=False),
    )

    assert next(permissive.import_rows(StringIO(content))).is_valid
    with pytest.raises(IngestError) as captured:
        tuple(strict.import_rows(StringIO(content)))

    assert captured.value.code == "ingest.csv_header_unexpected"
