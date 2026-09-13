import json

import pytest

from ledgerloom.domain.batches import LedgerBatch
from ledgerloom.domain.diagnostics import Diagnostic, DiagnosticSeverity, ValidationResult
from ledgerloom.domain.identifiers import AccountId, BatchId, EntryId, SourceId, SourceRecordId
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import LedgerEntry, SourceRecord
from ledgerloom.domain.time import UtcTimestamp
from ledgerloom.serialization.canonical import canonical_json_bytes
from ledgerloom.serialization.domain_v1 import (
    LEDGER_BATCH_V1,
    LEDGER_ENTRY_V1,
    SOURCE_RECORD_V1,
    decode_diagnostic,
    decode_ledger_batch,
    decode_ledger_entry,
    decode_money,
    decode_source_record,
    domain_v1_registry,
    encode_ledger_batch,
    encode_ledger_entry,
    encode_money,
    encode_source_record,
    register_domain_v1,
)
from ledgerloom.serialization.errors import SerializationError
from ledgerloom.serialization.fingerprints import ContentFingerprint
from ledgerloom.serialization.schema import SchemaDocument, SchemaRegistry, SchemaVersion


def make_record(
    *,
    record_id: str = "txn-42",
    amount: str = "125.7500",
    reference: str | None = "فاتورة-٤٢",
) -> SourceRecord:
    return SourceRecord(
        SourceId("bank-primary"),
        SourceRecordId(record_id),
        AccountId("assets:checking"),
        Money.parse(amount, "USD"),
        UtcTimestamp.parse("2026-08-23T09:15:30.123456+03:00"),
        reference,
    )


def make_entry(
    *,
    entry_id: str = "entry-42",
    record_id: str = "txn-42",
    amount: str = "125.7500",
    reference: str | None = "فاتورة-٤٢",
) -> LedgerEntry:
    return LedgerEntry.from_source_record(
        EntryId(entry_id),
        make_record(record_id=record_id, amount=amount, reference=reference),
    )


def make_error() -> Diagnostic:
    return Diagnostic("record.amount_invalid", "amount", "must be valid decimal text")


def make_warning() -> Diagnostic:
    return Diagnostic(
        "record.reference_missing",
        "reference",
        "will reduce match confidence",
        DiagnosticSeverity.WARNING,
    )


def make_batch() -> LedgerBatch:
    return LedgerBatch(
        BatchId("batch-23"),
        SourceId("bank-primary"),
        (
            ValidationResult.success(make_entry(), make_warning()),
            ValidationResult.failure(make_error()),
            ValidationResult.success(
                make_entry(entry_id="entry-44", record_id="txn-44", amount="-8.50", reference=None)
            ),
        ),
    )


def test_money_v1_uses_text_for_exact_decimal_and_currency():
    assert encode_money(Money.parse("125.7500", "USD")) == {
        "amount": "125.75",
        "currency": "USD",
    }


@pytest.mark.parametrize("amount", ["0", "-12.50", "0.000001", "1000000000000000"])
def test_money_v1_round_trip_preserves_exact_value(amount):
    original = Money.parse(amount, "EGP")

    assert decode_money(encode_money(original)) == original


def test_source_record_v1_has_stable_observable_payload():
    payload = encode_source_record(make_record())

    assert payload == {
        "account_id": "assets:checking",
        "amount": {"amount": "125.75", "currency": "USD"},
        "occurred_at": "2026-08-23T06:15:30.123456Z",
        "record_id": "txn-42",
        "reference": "فاتورة-٤٢",
        "source_id": "bank-primary",
    }


@pytest.mark.parametrize("reference", [None, "invoice-42", "فاتورة-٤٢"])
def test_source_record_v1_round_trip_preserves_reference(reference):
    original = make_record(reference=reference)

    assert decode_source_record(encode_source_record(original)) == original


def test_ledger_entry_v1_round_trip_preserves_provenance():
    original = make_entry()

    decoded = decode_ledger_entry(encode_ledger_entry(original))

    assert decoded == original
    assert decoded.source_key == original.source_key


def test_ledger_batch_v1_round_trip_preserves_order_and_outcomes():
    original = make_batch()

    decoded = decode_ledger_batch(encode_ledger_batch(original))

    assert decoded == original
    assert decoded.accepted_count == 2
    assert decoded.rejected_count == 1
    assert decoded.diagnostics == original.diagnostics


