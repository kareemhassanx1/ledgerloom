"""Immutable source records and normalized ledger entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from ledgerloom.domain.errors import DomainValidationError
from ledgerloom.domain.identifiers import AccountId, EntryId, SourceId, SourceRecordId
from ledgerloom.domain.money import Money
from ledgerloom.domain.time import UtcTimestamp

_MAX_REFERENCE_LENGTH = 256


def _require_type(value: object, expected: type[object], *, code: str, field: str) -> None:
    if not isinstance(value, expected):
        raise DomainValidationError(
            code=code,
            field=field,
            message=f"must be a {expected.__name__}",
            value=value,
        )


def _validate_reference(reference: object, *, owner: str) -> None:
    if reference is None:
        return
    if not isinstance(reference, str):
        raise DomainValidationError(
            code=f"{owner}.reference_type",
            field="reference",
            message="must be a string or None",
            value=reference,
        )
    if not reference or reference != reference.strip() or not reference.isprintable():
        raise DomainValidationError(
            code=f"{owner}.reference_format",
            field="reference",
            message="must be non-empty printable text without surrounding whitespace",
            value=reference,
        )
    if len(reference) > _MAX_REFERENCE_LENGTH:
        raise DomainValidationError(
            code=f"{owner}.reference_too_long",
            field="reference",
            message=f"must contain at most {_MAX_REFERENCE_LENGTH} characters",
            value=reference,
        )


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One validated record as supplied by an upstream financial source."""

    source_id: SourceId
    record_id: SourceRecordId
    account_id: AccountId
    amount: Money
    occurred_at: UtcTimestamp
    reference: str | None = None

    def __post_init__(self) -> None:
        _require_type(
            self.source_id,
            SourceId,
            code="source_record.source_id_type",
            field="source_id",
        )
        _require_type(
            self.record_id,
            SourceRecordId,
            code="source_record.record_id_type",
            field="record_id",
        )
        _require_type(
            self.account_id,
            AccountId,
            code="source_record.account_id_type",
            field="account_id",
        )
        _require_type(
            self.amount,
            Money,
            code="source_record.amount_type",
            field="amount",
        )
        _require_type(
            self.occurred_at,
            UtcTimestamp,
            code="source_record.occurred_at_type",
            field="occurred_at",
        )
        _validate_reference(self.reference, owner="source_record")

    @property
    def source_key(self) -> tuple[SourceId, SourceRecordId]:
        """Return the source-scoped identity used for deduplication."""

        return (self.source_id, self.record_id)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One normalized, non-zero financial entry with source provenance."""

    entry_id: EntryId
    source_id: SourceId
    source_record_id: SourceRecordId
    account_id: AccountId
    amount: Money
    occurred_at: UtcTimestamp
    reference: str | None = None

    def __post_init__(self) -> None:
        _require_type(
            self.entry_id,
            EntryId,
            code="ledger_entry.entry_id_type",
            field="entry_id",
        )
        _require_type(
            self.source_id,
            SourceId,
            code="ledger_entry.source_id_type",
            field="source_id",
        )
        _require_type(
            self.source_record_id,
            SourceRecordId,
            code="ledger_entry.source_record_id_type",
            field="source_record_id",
        )
        _require_type(
            self.account_id,
            AccountId,
            code="ledger_entry.account_id_type",
            field="account_id",
        )
        _require_type(
            self.amount,
            Money,
            code="ledger_entry.amount_type",
            field="amount",
        )
        if self.amount.is_zero:
            raise DomainValidationError(
                code="ledger_entry.zero_amount",
                field="amount",
                message="must not be zero",
                value=self.amount,
            )
        _require_type(
            self.occurred_at,
            UtcTimestamp,
            code="ledger_entry.occurred_at_type",
            field="occurred_at",
        )
        _validate_reference(self.reference, owner="ledger_entry")

    @classmethod
    def from_source_record(cls, entry_id: EntryId, record: SourceRecord) -> Self:
        """Normalize a validated record without losing its provenance."""

        _require_type(
            record,
            SourceRecord,
            code="ledger_entry.source_record_type",
            field="record",
        )
        return cls(
            entry_id=entry_id,
            source_id=record.source_id,
            source_record_id=record.record_id,
            account_id=record.account_id,
            amount=record.amount,
            occurred_at=record.occurred_at,
            reference=record.reference,
        )

    @property
    def source_key(self) -> tuple[SourceId, SourceRecordId]:
        return (self.source_id, self.source_record_id)
