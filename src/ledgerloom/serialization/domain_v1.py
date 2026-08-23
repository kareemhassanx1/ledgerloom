"""Version 1 schemas for LedgerLoom's foundational domain values."""

from __future__ import annotations

from collections.abc import Callable

from ledgerloom.domain.batches import LedgerBatch
from ledgerloom.domain.diagnostics import Diagnostic, DiagnosticSeverity, ValidationResult
from ledgerloom.domain.errors import DomainValidationError
from ledgerloom.domain.identifiers import (
    AccountId,
    BatchId,
    EntryId,
    SourceId,
    SourceRecordId,
)
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import LedgerEntry, SourceRecord
from ledgerloom.domain.time import UtcTimestamp
from ledgerloom.serialization.canonical import JsonValue
from ledgerloom.serialization.errors import SerializationError
from ledgerloom.serialization.schema import (
    FunctionSchemaCodec,
    SchemaRef,
    SchemaRegistry,
)

SOURCE_RECORD_V1 = SchemaRef.parse("ledgerloom.source_record", "1.0")
LEDGER_ENTRY_V1 = SchemaRef.parse("ledgerloom.ledger_entry", "1.0")
LEDGER_BATCH_V1 = SchemaRef.parse("ledgerloom.ledger_batch", "1.0")


def _path(parent: str, field: str) -> str:
    return f"{parent}.{field}"


