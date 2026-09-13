from dataclasses import FrozenInstanceError

import pytest

from ledgerloom.domain import (
    AccountId,
    Diagnostic,
    DiagnosticSeverity,
    Money,
    SourceId,
    SourceRecord,
    SourceRecordId,
    UtcTimestamp,
    ValidationResult,
)
from ledgerloom.ingest import (
    ImportDiagnosticCount,
    ImportLocation,
    ImportRowResult,
    ImportSummary,
    IngestError,
    summarize_import,
)


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


def test_import_summary_counts_results_severity_and_reasons():
    warning = Diagnostic(
        "ingest.reference_missing",
        "reference",
        "reference was not supplied",
        severity=DiagnosticSeverity.WARNING,
    )
    error = _error()
    rows = (
        ImportRowResult(
            ImportLocation(1, 2),
            ValidationResult.success(_record(), warning),
        ),
        ImportRowResult(
            ImportLocation(2, 3),
            ValidationResult.failure(error),
        ),
        ImportRowResult(
            ImportLocation(3, 4),
            ValidationResult.failure(error),
        ),
    )

    summary = summarize_import(rows)

    assert summary == ImportSummary(
        total=3,
        accepted=1,
        rejected=2,
        warnings=1,
        errors=2,
        diagnostic_counts=(
            ImportDiagnosticCount("ingest.amount_invalid", 2),
            ImportDiagnosticCount("ingest.reference_missing", 1),
        ),
    )


def test_import_summary_supports_an_empty_stream():
    assert summarize_import(()) == ImportSummary(0, 0, 0, 0, 0)


def test_import_summary_rejects_inconsistent_public_counts():
    with pytest.raises(IngestError) as outcomes:
        ImportSummary(2, 1, 0, 0, 0)
    with pytest.raises(IngestError) as diagnostics:
        ImportSummary(
            1,
            0,
            1,
            0,
            1,
            (ImportDiagnosticCount("ingest.problem", 2),),
        )

    assert outcomes.value.code == "ingest.summary_result_mismatch"
    assert diagnostics.value.code == "ingest.summary_diagnostic_mismatch"


def test_import_summary_is_a_terminal_stream_operation():
    consumed = 0

    def rows():
        nonlocal consumed
        consumed += 1
        yield ImportRowResult(ImportLocation(1, 1), ValidationResult.success(_record()))

    stream = rows()

    assert consumed == 0
    assert summarize_import(stream).accepted == 1
    assert consumed == 1
    assert tuple(stream) == ()
