"""Canonical version 1 documents for reconciliation reports."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from ledgerloom.domain.identifiers import BatchId, EntryId
from ledgerloom.reconciliation.batches import (
    ReconciliationPolicySnapshot,
    ScoringRuleSnapshot,
)
from ledgerloom.reconciliation.decisions import ConflictStrategy, DecisionStatus
from ledgerloom.reconciliation.errors import ReconciliationReportError
from ledgerloom.reconciliation.reports import (
    CandidateReport,
    DecisionReport,
    ReconciliationReport,
    ReconciliationSummary,
    SelectionReport,
)
from ledgerloom.rules import EvidenceFact, MatchVerdict, RuleEvidence
from ledgerloom.serialization.canonical import JsonValue
from ledgerloom.serialization.errors import SerializationError
from ledgerloom.serialization.fingerprints import ContentFingerprint
from ledgerloom.serialization.schema import SchemaDocument, SchemaRef

RECONCILIATION_REPORT_V1 = SchemaRef.parse("ledgerloom.reconciliation_report", "1.0")
_MAX_EVIDENCE_DEPTH = 64


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


def _object(
    value: JsonValue,
    *,
    path: str,
    fields: frozenset[str],
) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise SerializationError(
            code="report.object_type",
            path=path,
            message="must be an object",
        )
    actual = frozenset(value)
    if actual != fields:
        raise SerializationError(
            code="report.object_fields",
            path=path,
            message="object fields do not match the version 1 report schema",
        )
    return value


def _array(value: JsonValue, *, path: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise SerializationError(
            code="report.array_type",
            path=path,
            message="must be an array",
        )
    return value


def _text(value: JsonValue, *, path: str) -> str:
    if not isinstance(value, str):
        raise SerializationError(
            code="report.text_type",
            path=path,
            message="must be text",
        )
    return value


def _integer(value: JsonValue, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SerializationError(
            code="report.integer_type",
            path=path,
            message="must be an integer",
        )
    return value


def _boolean(value: JsonValue, *, path: str) -> bool:
    if not isinstance(value, bool):
        raise SerializationError(
            code="report.boolean_type",
            path=path,
            message="must be a boolean",
        )
    return value


def _construct[T](factory: Callable[[], T], *, path: str) -> T:
    try:
        return factory()
    except SerializationError:
        raise
    except (ValueError, TypeError, OverflowError) as error:
        raise SerializationError(
            code="report.payload_invalid",
            path=path,
            message="value violates reconciliation report invariants",
        ) from error


def _entry_id(value: JsonValue, *, path: str) -> EntryId:
    text = _text(value, path=path)
    return _construct(lambda: EntryId(text), path=path)


def _string_tuple(value: JsonValue, *, path: str) -> tuple[str, ...]:
    return tuple(
        _text(item, path=f"{path}[{index}]")
        for index, item in enumerate(_array(value, path=path))
    )


def _entry_id_tuple(value: JsonValue, *, path: str) -> tuple[EntryId, ...]:
    return tuple(
        _entry_id(item, path=f"{path}[{index}]")
        for index, item in enumerate(_array(value, path=path))
    )


def _decode_evidence(
    value: JsonValue,
    *,
    path: str,
    depth: int = 0,
) -> RuleEvidence:
    if depth > _MAX_EVIDENCE_DEPTH:
        raise SerializationError(
            code="report.evidence_depth",
            path=path,
            message=f"evidence nesting must not exceed {_MAX_EVIDENCE_DEPTH} levels",
        )
    item = _object(
        value,
        path=path,
        fields=frozenset({"children", "facts", "reason_code", "rule_id", "verdict"}),
    )
    facts: list[EvidenceFact] = []
    for index, fact_value in enumerate(_array(item["facts"], path=f"{path}.facts")):
        fact_path = f"{path}.facts[{index}]"
        fact = _object(
            fact_value,
            path=fact_path,
            fields=frozenset({"name", "value"}),
        )
        facts.append(
            _construct(
                lambda fact=fact, fact_path=fact_path: EvidenceFact(
                    _text(fact["name"], path=f"{fact_path}.name"),
                    fact["value"],  # type: ignore[arg-type]
                ),
                path=fact_path,
            )
        )
    children = tuple(
        _decode_evidence(
            child,
            path=f"{path}.children[{index}]",
            depth=depth + 1,
        )
        for index, child in enumerate(_array(item["children"], path=f"{path}.children"))
    )
    return _construct(
        lambda: RuleEvidence(
            _text(item["rule_id"], path=f"{path}.rule_id"),
            MatchVerdict(_text(item["verdict"], path=f"{path}.verdict")),
            _text(item["reason_code"], path=f"{path}.reason_code"),
            tuple(facts),
            children,
        ),
        path=path,
    )


def _decode_candidate(value: JsonValue, *, path: str) -> CandidateReport:
    item = _object(
        value,
        path=path,
        fields=frozenset(
            {
                "candidate_id",
                "confidence_basis_points",
                "evidence",
                "maximum_score",
                "reason_code",
                "required_rule_ids",
                "required_rules_satisfied",
                "score",
            }
        ),
    )
    evidence = tuple(
        _decode_evidence(child, path=f"{path}.evidence[{index}]")
        for index, child in enumerate(_array(item["evidence"], path=f"{path}.evidence"))
    )
    return _construct(
        lambda: CandidateReport(
            candidate_id=_entry_id(item["candidate_id"], path=f"{path}.candidate_id"),
            score=_integer(item["score"], path=f"{path}.score"),
            maximum_score=_integer(
                item["maximum_score"], path=f"{path}.maximum_score"
            ),
            confidence_basis_points=_integer(
                item["confidence_basis_points"],
                path=f"{path}.confidence_basis_points",
            ),
            required_rule_ids=_string_tuple(
                item["required_rule_ids"], path=f"{path}.required_rule_ids"
            ),
            required_rules_satisfied=_boolean(
                item["required_rules_satisfied"],
                path=f"{path}.required_rules_satisfied",
            ),
            reason_code=_text(item["reason_code"], path=f"{path}.reason_code"),
            evidence=evidence,
        ),
        path=path,
    )


def _decode_selection(value: JsonValue, *, path: str) -> SelectionReport | None:
    if value is None:
        return None
    item = _object(
        value,
        path=path,
        fields=frozenset(
            {
                "account",
                "amount",
                "evaluated",
                "excluded_batch_anchors",
                "indexed",
                "reference",
                "time",
            }
        ),
    )
    return _construct(
        lambda: SelectionReport(
            indexed=_integer(item["indexed"], path=f"{path}.indexed"),
            account=_integer(item["account"], path=f"{path}.account"),
            amount=_integer(item["amount"], path=f"{path}.amount"),
            time=_integer(item["time"], path=f"{path}.time"),
            reference=_integer(item["reference"], path=f"{path}.reference"),
            excluded_batch_anchors=_integer(
                item["excluded_batch_anchors"],
                path=f"{path}.excluded_batch_anchors",
            ),
            evaluated=_integer(item["evaluated"], path=f"{path}.evaluated"),
        ),
        path=path,
    )


def _decode_decision(value: JsonValue, *, path: str) -> DecisionReport:
    item = _object(
        value,
        path=path,
        fields=frozenset(
            {
                "anchor_id",
                "candidates",
                "competing_anchor_ids",
                "contender_ids",
                "reason_code",
                "selected_candidate_id",
                "selection",
                "status",
            }
        ),
    )
    selected_value = item["selected_candidate_id"]
    selected = (
        None
        if selected_value is None
        else _entry_id(selected_value, path=f"{path}.selected_candidate_id")
    )
    candidates = tuple(
        _decode_candidate(child, path=f"{path}.candidates[{index}]")
        for index, child in enumerate(_array(item["candidates"], path=f"{path}.candidates"))
    )
    return _construct(
        lambda: DecisionReport(
            anchor_id=_entry_id(item["anchor_id"], path=f"{path}.anchor_id"),
            status=DecisionStatus(_text(item["status"], path=f"{path}.status")),
            reason_code=_text(item["reason_code"], path=f"{path}.reason_code"),
            selected_candidate_id=selected,
            contender_ids=_entry_id_tuple(
                item["contender_ids"], path=f"{path}.contender_ids"
            ),
            competing_anchor_ids=_entry_id_tuple(
                item["competing_anchor_ids"], path=f"{path}.competing_anchor_ids"
            ),
            candidates=candidates,
            selection_report=_decode_selection(
                item["selection"], path=f"{path}.selection"
            ),
        ),
        path=path,
    )


def _parse_decimal(value: JsonValue, *, path: str) -> Decimal:
    text = _text(value, path=path)
    try:
        parsed = Decimal(text)
    except InvalidOperation as error:
        raise SerializationError(
            code="report.decimal_format",
            path=path,
            message="must be canonical decimal text",
        ) from error
    if _decimal_text(parsed) != text:
        raise SerializationError(
            code="report.decimal_format",
            path=path,
            message="must be canonical decimal text",
        )
    return parsed


def _parse_duration(value: JsonValue, *, path: str) -> timedelta:
    text = _text(value, path=path)
    if not text.isascii() or not text.isdecimal() or (len(text) > 1 and text[0] == "0"):
        raise SerializationError(
            code="report.duration_format",
            path=path,
            message="must be canonical non-negative integer microseconds",
        )
    return _construct(lambda: timedelta(microseconds=int(text)), path=path)


def _decode_policy(
    value: JsonValue,
    *,
    path: str,
) -> ReconciliationPolicySnapshot | None:
    if value is None:
        return None
    item = _object(
        value,
        path=path,
        fields=frozenset(
            {
                "conflict_strategy",
                "max_amount_difference",
                "max_time_difference_microseconds",
                "minimum_margin",
                "minimum_score",
                "require_reference",
                "scoring_rules",
            }
        ),
    )
    rules: list[ScoringRuleSnapshot] = []
    for index, rule_value in enumerate(
        _array(item["scoring_rules"], path=f"{path}.scoring_rules")
    ):
        rule_path = f"{path}.scoring_rules[{index}]"
        rule = _object(
            rule_value,
            path=rule_path,
            fields=frozenset({"required", "rule_id", "weight"}),
        )
        rules.append(
            _construct(
                lambda rule=rule, rule_path=rule_path: ScoringRuleSnapshot(
                    _text(rule["rule_id"], path=f"{rule_path}.rule_id"),
                    _integer(rule["weight"], path=f"{rule_path}.weight"),
                    _boolean(rule["required"], path=f"{rule_path}.required"),
                ),
                path=rule_path,
            )
        )
    return _construct(
        lambda: ReconciliationPolicySnapshot(
            scoring_rules=tuple(rules),
            minimum_score=_integer(
                item["minimum_score"], path=f"{path}.minimum_score"
            ),
            minimum_margin=_integer(
                item["minimum_margin"], path=f"{path}.minimum_margin"
            ),
            max_time_difference=_parse_duration(
                item["max_time_difference_microseconds"],
                path=f"{path}.max_time_difference_microseconds",
            ),
            max_amount_difference=_parse_decimal(
                item["max_amount_difference"], path=f"{path}.max_amount_difference"
            ),
            require_reference=_boolean(
                item["require_reference"], path=f"{path}.require_reference"
            ),
            conflict_strategy=ConflictStrategy(
                _text(item["conflict_strategy"], path=f"{path}.conflict_strategy")
            ),
        ),
        path=path,
    )


def _decode_summary(value: JsonValue, *, path: str) -> ReconciliationSummary:
    item = _object(
        value,
        path=path,
        fields=frozenset(
            {
                "ambiguous",
                "conflict",
                "coverage_basis_points",
                "evaluated_candidates",
                "matched",
                "total",
                "unmatched",
            }
        ),
    )
    return _construct(
        lambda: ReconciliationSummary(
            total=_integer(item["total"], path=f"{path}.total"),
            matched=_integer(item["matched"], path=f"{path}.matched"),
            unmatched=_integer(item["unmatched"], path=f"{path}.unmatched"),
            ambiguous=_integer(item["ambiguous"], path=f"{path}.ambiguous"),
            conflict=_integer(item["conflict"], path=f"{path}.conflict"),
            evaluated_candidates=_integer(
                item["evaluated_candidates"], path=f"{path}.evaluated_candidates"
            ),
            coverage_basis_points=_integer(
                item["coverage_basis_points"], path=f"{path}.coverage_basis_points"
            ),
        ),
        path=path,
    )


def decode_reconciliation_report(value: JsonValue) -> ReconciliationReport:
    """Decode a version 1 payload and recheck every report invariant."""

    payload = _object(
        value,
        path="$.payload",
        fields=frozenset({"batch_id", "decisions", "policy", "summary"}),
    )
    decisions = tuple(
        _decode_decision(item, path=f"$.payload.decisions[{index}]")
        for index, item in enumerate(
            _array(payload["decisions"], path="$.payload.decisions")
        )
    )
    return _construct(
        lambda: ReconciliationReport(
            batch_id=_construct(
                lambda: BatchId(_text(payload["batch_id"], path="$.payload.batch_id")),
                path="$.payload.batch_id",
            ),
            summary=_decode_summary(payload["summary"], path="$.payload.summary"),
            decisions=decisions,
            policy_snapshot=_decode_policy(payload["policy"], path="$.payload.policy"),
        ),
        path="$.payload",
    )


def parse_reconciliation_report_document(
    content: bytes,
    *,
    expected_fingerprint: ContentFingerprint | None = None,
) -> ReconciliationReport:
    """Verify canonical bytes, schema and fingerprint before decoding a report."""

    document = SchemaDocument.parse(
        content,
        expected_fingerprint=expected_fingerprint,
    )
    if document.schema != RECONCILIATION_REPORT_V1:
        raise SerializationError(
            code="report.schema_mismatch",
            path="$.schema",
            message=f"expected {RECONCILIATION_REPORT_V1}",
        )
    return decode_reconciliation_report(document.payload)
