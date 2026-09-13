import pytest

from ledgerloom.reconciliation import (
    RECONCILIATION_REPORT_V1,
    ReconciliationReportError,
    build_reconciliation_report,
    build_reconciliation_report_document,
    decode_reconciliation_report,
    encode_reconciliation_report,
    parse_reconciliation_report_document,
)
from ledgerloom.serialization import (
    ContentFingerprint,
    SchemaDocument,
    SchemaRef,
    SerializationError,
    canonical_json_bytes,
    loads_canonical_json,
)


def test_report_document_is_canonical_versioned_and_fingerprinted(mixed_batch):
    report = build_reconciliation_report(mixed_batch)

    document = build_reconciliation_report_document(report)
    envelope = loads_canonical_json(document.content)

    assert document.schema == RECONCILIATION_REPORT_V1
    assert document.fingerprint == ContentFingerprint.from_bytes(document.content)
    assert envelope["schema"] == "ledgerloom.reconciliation_report"
    assert envelope["version"] == "1.0"
    assert envelope["payload"] == encode_reconciliation_report(report)


def test_report_payload_retains_policy_selection_and_nested_evidence(mixed_batch):
    report = build_reconciliation_report(mixed_batch)

    payload = encode_reconciliation_report(report)
    matched = next(item for item in payload["decisions"] if item["anchor_id"] == "anchor-match")

    assert payload["policy"]["max_time_difference_microseconds"] == "300000000"
    assert matched["selection"]["evaluated"] == 1
    assert matched["candidates"][0]["evidence"][0]["rule_id"] == ("ledgerloom.same_account")


def test_equivalent_reports_produce_identical_documents(mixed_batch):
    first = build_reconciliation_report_document(build_reconciliation_report(mixed_batch))
    second = build_reconciliation_report_document(build_reconciliation_report(mixed_batch))

    assert second.content == first.content
    assert second.fingerprint == first.fingerprint


def test_report_encoder_rejects_non_report_values():
    with pytest.raises(ReconciliationReportError) as captured:
        encode_reconciliation_report(object())  # type: ignore[arg-type]

    assert captured.value.code == "report.encode_type"


def test_report_document_round_trips_through_verified_models(mixed_batch):
    expected = build_reconciliation_report(mixed_batch)
    document = build_reconciliation_report_document(expected)

    decoded = parse_reconciliation_report_document(
        document.content,
        expected_fingerprint=document.fingerprint,
    )

    assert decoded == expected
    assert decode_reconciliation_report(document.payload) == expected


def test_report_parser_rejects_a_changed_document_against_expected_fingerprint(
    mixed_batch,
):
    document = build_reconciliation_report_document(build_reconciliation_report(mixed_batch))
    envelope = loads_canonical_json(document.content)
    envelope["payload"]["batch_id"] = "changed-batch"
    changed = canonical_json_bytes(envelope)

    with pytest.raises(SerializationError) as captured:
        parse_reconciliation_report_document(
            changed,
            expected_fingerprint=document.fingerprint,
        )

    assert captured.value.code == "fingerprint.mismatch"


def test_report_parser_rejects_a_different_schema():
    document = SchemaDocument.build(
        SchemaRef.parse("ledgerloom.other_report", "1.0"),
        {},
    )

    with pytest.raises(SerializationError) as captured:
        parse_reconciliation_report_document(document.content)

    assert captured.value.code == "report.schema_mismatch"


def test_report_decoder_rechecks_summary_invariants(mixed_batch):
    payload = encode_reconciliation_report(build_reconciliation_report(mixed_batch))
    payload["summary"]["matched"] = 5

    with pytest.raises(SerializationError) as captured:
        decode_reconciliation_report(payload)

    assert captured.value.code == "report.payload_invalid"
