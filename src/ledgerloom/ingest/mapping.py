"""Configurable source-record field mapping and row validation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypeVar

from ledgerloom.domain.diagnostics import Diagnostic, ValidationResult
from ledgerloom.domain.errors import DomainValidationError
from ledgerloom.domain.identifiers import AccountId, SourceId, SourceRecordId
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import SourceRecord
from ledgerloom.domain.time import UtcTimestamp
from ledgerloom.ingest.errors import IngestError

_MAX_COLUMN_NAME_LENGTH = 128
_ConstructedValue = TypeVar("_ConstructedValue")


def _validate_column_name(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise IngestError(
            code="ingest.column_name_type",
            path=f"$.columns.{field}",
            message="column name must be text",
        )
    if (
        not value
        or value != value.strip()
        or not value.isprintable()
        or len(value) > _MAX_COLUMN_NAME_LENGTH
    ):
        raise IngestError(
            code="ingest.column_name_format",
            path=f"$.columns.{field}",
            message=(
                "column name must be non-empty printable text without surrounding "
                f"whitespace and at most {_MAX_COLUMN_NAME_LENGTH} characters"
            ),
        )
    return value


@dataclass(frozen=True, slots=True)
class SourceRecordColumns:
    """Map external field names onto LedgerLoom's source-record fields."""

    record_id: str = "record_id"
    account_id: str = "account_id"
    amount: str = "amount"
    currency: str = "currency"
    occurred_at: str = "occurred_at"
    reference: str = "reference"

    def __post_init__(self) -> None:
        names = tuple(
            _validate_column_name(getattr(self, field), field=field)
            for field in (
                "record_id",
                "account_id",
                "amount",
                "currency",
                "occurred_at",
                "reference",
            )
        )
        if len(names) != len(set(names)):
            raise IngestError(
                code="ingest.column_name_duplicate",
                path="$.columns",
                message="mapped column names must be unique",
            )

    @property
    def required(self) -> tuple[str, ...]:
        return (
            self.record_id,
            self.account_id,
            self.amount,
            self.currency,
            self.occurred_at,
        )

    @property
    def all(self) -> tuple[str, ...]:
        return (*self.required, self.reference)


def _field_text(
    row: Mapping[str, object],
    column: str,
    *,
    field: str,
    required: bool,
) -> tuple[str | None, Diagnostic | None]:
    if column not in row:
        if not required:
            return None, None
        return None, Diagnostic(
            "ingest.field_missing",
            field,
            "required field is missing",
        )
    value = row[column]
    if value is None and not required:
        return None, None
    if not isinstance(value, str):
        return None, Diagnostic(
            "ingest.field_type",
            field,
            "field must be text",
        )
    if not value:
        if not required:
            return None, None
        return None, Diagnostic(
            "ingest.field_empty",
            field,
            "required field must not be empty",
        )
    return value, None


@dataclass(frozen=True, slots=True)
class SourceRecordMapper:
    """Validate one mapped object without retaining rejected field values."""

    source_id: SourceId
    columns: SourceRecordColumns = SourceRecordColumns()

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, SourceId):
            raise IngestError(
                code="ingest.source_id_type",
                path="$.mapper.source_id",
                message="source_id must be a SourceId",
            )
        if not isinstance(self.columns, SourceRecordColumns):
            raise IngestError(
                code="ingest.columns_type",
                path="$.mapper.columns",
                message="columns must be a SourceRecordColumns",
            )

    def map(self, row: Mapping[str, object]) -> ValidationResult[SourceRecord]:
        """Map one external object into a validated source record."""

        if not isinstance(row, Mapping):
            raise IngestError(
                code="ingest.row_type",
                path="$.row",
                message="row must be a mapping",
            )
        specifications = (
            ("record_id", self.columns.record_id, True),
            ("account_id", self.columns.account_id, True),
            ("amount", self.columns.amount, True),
            ("currency", self.columns.currency, True),
            ("occurred_at", self.columns.occurred_at, True),
            ("reference", self.columns.reference, False),
        )
        values: dict[str, str | None] = {}
        diagnostics: list[Diagnostic] = []
        for field, column, required in specifications:
            value, diagnostic = _field_text(
                row,
                column,
                field=field,
                required=required,
            )
            values[field] = value
            if diagnostic is not None:
                diagnostics.append(diagnostic)
        if diagnostics:
            return ValidationResult.failure(*diagnostics)

        def construct(
            field: str,
            factory: Callable[[], _ConstructedValue],
        ) -> _ConstructedValue | None:
            try:
                return factory()
            except DomainValidationError as error:
                diagnostics.append(
                    Diagnostic(
                        error.code,
                        field if not error.field else error.field,
                        error.message,
                    )
                )
                return None

        record_id_text = values["record_id"]
        account_id_text = values["account_id"]
        amount_text = values["amount"]
        currency_text = values["currency"]
        occurred_at_text = values["occurred_at"]
        assert record_id_text is not None
        assert account_id_text is not None
        assert amount_text is not None
        assert currency_text is not None
        assert occurred_at_text is not None

        record_id = construct("record_id", lambda: SourceRecordId(record_id_text))
        account_id = construct("account_id", lambda: AccountId(account_id_text))
        amount = construct(
            "amount",
            lambda: Money.parse(amount_text, currency_text),
        )
        occurred_at = construct("occurred_at", lambda: UtcTimestamp.parse(occurred_at_text))
        if diagnostics:
            return ValidationResult.failure(*diagnostics)

        assert record_id is not None
        assert account_id is not None
        assert amount is not None
        assert occurred_at is not None
        try:
            record = SourceRecord(
                self.source_id,
                record_id,
                account_id,
                amount,
                occurred_at,
                values["reference"],
            )
        except DomainValidationError as error:
            return ValidationResult.failure(Diagnostic.from_error(error))
        return ValidationResult.success(record)
