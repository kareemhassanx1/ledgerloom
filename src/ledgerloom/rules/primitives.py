"""Built-in safeguards for comparing normalized ledger entries."""

from __future__ import annotations

from dataclasses import dataclass

from ledgerloom.domain.records import LedgerEntry
from ledgerloom.rules.contracts import require_entries
from ledgerloom.rules.evidence import (
    EvidenceFact,
    MatchVerdict,
    RuleEvidence,
    validate_rule_code,
)


@dataclass(frozen=True, slots=True)
class DifferentEntryRule:
    """Prevent an entry from being reconciled with itself."""

    rule_id: str = "ledgerloom.different_entry"

    def __post_init__(self) -> None:
        validate_rule_code(self.rule_id, path="$.rule.rule_id", kind="id")

    def evaluate(self, left: LedgerEntry, right: LedgerEntry) -> RuleEvidence:
        left, right = require_entries(left, right)
        distinct = left.entry_id != right.entry_id
        return RuleEvidence(
            self.rule_id,
            MatchVerdict.MATCH if distinct else MatchVerdict.NO_MATCH,
            "entry.distinct" if distinct else "entry.same",
            facts=(
                EvidenceFact("left_entry_id", str(left.entry_id)),
                EvidenceFact("right_entry_id", str(right.entry_id)),
            ),
        )


@dataclass(frozen=True, slots=True)
class SameAccountRule:
    """Require both entries to belong to the same normalized account."""

    rule_id: str = "ledgerloom.same_account"

    def __post_init__(self) -> None:
        validate_rule_code(self.rule_id, path="$.rule.rule_id", kind="id")

    def evaluate(self, left: LedgerEntry, right: LedgerEntry) -> RuleEvidence:
        left, right = require_entries(left, right)
        same = left.account_id == right.account_id
        return RuleEvidence(
            self.rule_id,
            MatchVerdict.MATCH if same else MatchVerdict.NO_MATCH,
            "account.same" if same else "account.different",
            facts=(
                EvidenceFact("left_account_id", str(left.account_id)),
                EvidenceFact("right_account_id", str(right.account_id)),
            ),
        )


@dataclass(frozen=True, slots=True)
class DifferentSourceRule:
    """Require entries to originate from different upstream sources."""

    rule_id: str = "ledgerloom.different_source"

    def __post_init__(self) -> None:
        validate_rule_code(self.rule_id, path="$.rule.rule_id", kind="id")

    def evaluate(self, left: LedgerEntry, right: LedgerEntry) -> RuleEvidence:
        left, right = require_entries(left, right)
        distinct = left.source_id != right.source_id
        return RuleEvidence(
            self.rule_id,
            MatchVerdict.MATCH if distinct else MatchVerdict.NO_MATCH,
            "source.distinct" if distinct else "source.same",
            facts=(
                EvidenceFact("left_source_id", str(left.source_id)),
                EvidenceFact("right_source_id", str(right.source_id)),
            ),
        )
