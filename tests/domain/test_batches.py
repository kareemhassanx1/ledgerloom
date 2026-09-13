from dataclasses import FrozenInstanceError, replace

import pytest

from ledgerloom.domain.batches import LedgerBatch, LocatedDiagnostic
from ledgerloom.domain.diagnostics import Diagnostic, DiagnosticSeverity, ValidationResult
from ledgerloom.domain.errors import DomainValidationError
from ledgerloom.domain.identifiers import AccountId, BatchId, EntryId, SourceId, SourceRecordId
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import LedgerEntry, SourceRecord
from ledgerloom.domain.time import UtcTimestamp


def make_entry(
    *,
    entry_id: str = "entry-1",
    source_id: str = "bank",
    record_id: str = "record-1",
) -> LedgerEntry:
    record = SourceRecord(
        source_id=SourceId(source_id),
        record_id=SourceRecordId(record_id),
        account_id=AccountId("assets:cash"),
        amount=Money.parse("10.50", "USD"),
        occurred_at=UtcTimestamp.parse("2026-08-22T16:00:00Z"),
    )
    return LedgerEntry.from_source_record(EntryId(entry_id), record)


def make_error() -> Diagnostic:
    return Diagnostic(
        code="record.amount_invalid",
        field="amount",
        message="must be valid decimal text",
    )


def make_batch(*results: ValidationResult[LedgerEntry]) -> LedgerBatch:
    return LedgerBatch(BatchId("batch-1"), SourceId("bank"), results)


def test_batch_preserves_successes_failures_and_input_positions():
    first = make_entry()
    third = make_entry(entry_id="entry-3", record_id="record-3")
    error = make_error()

    batch = make_batch(
        ValidationResult.success(first),
        ValidationResult.failure(error),
        ValidationResult.success(third),
    )

    assert batch.entries == (first, third)
    assert batch.diagnostics == (LocatedDiagnostic(position=2, diagnostic=error),)
    assert batch.accepted_count == 2
    assert batch.rejected_count == 1
    assert not batch.is_complete


def test_batch_reports_warnings_at_their_successful_item_position():
    warning = Diagnostic(
        code="record.reference_missing",
        field="reference",
        message="will reduce match confidence",
        severity=DiagnosticSeverity.WARNING,
    )
    entry = make_entry()

    batch = make_batch(ValidationResult.success(entry, warning))

    assert batch.is_complete
    assert batch.diagnostics == (LocatedDiagnostic(1, warning),)


def test_batch_requires_nonempty_immutable_results():
    result = ValidationResult.success(make_entry())

    with pytest.raises(DomainValidationError) as empty:
        make_batch()
    with pytest.raises(DomainValidationError) as mutable:
        LedgerBatch(BatchId("batch-1"), SourceId("bank"), [result])  # type: ignore[arg-type]

    assert empty.value.code == "ledger_batch.empty"
    assert mutable.value.code == "ledger_batch.results_type"


@pytest.mark.parametrize(
    "batch_id,source_id,expected_code",
    [
        ("batch-1", SourceId("bank"), "ledger_batch.batch_id_type"),
        (BatchId("batch-1"), "bank", "ledger_batch.source_id_type"),
    ],
)
def test_batch_requires_validated_identifiers(batch_id, source_id, expected_code):
    with pytest.raises(DomainValidationError) as captured:
        LedgerBatch(
            batch_id,
            source_id,
            (ValidationResult.failure(make_error()),),
        )

    assert captured.value.code == expected_code


def test_batch_rejects_non_result_and_non_entry_values():
    with pytest.raises(DomainValidationError) as wrong_result:
        LedgerBatch(BatchId("batch-1"), SourceId("bank"), (make_entry(),))  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError) as wrong_value:
        make_batch(ValidationResult.success("not-an-entry"))  # type: ignore[arg-type]

    assert wrong_result.value.code == "ledger_batch.result_type"
    assert wrong_value.value.code == "ledger_batch.value_type"


def test_batch_rejects_entries_from_another_source():
    result = ValidationResult.success(make_entry(source_id="card"))

    with pytest.raises(DomainValidationError) as captured:
        make_batch(result)

    assert captured.value.code == "ledger_batch.source_mismatch"


def test_batch_rejects_duplicate_entry_identifiers():
    first = make_entry(entry_id="entry-shared", record_id="record-1")
    second = make_entry(entry_id="entry-shared", record_id="record-2")

    with pytest.raises(DomainValidationError) as captured:
        make_batch(ValidationResult.success(first), ValidationResult.success(second))

    assert captured.value.code == "ledger_batch.duplicate_entry"


def test_batch_rejects_duplicate_source_records():
    first = make_entry(entry_id="entry-1", record_id="record-shared")
    second = make_entry(entry_id="entry-2", record_id="record-shared")

    with pytest.raises(DomainValidationError) as captured:
        make_batch(ValidationResult.success(first), ValidationResult.success(second))

    assert captured.value.code == "ledger_batch.duplicate_source_record"


def test_batch_is_immutable_and_does_not_alias_mutable_input():
    batch = make_batch(ValidationResult.success(make_entry()))

    with pytest.raises(FrozenInstanceError):
        batch.results = ()  # type: ignore[misc]
    with pytest.raises(DomainValidationError):
        replace(batch, results=list(batch.results))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "position,diagnostic,expected_code",
    [
        (True, make_error(), "located_diagnostic.position_type"),
        (0, make_error(), "located_diagnostic.position_range"),
        (1, "not-a-diagnostic", "located_diagnostic.diagnostic_type"),
    ],
)
def test_located_diagnostic_validates_its_public_boundary(position, diagnostic, expected_code):
    with pytest.raises(DomainValidationError) as captured:
        LocatedDiagnostic(position, diagnostic)

    assert captured.value.code == expected_code
