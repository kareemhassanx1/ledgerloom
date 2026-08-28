from datetime import timedelta
from decimal import Decimal

import pytest

from ledgerloom.rules import (
    AmountRule,
    MatchVerdict,
    ReferenceRule,
    RuleError,
    TimeWindowRule,
)


def test_exact_amount_rule_accepts_equivalent_decimal_scales(make_entry):
    evidence = AmountRule().evaluate(
        make_entry("entry-1", amount="125.500"),
        make_entry("entry-2", amount="125.50"),
    )

    assert evidence.verdict is MatchVerdict.MATCH
    assert evidence.reason_code == "amount.within_tolerance"
    assert evidence.fact("difference") == "0"


def test_amount_rule_includes_tolerance_boundary_and_rejects_beyond_it(make_entry):
    rule = AmountRule(Decimal("0.01"))

    boundary = rule.evaluate(
        make_entry("entry-1", amount="10.00"),
        make_entry("entry-2", amount="10.01"),
    )
    outside = rule.evaluate(
        make_entry("entry-1", amount="10.00"),
        make_entry("entry-3", amount="10.010001"),
    )

    assert boundary.verdict is MatchVerdict.MATCH
    assert boundary.fact("difference") == "0.01"
    assert outside.verdict is MatchVerdict.NO_MATCH
    assert outside.reason_code == "amount.outside_tolerance"


def test_amount_rule_rejects_different_currencies_without_decimal_arithmetic(make_entry):
    evidence = AmountRule(Decimal("1000")).evaluate(
        make_entry("entry-1", amount="10", currency="USD"),
        make_entry("entry-2", amount="10", currency="EUR"),
    )

    assert evidence.verdict is MatchVerdict.NO_MATCH
    assert evidence.reason_code == "amount.currency_different"
    assert evidence.fact("difference") is None


@pytest.mark.parametrize(
    "value",
    [1, Decimal("-0.01"), Decimal("Infinity"), Decimal("1E+38"), Decimal("1E-19")],
)
def test_amount_rule_rejects_invalid_tolerance(value):
    with pytest.raises(RuleError):
        AmountRule(value)  # type: ignore[arg-type]


def test_reference_rule_matches_exact_text_and_rejects_case_changes(make_entry):
    rule = ReferenceRule()

    same = rule.evaluate(
        make_entry("entry-1", reference="Invoice-42"),
        make_entry("entry-2", reference="Invoice-42"),
    )
    changed = rule.evaluate(
        make_entry("entry-1", reference="Invoice-42"),
        make_entry("entry-3", reference="invoice-42"),
    )

    assert same.verdict is MatchVerdict.MATCH
    assert changed.verdict is MatchVerdict.NO_MATCH
    assert changed.reason_code == "reference.different"


def test_reference_rule_reports_missing_data_as_indeterminate(make_entry):
    left = make_entry("entry-1", reference=None)
    right = make_entry("entry-2")

    evidence = ReferenceRule().evaluate(left, right)

    assert evidence.verdict is MatchVerdict.INDETERMINATE
    assert evidence.reason_code == "reference.missing"
    assert evidence.fact("left_reference") is None


def test_time_window_rule_includes_both_exact_boundaries(make_entry):
    rule = TimeWindowRule(timedelta(hours=2))
    center = make_entry("entry-1", occurred_at="2026-08-27T10:00:00Z")

    before = rule.evaluate(
        center,
        make_entry("entry-2", occurred_at="2026-08-27T08:00:00Z"),
    )
    after = rule.evaluate(
        center,
        make_entry("entry-3", occurred_at="2026-08-27T12:00:00Z"),
    )

    assert before.verdict is MatchVerdict.MATCH
    assert after.verdict is MatchVerdict.MATCH
    assert after.fact("difference_microseconds") == 7_200_000_000


def test_time_window_rule_rejects_one_microsecond_beyond_boundary(make_entry):
    evidence = TimeWindowRule(timedelta(hours=2)).evaluate(
        make_entry("entry-1", occurred_at="2026-08-27T10:00:00Z"),
        make_entry("entry-2", occurred_at="2026-08-27T12:00:00.000001Z"),
    )

    assert evidence.verdict is MatchVerdict.NO_MATCH
    assert evidence.reason_code == "time.outside_window"


@pytest.mark.parametrize("value", [60, timedelta(microseconds=-1)])
def test_time_window_rule_rejects_invalid_windows(value):
    with pytest.raises(RuleError):
        TimeWindowRule(value)  # type: ignore[arg-type]
