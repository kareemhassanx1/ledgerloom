import pytest

from ledgerloom.reconciliation import (
    RECONCILIATION_REPORT_V1,
    ReconciliationReportError,
    build_reconciliation_report,
    build_reconciliation_report_document,
    encode_reconciliation_report,
)
from ledgerloom.serialization import ContentFingerprint, loads_canonical_json


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
    matched = next(
        item for item in payload["decisions"] if item["anchor_id"] == "anchor-match"
    )

    assert payload["policy"]["max_time_difference_microseconds"] == "300000000"
    assert matched["selection"]["evaluated"] == 1
    assert matched["candidates"][0]["evidence"][0]["rule_id"] == (
        "ledgerloom.same_account"
    )


def test_equivalent_reports_produce_identical_documents(mixed_batch):
    first = build_reconciliation_report_document(
        build_reconciliation_report(mixed_batch)
    )
    second = build_reconciliation_report_document(
        build_reconciliation_report(mixed_batch)
    )

    assert second.content == first.content
    assert second.fingerprint == first.fingerprint


def test_report_encoder_rejects_non_report_values():
    with pytest.raises(ReconciliationReportError) as captured:
        encode_reconciliation_report(object())  # type: ignore[arg-type]

    assert captured.value.code == "report.encode_type"
