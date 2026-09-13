"""Deterministic boolean composition that retains every child explanation."""

from __future__ import annotations

from dataclasses import dataclass

from ledgerloom.domain.records import LedgerEntry
from ledgerloom.rules.contracts import MatchRule, evaluate_rule, require_entries
from ledgerloom.rules.errors import RuleError
from ledgerloom.rules.evidence import (
    EvidenceFact,
    MatchVerdict,
    RuleEvidence,
    validate_rule_code,
)


def _validate_children(rule_id: str, rules: object) -> tuple[MatchRule, ...]:
    validate_rule_code(rule_id, path="$.rule.rule_id", kind="id")
    if not isinstance(rules, tuple):
        raise RuleError(
            code="rule.children_type",
            path="$.rule.rules",
            message="composite rules must be a tuple",
        )
    if not rules:
        raise RuleError(
            code="rule.children_empty",
            path="$.rule.rules",
            message="composite rule must contain at least one child",
        )
    if not all(isinstance(rule, MatchRule) for rule in rules):
        raise RuleError(
            code="rule.child_type",
            path="$.rule.rules",
            message="composite children must satisfy MatchRule",
        )
    child_ids = tuple(
        validate_rule_code(rule.rule_id, path="$.rule.rules.rule_id", kind="id") for rule in rules
    )
    if len(child_ids) != len(set(child_ids)):
        raise RuleError(
            code="rule.child_id_duplicate",
            path="$.rule.rules",
            message="composite child rule identifiers must be unique",
        )
    if rule_id in child_ids:
        raise RuleError(
            code="rule.child_id_recursive",
            path="$.rule.rules",
            message="composite cannot directly contain its own rule identifier",
        )
    return rules


def _counts(children: tuple[RuleEvidence, ...]) -> tuple[EvidenceFact, ...]:
    return (
        EvidenceFact(
            "indeterminate_count",
            sum(child.verdict is MatchVerdict.INDETERMINATE for child in children),
        ),
        EvidenceFact(
            "match_count",
            sum(child.verdict is MatchVerdict.MATCH for child in children),
        ),
        EvidenceFact(
            "no_match_count",
            sum(child.verdict is MatchVerdict.NO_MATCH for child in children),
        ),
    )


@dataclass(frozen=True, slots=True)
class AllOfRule:
    """Match only when every child matches, while evaluating every child."""

    rule_id: str
    rules: tuple[MatchRule, ...]

    def __post_init__(self) -> None:
        _validate_children(self.rule_id, self.rules)

    def evaluate(self, left: LedgerEntry, right: LedgerEntry) -> RuleEvidence:
        left, right = require_entries(left, right)
        children = tuple(evaluate_rule(rule, left, right) for rule in self.rules)
        verdicts = tuple(child.verdict for child in children)
        if MatchVerdict.NO_MATCH in verdicts:
            verdict = MatchVerdict.NO_MATCH
            reason = "composite.child_rejected"
        elif MatchVerdict.INDETERMINATE in verdicts:
            verdict = MatchVerdict.INDETERMINATE
            reason = "composite.child_indeterminate"
        else:
            verdict = MatchVerdict.MATCH
            reason = "composite.all_matched"
        return RuleEvidence(
            self.rule_id,
            verdict,
            reason,
            facts=_counts(children),
            children=children,
        )


@dataclass(frozen=True, slots=True)
class AnyOfRule:
    """Match when any child matches, retaining unsuccessful alternatives."""

    rule_id: str
    rules: tuple[MatchRule, ...]

    def __post_init__(self) -> None:
        _validate_children(self.rule_id, self.rules)

    def evaluate(self, left: LedgerEntry, right: LedgerEntry) -> RuleEvidence:
        left, right = require_entries(left, right)
        children = tuple(evaluate_rule(rule, left, right) for rule in self.rules)
        verdicts = tuple(child.verdict for child in children)
        if MatchVerdict.MATCH in verdicts:
            verdict = MatchVerdict.MATCH
            reason = "composite.child_matched"
        elif MatchVerdict.INDETERMINATE in verdicts:
            verdict = MatchVerdict.INDETERMINATE
            reason = "composite.child_indeterminate"
        else:
            verdict = MatchVerdict.NO_MATCH
            reason = "composite.none_matched"
        return RuleEvidence(
            self.rule_id,
            verdict,
            reason,
            facts=_counts(children),
            children=children,
        )


@dataclass(frozen=True, slots=True)
class NotRule:
    """Invert a child match without discarding the child's explanation."""

    rule_id: str
    rule: MatchRule

    def __post_init__(self) -> None:
        validate_rule_code(self.rule_id, path="$.rule.rule_id", kind="id")
        if not isinstance(self.rule, MatchRule):
            raise RuleError(
                code="rule.child_type",
                path="$.rule.rule",
                message="negated child must satisfy MatchRule",
            )
        child_id = validate_rule_code(
            self.rule.rule_id,
            path="$.rule.rule.rule_id",
            kind="id",
        )
        if child_id == self.rule_id:
            raise RuleError(
                code="rule.child_id_recursive",
                path="$.rule.rule",
                message="negated rule cannot directly contain its own rule identifier",
            )

    def evaluate(self, left: LedgerEntry, right: LedgerEntry) -> RuleEvidence:
        left, right = require_entries(left, right)
        child = evaluate_rule(self.rule, left, right)
        if child.verdict is MatchVerdict.MATCH:
            verdict = MatchVerdict.NO_MATCH
            reason = "composite.negated_match"
        elif child.verdict is MatchVerdict.NO_MATCH:
            verdict = MatchVerdict.MATCH
            reason = "composite.negated_no_match"
        else:
            verdict = MatchVerdict.INDETERMINATE
            reason = "composite.negated_indeterminate"
        return RuleEvidence(
            self.rule_id,
            verdict,
            reason,
            children=(child,),
        )
