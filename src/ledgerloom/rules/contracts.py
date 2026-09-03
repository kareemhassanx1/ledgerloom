"""Public contracts and guarded evaluation for matching rules."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ledgerloom.domain.records import LedgerEntry
from ledgerloom.rules.errors import RuleError
from ledgerloom.rules.evidence import RuleEvidence, validate_rule_code


@runtime_checkable
class MatchRule(Protocol):
    """A deterministic comparison that explains its result."""

    @property
    def rule_id(self) -> str:
        """Return the stable identifier included in emitted evidence."""

    def evaluate(self, left: LedgerEntry, right: LedgerEntry) -> RuleEvidence:
        """Compare two entries and return structured evidence."""


def require_entries(left: object, right: object) -> tuple[LedgerEntry, LedgerEntry]:
    """Validate operands consistently for built-in and composite rules."""

    if not isinstance(left, LedgerEntry):
        raise RuleError(
            code="rule.left_type",
            path="$.left",
            message="left operand must be a LedgerEntry",
        )
    if not isinstance(right, LedgerEntry):
        raise RuleError(
            code="rule.right_type",
            path="$.right",
            message="right operand must be a LedgerEntry",
        )
    return left, right


def evaluate_rule(
    rule: MatchRule,
    left: LedgerEntry,
    right: LedgerEntry,
) -> RuleEvidence:
    """Evaluate a structural rule and enforce its observable result contract."""

    left, right = require_entries(left, right)
    if not isinstance(rule, MatchRule):
        raise RuleError(
            code="rule.type",
            path="$.rule",
            message="rule must provide a rule_id and callable evaluate method",
        )
    rule_id = validate_rule_code(rule.rule_id, path="$.rule.rule_id", kind="id")
    evidence = rule.evaluate(left, right)
    if not isinstance(evidence, RuleEvidence):
        raise RuleError(
            code="rule.result_type",
            path="$.rule.result",
            message="rule evaluate method must return RuleEvidence",
        )
    if evidence.rule_id != rule_id:
        raise RuleError(
            code="rule.result_id_mismatch",
            path="$.rule.result.rule_id",
            message="evidence rule_id must match the evaluated rule",
        )
    return evidence
