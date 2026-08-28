"""Composable matching rules with deterministic structured evidence."""

from ledgerloom.rules.composite import AllOfRule, AnyOfRule, NotRule
from ledgerloom.rules.contracts import MatchRule, evaluate_rule
from ledgerloom.rules.errors import RuleError
from ledgerloom.rules.evidence import EvidenceFact, MatchVerdict, RuleEvidence
from ledgerloom.rules.primitives import (
    AmountRule,
    DifferentEntryRule,
    DifferentSourceRule,
    ReferenceRule,
    SameAccountRule,
    TimeWindowRule,
)

__all__ = [
    "AllOfRule",
    "AmountRule",
    "AnyOfRule",
    "DifferentEntryRule",
    "DifferentSourceRule",
    "EvidenceFact",
    "MatchRule",
    "MatchVerdict",
    "NotRule",
    "ReferenceRule",
    "RuleError",
    "RuleEvidence",
    "SameAccountRule",
    "TimeWindowRule",
    "evaluate_rule",
]