def test_registry_contains_each_domain_v1_schema():
    registry = domain_v1_registry()

    assert registry.available_versions(SOURCE_RECORD_V1.name) == (SchemaVersion(1, 0),)
    assert registry.available_versions(LEDGER_ENTRY_V1.name) == (SchemaVersion(1, 0),)
    assert registry.available_versions(LEDGER_BATCH_V1.name) == (SchemaVersion(1, 0),)


@pytest.mark.parametrize(
    "value,ref",
    [
        (make_record(), SOURCE_RECORD_V1),
        (make_entry(), LEDGER_ENTRY_V1),
        (make_batch(), LEDGER_BATCH_V1),
    ],
)
def test_registry_round_trip_uses_expected_schema(value, ref):
    registry = domain_v1_registry()

    document = registry.encode(value)

    assert document.schema == ref
    assert registry.decode(document) == value


def test_registry_decodes_canonical_bytes_with_expected_fingerprint():
    registry = domain_v1_registry()
    original = make_batch()
    document = registry.encode(original)

    decoded = registry.decode(
        document.content,
        expected_fingerprint=document.fingerprint,
    )

    assert decoded == original


def test_semantically_equal_values_have_equal_canonical_documents_and_fingerprints():
    registry = domain_v1_registry()

    first = registry.encode(make_record(amount="125.7500"))
    second = registry.encode(make_record(amount="125.75"))

    assert first.content == second.content
    assert first.fingerprint == second.fingerprint


def test_changed_domain_value_changes_fingerprint():
    registry = domain_v1_registry()

    first = registry.encode(make_record(amount="125.75"))
    second = registry.encode(make_record(amount="125.76"))

    assert first.fingerprint != second.fingerprint


def test_source_record_document_has_exact_canonical_envelope():
    document = domain_v1_registry().encode(make_record(reference=None))
    expected_payload = encode_source_record(make_record(reference=None))
    expected = canonical_json_bytes(
        {
            "payload": expected_payload,
            "schema": "ledgerloom.source_record",
            "version": "1.0",
        }
    )

    assert document.content == expected


@pytest.mark.parametrize(
    "payload,path",
    [
        ([], "$.payload"),
        ({"amount": "1", "currency": "USD"}, "$.payload"),
        (
            {
                "account_id": "assets:checking",
                "amount": {"amount": "1", "currency": "USD"},
                "occurred_at": "2026-08-23T06:00:00Z",
                "record_id": "record-1",
                "reference": None,
                "source_id": "bank",
                "unexpected": True,
            },
            "$.payload",
        ),
    ],
)
def test_source_record_decoder_rejects_wrong_shape(payload, path):
    with pytest.raises(SerializationError) as captured:
        decode_source_record(payload)

    assert captured.value.path == path
    assert captured.value.code in {"payload.object_type", "payload.object_fields"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", 42),
        ("record_id", None),
        ("account_id", ["assets:checking"]),
        ("occurred_at", True),
        ("reference", 42),
    ],
)
def test_source_record_decoder_reports_scalar_field_types(field, value):
    payload = encode_source_record(make_record())
    payload[field] = value

    with pytest.raises(SerializationError) as captured:
        decode_source_record(payload)

    assert captured.value.code == "payload.text_type"
    assert captured.value.path == f"$.payload.{field}"


@pytest.mark.parametrize(
    "payload,path",
    [
        ({"amount": 1, "currency": "USD"}, "$.payload.amount.amount"),
        ({"amount": "1", "currency": "usd"}, "$.payload.amount.currency"),
        ({"amount": "NaN", "currency": "USD"}, "$.payload.amount.amount"),
    ],
)
def test_money_decoder_rejects_invalid_exact_values(payload, path):
    with pytest.raises(SerializationError) as captured:
        decode_money(payload)

    assert captured.value.path == path


def test_entry_decoder_rejects_zero_amount_through_domain_invariant():
    payload = encode_ledger_entry(make_entry())
    payload["amount"] = {"amount": "0", "currency": "USD"}

    with pytest.raises(SerializationError) as captured:
        decode_ledger_entry(payload)

    assert captured.value.code == "payload.domain_invalid"
    assert captured.value.path == "$.payload.amount"


def test_diagnostic_decoder_rejects_unknown_severity():
    payload = {
        "code": "record.invalid",
        "field": "record",
        "message": "is invalid",
        "severity": "fatal",
    }

    with pytest.raises(SerializationError) as captured:
        decode_diagnostic(payload, path="$.payload.diagnostic")

    assert captured.value.code == "payload.enum"
    assert captured.value.path == "$.payload.diagnostic.severity"