def _object(
    value: JsonValue,
    *,
    path: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise SerializationError(
            code="payload.object_type",
            path=path,
            message="must be an object",
        )
    fields = frozenset(value)
    missing = sorted(required - fields)
    extra = sorted(fields - required - optional)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise SerializationError(
            code="payload.object_fields",
            path=path,
            message="object fields are invalid: " + "; ".join(details),
        )
    return value


def _text(value: JsonValue, *, path: str) -> str:
    if not isinstance(value, str):
        raise SerializationError(
            code="payload.text_type",
            path=path,
            message="must be text",
        )
    return value


def _optional_text(value: JsonValue, *, path: str) -> str | None:
    if value is None:
        return None
    return _text(value, path=path)


def _array(value: JsonValue, *, path: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise SerializationError(
            code="payload.array_type",
            path=path,
            message="must be an array",
        )
    return value


def _domain[T](factory: Callable[[], T], *, path: str) -> T:
    try:
        return factory()
    except DomainValidationError as error:
        field_path = path if not error.field else _path(path, error.field)
        raise SerializationError(
            code="payload.domain_invalid",
            path=field_path,
            message=error.message,
        ) from error


def _require_python_type[T](value: object, expected: type[T]) -> T:
    if not isinstance(value, expected):
        raise SerializationError(
            code="payload.python_type",
            path="$.payload",
            message=f"must be a {expected.__name__}",
        )
    return value


def encode_money(value: Money) -> dict[str, JsonValue]:
    money = _require_python_type(value, Money)
    return {"amount": money.canonical_amount(), "currency": money.currency.code}


def decode_money(value: JsonValue, *, path: str = "$.payload.amount") -> Money:
    payload = _object(
        value,
        path=path,
        required=frozenset({"amount", "currency"}),
    )
    amount = _text(payload["amount"], path=_path(path, "amount"))
    currency = _text(payload["currency"], path=_path(path, "currency"))
    return _domain(lambda: Money.parse(amount, currency), path=path)


def encode_source_record(value: SourceRecord) -> dict[str, JsonValue]:
    record = _require_python_type(value, SourceRecord)
    return {
        "account_id": str(record.account_id),
        "amount": encode_money(record.amount),
        "occurred_at": record.occurred_at.canonical(),
        "record_id": str(record.record_id),
        "reference": record.reference,
        "source_id": str(record.source_id),
    }


def decode_source_record(value: JsonValue) -> SourceRecord:
    path = "$.payload"
    payload = _object(
        value,
        path=path,
        required=frozenset(
            {"account_id", "amount", "occurred_at", "record_id", "reference", "source_id"}
        ),
    )
    source_id = _text(payload["source_id"], path=_path(path, "source_id"))
    record_id = _text(payload["record_id"], path=_path(path, "record_id"))
    account_id = _text(payload["account_id"], path=_path(path, "account_id"))
    occurred_at = _text(payload["occurred_at"], path=_path(path, "occurred_at"))
    reference = _optional_text(payload["reference"], path=_path(path, "reference"))
    money = decode_money(payload["amount"], path=_path(path, "amount"))
    return _domain(
        lambda: SourceRecord(
            source_id=SourceId(source_id),
            record_id=SourceRecordId(record_id),
            account_id=AccountId(account_id),
            amount=money,
            occurred_at=UtcTimestamp.parse(occurred_at),
            reference=reference,
        ),
        path=path,
    )


def encode_ledger_entry(value: LedgerEntry) -> dict[str, JsonValue]:
    entry = _require_python_type(value, LedgerEntry)
    return {
        "account_id": str(entry.account_id),
        "amount": encode_money(entry.amount),
        "entry_id": str(entry.entry_id),
        "occurred_at": entry.occurred_at.canonical(),
        "reference": entry.reference,
        "source_id": str(entry.source_id),
        "source_record_id": str(entry.source_record_id),
    }


def decode_ledger_entry(value: JsonValue, *, path: str = "$.payload") -> LedgerEntry:
    payload = _object(
        value,
        path=path,
        required=frozenset(
            {
                "account_id",
                "amount",
                "entry_id",
                "occurred_at",
                "reference",
                "source_id",
                "source_record_id",
            }
        ),
    )
    entry_id = _text(payload["entry_id"], path=_path(path, "entry_id"))
    source_id = _text(payload["source_id"], path=_path(path, "source_id"))
    source_record_id = _text(
        payload["source_record_id"],
        path=_path(path, "source_record_id"),
    )
    account_id = _text(payload["account_id"], path=_path(path, "account_id"))
    occurred_at = _text(payload["occurred_at"], path=_path(path, "occurred_at"))
    reference = _optional_text(payload["reference"], path=_path(path, "reference"))
    money = decode_money(payload["amount"], path=_path(path, "amount"))
    return _domain(
        lambda: LedgerEntry(
            entry_id=EntryId(entry_id),
            source_id=SourceId(source_id),
            source_record_id=SourceRecordId(source_record_id),
            account_id=AccountId(account_id),
            amount=money,
            occurred_at=UtcTimestamp.parse(occurred_at),
            reference=reference,
        ),
        path=path,
    )


def encode_diagnostic(value: Diagnostic) -> dict[str, JsonValue]:
    diagnostic = _require_python_type(value, Diagnostic)
    return {
        "code": diagnostic.code,
        "field": diagnostic.field,
        "message": diagnostic.message,
        "severity": diagnostic.severity.value,
    }


def decode_diagnostic(value: JsonValue, *, path: str) -> Diagnostic:
    payload = _object(
        value,
        path=path,
        required=frozenset({"code", "field", "message", "severity"}),
    )
    code = _text(payload["code"], path=_path(path, "code"))
    field = _text(payload["field"], path=_path(path, "field"))
    message = _text(payload["message"], path=_path(path, "message"))
    severity_text = _text(payload["severity"], path=_path(path, "severity"))
    try:
        severity = DiagnosticSeverity(severity_text)
    except ValueError as error:
        raise SerializationError(
            code="payload.enum",
            path=_path(path, "severity"),
            message="must be 'warning' or 'error'",
        ) from error
    return _domain(
        lambda: Diagnostic(code=code, field=field, message=message, severity=severity),
        path=path,
    )


def _encode_result(result: ValidationResult[LedgerEntry]) -> dict[str, JsonValue]:
    validated = _require_python_type(result, ValidationResult)
    diagnostics = [encode_diagnostic(item) for item in validated.diagnostics]
    if validated.value is None:
        return {"diagnostics": diagnostics, "status": "rejected"}
    return {
        "diagnostics": diagnostics,
        "entry": encode_ledger_entry(validated.value),
        "status": "accepted",
    }


def _decode_result(value: JsonValue, *, path: str) -> ValidationResult[LedgerEntry]:
    if not isinstance(value, dict):
        raise SerializationError(
            code="payload.object_type",
            path=path,
            message="must be an object",
        )
    status = _text(value.get("status"), path=_path(path, "status"))
    if status == "accepted":
        payload = _object(
            value,
            path=path,
            required=frozenset({"diagnostics", "entry", "status"}),
        )
        entry = decode_ledger_entry(payload["entry"], path=_path(path, "entry"))
    elif status == "rejected":
        payload = _object(
            value,
            path=path,
            required=frozenset({"diagnostics", "status"}),
        )
        entry = None
    else:
        raise SerializationError(
            code="payload.result_status",
            path=_path(path, "status"),
            message="must be 'accepted' or 'rejected'",
        )

    diagnostic_values = _array(
        payload["diagnostics"],
        path=_path(path, "diagnostics"),
    )
    diagnostics = tuple(
        decode_diagnostic(item, path=f"{path}.diagnostics[{index}]")
        for index, item in enumerate(diagnostic_values)
    )
    return _domain(
        lambda: ValidationResult(value=entry, diagnostics=diagnostics),
        path=path,
    )


def encode_ledger_batch(value: LedgerBatch) -> dict[str, JsonValue]:
    batch = _require_python_type(value, LedgerBatch)
    return {
        "batch_id": str(batch.batch_id),
        "results": [_encode_result(result) for result in batch.results],
        "source_id": str(batch.source_id),
    }


def decode_ledger_batch(value: JsonValue) -> LedgerBatch:
    path = "$.payload"
    payload = _object(
        value,
        path=path,
        required=frozenset({"batch_id", "results", "source_id"}),
    )
    batch_id = _text(payload["batch_id"], path=_path(path, "batch_id"))
    source_id = _text(payload["source_id"], path=_path(path, "source_id"))
    result_values = _array(payload["results"], path=_path(path, "results"))
    results = tuple(
        _decode_result(result, path=f"{path}.results[{index}]")
        for index, result in enumerate(result_values)
    )
    return _domain(
        lambda: LedgerBatch(BatchId(batch_id), SourceId(source_id), results),
        path=path,
    )


SOURCE_RECORD_V1_CODEC = FunctionSchemaCodec(
    SOURCE_RECORD_V1,
    SourceRecord,
    encode_source_record,
    decode_source_record,
)
LEDGER_ENTRY_V1_CODEC = FunctionSchemaCodec(
    LEDGER_ENTRY_V1,
    LedgerEntry,
    encode_ledger_entry,
    decode_ledger_entry,
)
LEDGER_BATCH_V1_CODEC = FunctionSchemaCodec(
    LEDGER_BATCH_V1,
    LedgerBatch,
    encode_ledger_batch,
    decode_ledger_batch,
)


def register_domain_v1(registry: SchemaRegistry) -> SchemaRegistry:
    """Register every foundational version 1 domain schema and return the registry."""

    if not isinstance(registry, SchemaRegistry):
        raise SerializationError(
            code="schema.registry_type",
            path="$",
            message="registry must be a SchemaRegistry",
        )
    registry.register(SOURCE_RECORD_V1_CODEC)
    registry.register(LEDGER_ENTRY_V1_CODEC)
    registry.register(LEDGER_BATCH_V1_CODEC)
    return registry


def domain_v1_registry() -> SchemaRegistry:
    """Build an isolated registry containing LedgerLoom's version 1 schemas."""

    return register_domain_v1(SchemaRegistry())
