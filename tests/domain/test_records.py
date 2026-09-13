from dataclasses import FrozenInstanceError

import pytest

from ledgerloom.domain.errors import DomainValidationError
from ledgerloom.domain.identifiers import AccountId, EntryId, SourceId, SourceRecordId
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import LedgerEntry, SourceRecord
from ledgerloom.domain.time import UtcTimestamp


def make_record(
    *,
    source_id: str = "bank-primary",
    record_id: str = "txn-0042",
    amount: str = "125.75",
    reference: str | None = "invoice-884",
) -> SourceRecord:
    return SourceRecord(
        source_id=SourceId(source_id),
        record_id=SourceRecordId(record_id),
        account_id=AccountId("assets:checking"),
        amount=Money.parse(amount, "USD"),
        occurred_at=UtcTimestamp.parse("2026-08-22T16:00:00Z"),
        reference=reference,
    )


def make_entry(*, entry_id: str = "entry-0042", record: SourceRecord | None = None) -> LedgerEntry:
    return LedgerEntry.from_source_record(EntryId(entry_id), record or make_record())


def test_source_record_retains_exact_validated_values_and_scoped_identity():
    record = make_record()

    assert record.amount == Money.parse("125.75", "USD")
    assert record.occurred_at.canonical() == "2026-08-22T16:00:00Z"
    assert record.source_key == (SourceId("bank-primary"), SourceRecordId("txn-0042"))


@pytest.mark.parametrize(
    "field,value,expected_code",
    [
        ("source_id", "bank-primary", "source_record.source_id_type"),
        ("record_id", "txn-0042", "source_record.record_id_type"),
        ("account_id", "assets:checking", "source_record.account_id_type"),
        ("amount", "125.75 USD", "source_record.amount_type"),
        ("occurred_at", "2026-08-22T16:00:00Z", "source_record.occurred_at_type"),
    ],
)
def test_source_record_rejects_unvalidated_component_types(field, value, expected_code):
    values = {
        "source_id": SourceId("bank-primary"),
        "record_id": SourceRecordId("txn-0042"),
        "account_id": AccountId("assets:checking"),
        "amount": Money.parse("125.75", "USD"),
        "occurred_at": UtcTimestamp.parse("2026-08-22T16:00:00Z"),
    }
    values[field] = value

    with pytest.raises(DomainValidationError) as captured:
        SourceRecord(**values)  # type: ignore[arg-type]

    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    "reference,expected_code",
    [
        (42, "source_record.reference_type"),
        ("", "source_record.reference_format"),
        (" padded ", "source_record.reference_format"),
        ("line\nbreak", "source_record.reference_format"),
        ("x" * 257, "source_record.reference_too_long"),
    ],
)
def test_source_record_rejects_invalid_references(reference, expected_code):
    with pytest.raises(DomainValidationError) as captured:
        make_record(reference=reference)

    assert captured.value.code == expected_code
    assert captured.value.field == "reference"


def test_reference_accepts_none_unicode_and_the_maximum_length():
    assert make_record(reference=None).reference is None
    assert make_record(reference="فاتورة-٤٢").reference == "فاتورة-٤٢"
    assert make_record(reference="x" * 256).reference == "x" * 256


def test_entry_factory_copies_record_values_and_provenance():
    record = make_record()

    entry = LedgerEntry.from_source_record(EntryId("entry-0042"), record)

    assert entry.entry_id == EntryId("entry-0042")
    assert entry.source_key == record.source_key
    assert entry.account_id == record.account_id
    assert entry.amount == record.amount
    assert entry.occurred_at == record.occurred_at
    assert entry.reference == record.reference


def test_entry_factory_requires_a_source_record():
    with pytest.raises(DomainValidationError) as captured:
        LedgerEntry.from_source_record(EntryId("entry-0042"), "not-a-record")  # type: ignore[arg-type]

    assert captured.value.code == "ledger_entry.source_record_type"


def test_zero_source_amount_is_preserved_but_cannot_become_an_entry():
    record = make_record(amount="0")

    assert record.amount.is_zero
    with pytest.raises(DomainValidationError) as captured:
        make_entry(record=record)

    assert captured.value.code == "ledger_entry.zero_amount"


def test_record_and_entry_are_immutable_and_hashable():
    record = make_record()
    entry = make_entry(record=record)

    with pytest.raises(FrozenInstanceError):
        record.reference = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        entry.reference = "changed"  # type: ignore[misc]

    assert {record: "raw"}[make_record()] == "raw"
    assert {entry: "normalized"}[make_entry()] == "normalized"
