from dataclasses import FrozenInstanceError

import pytest

from ledgerloom.domain import SourceId
from ledgerloom.ingest import IngestError, SourceRecordColumns, SourceRecordMapper


def _row(**changes):
    row = {
        "record_id": "txn-42",
        "account_id": "assets:cash",
        "amount": "125.7500",
        "currency": "USD",
        "occurred_at": "2026-08-31T09:15:30+03:00",
        "reference": "invoice-42",
    }
    row.update(changes)
    return row


def test_mapper_builds_exact_source_record():
    result = SourceRecordMapper(SourceId("bank-primary")).map(_row())

    assert result.is_valid
    assert str(result.value.source_id) == "bank-primary"
    assert str(result.value.record_id) == "txn-42"
    assert result.value.amount.canonical_amount() == "125.75"
    assert result.value.occurred_at.canonical() == "2026-08-31T06:15:30Z"
    assert result.value.reference == "invoice-42"


def test_mapper_accepts_missing_or_empty_optional_reference():
    mapper = SourceRecordMapper(SourceId("bank"))
    absent = _row()
    del absent["reference"]

    assert mapper.map(absent).value.reference is None
    assert mapper.map(_row(reference="")).value.reference is None


def test_mapper_collects_all_structural_field_errors_without_values():
    row = _row(record_id="", amount=10, occurred_at=None)

    result = SourceRecordMapper(SourceId("bank")).map(row)

    assert not result.is_valid
    assert tuple(item.code for item in result.diagnostics) == (
        "ingest.field_empty",
        "ingest.field_type",
        "ingest.field_type",
    )
    assert tuple(item.field for item in result.diagnostics) == (
        "record_id",
        "amount",
        "occurred_at",
    )
    assert "10" not in repr(result.diagnostics)


def test_mapper_collects_independent_domain_validation_errors():
    result = SourceRecordMapper(SourceId("bank")).map(
        _row(record_id="bad id", amount="not-decimal", occurred_at="yesterday")
    )

    assert not result.is_valid
    assert tuple(item.code for item in result.diagnostics) == (
        "identifier.format",
        "money.parse_format",
        "timestamp.parse_format",
    )


def test_custom_columns_map_external_names():
    mapper = SourceRecordMapper(
        SourceId("bank"),
        SourceRecordColumns(
            record_id="id",
            account_id="account",
            amount="value",
            currency="unit",
            occurred_at="timestamp",
            reference="memo",
        ),
    )

    result = mapper.map(
        {
            "id": "txn-1",
            "account": "assets:cash",
            "value": "10",
            "unit": "EUR",
            "timestamp": "2026-08-31T10:00:00Z",
            "memo": None,
        }
    )

    assert result.is_valid
    assert result.value.amount.currency.code == "EUR"


def test_columns_reject_duplicate_and_noncanonical_names():
    with pytest.raises(IngestError) as duplicate:
        SourceRecordColumns(record_id="id", account_id="id")
    with pytest.raises(IngestError) as whitespace:
        SourceRecordColumns(amount=" amount ")

    assert duplicate.value.code == "ingest.column_name_duplicate"
    assert whitespace.value.code == "ingest.column_name_format"


def test_mapper_rejects_non_mapping_and_is_immutable():
    mapper = SourceRecordMapper(SourceId("bank"))

    with pytest.raises(IngestError) as captured:
        mapper.map("row")  # type: ignore[arg-type]
    with pytest.raises(FrozenInstanceError):
        mapper.source_id = SourceId("other")  # type: ignore[misc]

    assert captured.value.code == "ingest.row_type"
