"""Composable matching rules with deterministic structured evidence."""

from ledgerloom.rules.composite import AllOfRule, AnyOfRule, NotRule
from ledgerloom.rules.contracts import MatchRule, evaluate_rule
from ledgerloom.rules.errors import RuleError
from ledgerloom.rules.evidence import EvidenceFact, MatchVerdict, RuleEvidence
from ledgerloom.rules.primitives import DifferentEntryRule, DifferentSourceRule, SameAccountRule

__all__ = [
    "AllOfRule",
    "AnyOfRule",
    "DifferentEntryRule",
    "DifferentSourceRule",
    "EvidenceFact",
    "MatchRule",
    "MatchVerdict",
    "NotRule",
    "RuleError",
    "RuleEvidence",
    "SameAccountRule",
    "evaluate_rule",
]
