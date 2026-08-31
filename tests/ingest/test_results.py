from dataclasses import FrozenInstanceError

import pytest

from ledgerloom.domain import (
    AccountId,
    Diagnostic,
    Money,
    SourceId,
    SourceRecord,
    SourceRecordId,
    UtcTimestamp,
    ValidationResult,
)
from ledgerloom.ingest import ImportLocation, ImportRowResult, IngestError


def _record() -> SourceRecord:
    return SourceRecord(
        SourceId("bank"),
        SourceRecordId("record-1"),
        AccountId("assets:cash"),
        Money.parse("10.50", "USD"),
        UtcTimestamp.parse("2026-08-31T08:00:00Z"),
    )


def _error() -> Diagnostic:
    return Diagnostic("ingest.amount_invalid", "amount", "must be decimal text")


def test_row_result_exposes_success_without_raw_input():
    record = _record()
    result = ImportRowResult(
        ImportLocation(record_number=2, line_number=4),
        ValidationResult.success(record),
    )

    assert result.is_valid
    assert result.record == record
    assert result.diagnostics == ()
    assert not hasattr(result, "raw")


def test_row_result_exposes_structured_failure():
    diagnostic = _error()
    result = ImportRowResult(
        ImportLocation(1, 3),
        ValidationResult.failure(diagnostic),
    )

    assert not result.is_valid
    assert result.record is None
    assert result.diagnostics == (diagnostic,)


@pytest.mark.parametrize("values", [(0, 1), (1, 0), (True, 1), (1, 1.5)])
def test_location_requires_positive_integer_positions(values):
    with pytest.raises(IngestError):
        ImportLocation(*values)


def test_row_result_requires_a_source_record_on_success():
    with pytest.raises(IngestError) as captured:
        ImportRowResult(ImportLocation(1, 1), ValidationResult.success("record"))

    assert captured.value.code == "ingest.value_type"


def test_import_results_are_immutable():
    result = ImportRowResult(ImportLocation(1, 1), ValidationResult.success(_record()))

    with pytest.raises(FrozenInstanceError):
        result.location = ImportLocation(2, 2)  # type: ignore[misc]
