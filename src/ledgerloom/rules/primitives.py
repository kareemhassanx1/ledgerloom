"""Built-in safeguards for comparing normalized ledger entries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext

from ledgerloom.domain.records import LedgerEntry
from ledgerloom.rules.contracts import require_entries
from ledgerloom.rules.errors import RuleError
from ledgerloom.rules.evidence import (
    EvidenceFact,
    MatchVerdict,
    RuleEvidence,
    validate_rule_code,
)


def _canonical_decimal(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _timedelta_microseconds(value: timedelta) -> int:
    return ((value.days * 86_400) + value.seconds) * 1_000_000 + value.microseconds


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


@dataclass(frozen=True, slots=True)
class AmountRule:
    """Require equal currencies and amounts within an exact decimal tolerance."""

    max_difference: Decimal = Decimal("0")
    rule_id: str = "ledgerloom.amount"

    def __post_init__(self) -> None:
        validate_rule_code(self.rule_id, path="$.rule.rule_id", kind="id")
        if not isinstance(self.max_difference, Decimal):
            raise RuleError(
                code="rule.amount_tolerance_type",
                path="$.rule.max_difference",
                message="amount tolerance must be a Decimal",
            )
        if not self.max_difference.is_finite() or self.max_difference < 0:
            raise RuleError(
                code="rule.amount_tolerance_range",
                path="$.rule.max_difference",
                message="amount tolerance must be finite and non-negative",
            )
        _, digits, exponent = self.max_difference.as_tuple()
        assert isinstance(exponent, int)
        integer_digits = max(len(digits) + exponent, 0)
        if len(digits) > 38 or max(-exponent, 0) > 18 or integer_digits > 38:
            raise RuleError(
                code="rule.amount_tolerance_precision",
                path="$.rule.max_difference",
                message="amount tolerance exceeds the supported decimal precision",
            )

    def evaluate(self, left: LedgerEntry, right: LedgerEntry) -> RuleEvidence:
        left, right = require_entries(left, right)
        common_facts = (
            EvidenceFact("left_amount", left.amount.canonical_amount()),
            EvidenceFact("left_currency", left.amount.currency.code),
            EvidenceFact("max_difference", _canonical_decimal(self.max_difference)),
            EvidenceFact("right_amount", right.amount.canonical_amount()),
            EvidenceFact("right_currency", right.amount.currency.code),
        )
        if left.amount.currency != right.amount.currency:
            return RuleEvidence(
                self.rule_id,
                MatchVerdict.NO_MATCH,
                "amount.currency_different",
                facts=(*common_facts, EvidenceFact("difference", None)),
            )

        with localcontext() as context:
            context.prec = 76
            difference = abs(left.amount.amount - right.amount.amount)
        within_tolerance = difference <= self.max_difference
        return RuleEvidence(
            self.rule_id,
            MatchVerdict.MATCH if within_tolerance else MatchVerdict.NO_MATCH,
            "amount.within_tolerance" if within_tolerance else "amount.outside_tolerance",
            facts=(*common_facts, EvidenceFact("difference", _canonical_decimal(difference))),
        )


@dataclass(frozen=True, slots=True)
class ReferenceRule:
    """Require exact reference text, preserving uncertainty when either is missing."""

    rule_id: str = "ledgerloom.reference"

    def __post_init__(self) -> None:
        validate_rule_code(self.rule_id, path="$.rule.rule_id", kind="id")

    def evaluate(self, left: LedgerEntry, right: LedgerEntry) -> RuleEvidence:
        left, right = require_entries(left, right)
        facts = (
            EvidenceFact("left_reference", left.reference),
            EvidenceFact("right_reference", right.reference),
        )
        if left.reference is None or right.reference is None:
            return RuleEvidence(
                self.rule_id,
                MatchVerdict.INDETERMINATE,
                "reference.missing",
                facts=facts,
            )
        same = left.reference == right.reference
        return RuleEvidence(
            self.rule_id,
            MatchVerdict.MATCH if same else MatchVerdict.NO_MATCH,
            "reference.same" if same else "reference.different",
            facts=facts,
        )


@dataclass(frozen=True, slots=True)
class TimeWindowRule:
    """Require occurrence instants to differ by no more than an inclusive window."""

    max_difference: timedelta
    rule_id: str = "ledgerloom.time_window"

    def __post_init__(self) -> None:
        validate_rule_code(self.rule_id, path="$.rule.rule_id", kind="id")
        if not isinstance(self.max_difference, timedelta):
            raise RuleError(
                code="rule.time_window_type",
                path="$.rule.max_difference",
                message="time window must be a timedelta",
            )
        if self.max_difference < timedelta(0):
            raise RuleError(
                code="rule.time_window_negative",
                path="$.rule.max_difference",
                message="time window must not be negative",
            )

    def evaluate(self, left: LedgerEntry, right: LedgerEntry) -> RuleEvidence:
        left, right = require_entries(left, right)
        difference = abs(right.occurred_at.value - left.occurred_at.value)
        within_window = difference <= self.max_difference
        return RuleEvidence(
            self.rule_id,
            MatchVerdict.MATCH if within_window else MatchVerdict.NO_MATCH,
            "time.within_window" if within_window else "time.outside_window",
            facts=(
                EvidenceFact("difference_microseconds", _timedelta_microseconds(difference)),
                EvidenceFact("left_occurred_at", left.occurred_at.canonical()),
                EvidenceFact(
                    "max_difference_microseconds",
                    _timedelta_microseconds(self.max_difference),
                ),
                EvidenceFact("right_occurred_at", right.occurred_at.canonical()),
            ),
        )
