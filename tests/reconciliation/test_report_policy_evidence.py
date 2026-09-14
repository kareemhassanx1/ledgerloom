from dataclasses import replace

import pytest

from ledgerloom.domain import BatchId
from ledgerloom.reconciliation import (
    RECONCILIATION_REPORT_V1,
    CandidateIndex,
    ReconciliationReportError,
    build_reconciliation_report,
    build_reconciliation_report_document,
    parse_reconciliation_report_document,
)
from ledgerloom.serialization.errors import SerializationError
from ledgerloom.serialization.schema import SchemaDocument


def _report(make_entry, reconciler):
    return build_reconciliation_report(
        reconciler.reconcile(
            BatchId("review"), (make_entry("anchor"),), CandidateIndex((make_entry("candidate"),))
        )
    )


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("rule_order", "report.policy_rule_mismatch"),
        ("rule_identity", "report.policy_rule_mismatch"),
        ("required_rules", "report.policy_required_mismatch"),
        ("maximum_scale", "report.policy_maximum_mismatch"),
        ("redistributed_weights", "report.policy_score_mismatch"),
        ("candidate_score", "report.policy_score_mismatch"),
    ],
)
def test_report_must_agree_with_its_retained_scoring_policy(
    make_entry,
    batch_reconciler,
    change,
    code,
):
    report = _report(make_entry, batch_reconciler)
    policy = report.policy_snapshot
    rules = list(policy.scoring_rules)
    decisions = report.decisions
    if change == "rule_order":
        rules.reverse()
    elif change == "rule_identity":
        rules[0] = replace(rules[0], rule_id="custom.changed")
    elif change == "required_rules":
        rules[0] = replace(rules[0], required=False)
    elif change == "maximum_scale":
        rules[0] = replace(rules[0], weight=rules[0].weight + 1)
    elif change == "redistributed_weights":
        rules[1] = replace(rules[1], weight=rules[1].weight + 1)
        rules[2] = replace(rules[2], weight=rules[2].weight - 1)
    else:
        candidate = decisions[0].candidates[0]
        lowered = candidate.score - 1
        altered = replace(
            candidate,
            score=lowered,
            confidence_basis_points=lowered * 10_000 // candidate.maximum_score,
        )
        decisions = (replace(decisions[0], candidates=(altered,)),)

    with pytest.raises(ReconciliationReportError) as captured:
        replace(
            report, decisions=decisions, policy_snapshot=replace(policy, scoring_rules=tuple(rules))
        )

    assert captured.value.code == code
    assert captured.value.path.startswith("$.report.decisions[0].candidates[0].")


def test_rehashing_a_document_does_not_validate_altered_policy(make_entry, batch_reconciler):
    original = build_reconciliation_report_document(_report(make_entry, batch_reconciler))
    payload = original.payload
    payload["policy"]["scoring_rules"][0]["weight"] += 1
    changed = SchemaDocument.build(RECONCILIATION_REPORT_V1, payload)
    assert changed.fingerprint != original.fingerprint

    with pytest.raises(SerializationError) as captured:
        parse_reconciliation_report_document(
            changed.content, expected_fingerprint=changed.fingerprint
        )
    assert captured.value.code == "report.payload_invalid"
    assert captured.value.path == "$.payload"
    assert isinstance(captured.value.__cause__, ReconciliationReportError)
    assert captured.value.__cause__.code == "report.policy_maximum_mismatch"


def test_legacy_report_without_policy_keeps_its_declared_contract(make_entry, batch_reconciler):
    report = replace(_report(make_entry, batch_reconciler), policy_snapshot=None)
    document = build_reconciliation_report_document(report)

    assert parse_reconciliation_report_document(document.content) == report
