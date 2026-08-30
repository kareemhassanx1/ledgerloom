"""Canonical version 1 documents for reconciliation reports."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from ledgerloom.reconciliation.batches import ReconciliationPolicySnapshot
from ledgerloom.reconciliation.errors import ReconciliationReportError
from ledgerloom.reconciliation.reports import (
    CandidateReport,
    DecisionReport,
    ReconciliationReport,
    SelectionReport,
)
from ledgerloom.rules import RuleEvidence
from ledgerloom.serialization.canonical import JsonValue
from ledgerloom.serialization.schema import SchemaDocument, SchemaRef

RECONCILIATION_REPORT_V1 = SchemaRef.parse("ledgerloom.reconciliation_report", "1.0")


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _timedelta_microseconds(value: timedelta) -> str:
    return str(
        value.days * 86_400_000_000
        + value.seconds * 1_000_000
        + value.microseconds
    )


def _encode_evidence(evidence: RuleEvidence) -> dict[str, JsonValue]:
    return {
        "children": [_encode_evidence(item) for item in evidence.children],
        "facts": [
            {"name": item.name, "value": item.value} for item in evidence.facts
        ],
        "reason_code": evidence.reason_code,
        "rule_id": evidence.rule_id,
        "verdict": str(evidence.verdict),
    }


def _encode_candidate(candidate: CandidateReport) -> dict[str, JsonValue]:
    return {
        "candidate_id": str(candidate.candidate_id),
        "confidence_basis_points": candidate.confidence_basis_points,
        "evidence": [_encode_evidence(item) for item in candidate.evidence],
        "maximum_score": candidate.maximum_score,
        "reason_code": candidate.reason_code,
        "required_rule_ids": list(candidate.required_rule_ids),
        "required_rules_satisfied": candidate.required_rules_satisfied,
        "score": candidate.score,
    }


def _encode_selection(selection: SelectionReport | None) -> JsonValue:
    if selection is None:
        return None
    return {
        "account": selection.account,
        "amount": selection.amount,
        "evaluated": selection.evaluated,
        "excluded_batch_anchors": selection.excluded_batch_anchors,
        "indexed": selection.indexed,
        "reference": selection.reference,
        "time": selection.time,
    }


def _encode_decision(decision: DecisionReport) -> dict[str, JsonValue]:
    return {
        "anchor_id": str(decision.anchor_id),
        "candidates": [_encode_candidate(item) for item in decision.candidates],
        "competing_anchor_ids": [str(item) for item in decision.competing_anchor_ids],
        "contender_ids": [str(item) for item in decision.contender_ids],
        "reason_code": decision.reason_code,
        "selected_candidate_id": (
            None
            if decision.selected_candidate_id is None
            else str(decision.selected_candidate_id)
        ),
        "selection": _encode_selection(decision.selection_report),
        "status": str(decision.status),
    }


def _encode_policy(policy: ReconciliationPolicySnapshot | None) -> JsonValue:
    if policy is None:
        return None
    return {
        "conflict_strategy": str(policy.conflict_strategy),
        "max_amount_difference": _decimal_text(policy.max_amount_difference),
        "max_time_difference_microseconds": _timedelta_microseconds(
            policy.max_time_difference
        ),
        "minimum_margin": policy.minimum_margin,
        "minimum_score": policy.minimum_score,
        "require_reference": policy.require_reference,
        "scoring_rules": [
            {
                "required": item.required,
                "rule_id": item.rule_id,
                "weight": item.weight,
            }
            for item in policy.scoring_rules
        ],
    }


def encode_reconciliation_report(report: ReconciliationReport) -> dict[str, JsonValue]:
    """Encode a verified report as a stable version 1 JSON payload."""

    if not isinstance(report, ReconciliationReport):
        raise ReconciliationReportError(
            code="report.encode_type",
            path="$.report",
            message="report must be a ReconciliationReport",
        )
    summary = report.summary
    return {
        "batch_id": str(report.batch_id),
        "decisions": [_encode_decision(item) for item in report.decisions],
        "policy": _encode_policy(report.policy_snapshot),
        "summary": {
            "ambiguous": summary.ambiguous,
            "conflict": summary.conflict,
            "coverage_basis_points": summary.coverage_basis_points,
            "evaluated_candidates": summary.evaluated_candidates,
            "matched": summary.matched,
            "total": summary.total,
            "unmatched": summary.unmatched,
        },
    }


def build_reconciliation_report_document(
    report: ReconciliationReport,
) -> SchemaDocument:
    """Build canonical bytes and a SHA-256 fingerprint for one report."""

    return SchemaDocument.build(
        RECONCILIATION_REPORT_V1,
        encode_reconciliation_report(report),
    )