def test_batch_decoder_rejects_unknown_result_status():
    payload = encode_ledger_batch(make_batch())
    results = payload["results"]
    assert isinstance(results, list)
    assert isinstance(results[0], dict)
    results[0]["status"] = "pending"

    with pytest.raises(SerializationError) as captured:
        decode_ledger_batch(payload)

    assert captured.value.code == "payload.result_status"
    assert captured.value.path == "$.payload.results[0].status"


def test_batch_decoder_rejects_accepted_result_without_entry():
    payload = encode_ledger_batch(make_batch())
    results = payload["results"]
    assert isinstance(results, list)
    assert isinstance(results[0], dict)
    del results[0]["entry"]

    with pytest.raises(SerializationError) as captured:
        decode_ledger_batch(payload)

    assert captured.value.code == "payload.object_fields"
    assert captured.value.path == "$.payload.results[0]"


def test_batch_decoder_rejects_rejected_result_with_entry():
    payload = encode_ledger_batch(make_batch())
    results = payload["results"]
    assert isinstance(results, list)
    assert isinstance(results[1], dict)
    results[1]["entry"] = encode_ledger_entry(make_entry(entry_id="entry-extra"))

    with pytest.raises(SerializationError) as captured:
        decode_ledger_batch(payload)

    assert captured.value.code == "payload.object_fields"
    assert captured.value.path == "$.payload.results[1]"


def test_batch_decoder_rejects_warning_only_failure():
    payload = encode_ledger_batch(make_batch())
    results = payload["results"]
    assert isinstance(results, list)
    assert isinstance(results[1], dict)
    diagnostics = results[1]["diagnostics"]
    assert isinstance(diagnostics, list)
    assert isinstance(diagnostics[0], dict)
    diagnostics[0]["severity"] = "warning"

    with pytest.raises(SerializationError) as captured:
        decode_ledger_batch(payload)

    assert captured.value.code == "payload.domain_invalid"
    assert captured.value.path == "$.payload.results[1].diagnostics"


def test_batch_decoder_rechecks_duplicate_entry_invariant():
    payload = encode_ledger_batch(make_batch())
    results = payload["results"]
    assert isinstance(results, list)
    assert isinstance(results[0], dict)
    assert isinstance(results[2], dict)
    first_entry = results[0]["entry"]
    assert isinstance(first_entry, dict)
    second_entry = results[2]["entry"]
    assert isinstance(second_entry, dict)
    second_entry["entry_id"] = first_entry["entry_id"]

    with pytest.raises(SerializationError) as captured:
        decode_ledger_batch(payload)

    assert captured.value.code == "payload.domain_invalid"


def test_tampering_with_document_bytes_breaks_expected_fingerprint():
    registry = domain_v1_registry()
    document = registry.encode(make_record())
    decoded = json.loads(document.content)
    decoded["payload"]["reference"] = "changed"
    changed = canonical_json_bytes(decoded)

    with pytest.raises(SerializationError) as captured:
        registry.decode(changed, expected_fingerprint=document.fingerprint)

    assert captured.value.code == "fingerprint.mismatch"


def test_noncanonical_domain_document_is_rejected_before_decoding():
    canonical = domain_v1_registry().encode(make_record()).content
    decoded = json.loads(canonical)
    noncanonical = json.dumps(decoded, ensure_ascii=False, indent=2).encode()

    with pytest.raises(SerializationError) as captured:
        SchemaDocument.parse(noncanonical)

    assert captured.value.code == "json.non_canonical"


def test_register_domain_v1_returns_same_registry_and_rejects_duplicate_registration():
    registry = SchemaRegistry()

    assert register_domain_v1(registry) is registry
    with pytest.raises(SerializationError) as captured:
        register_domain_v1(registry)

    assert captured.value.code == "schema.duplicate_codec"


def test_register_domain_v1_requires_registry_instance():
    with pytest.raises(SerializationError) as captured:
        register_domain_v1("registry")  # type: ignore[arg-type]

    assert captured.value.code == "schema.registry_type"


def test_domain_documents_can_be_verified_without_registry():
    document = domain_v1_registry().encode(make_entry())

    parsed = SchemaDocument.parse(document.content)

    assert parsed.schema == LEDGER_ENTRY_V1
    assert parsed.fingerprint == ContentFingerprint.from_bytes(document.content)
