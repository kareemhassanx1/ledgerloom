from dataclasses import FrozenInstanceError

import pytest

from ledgerloom.rules import (
    DifferentEntryRule,
    DifferentSourceRule,
    MatchVerdict,
    RuleError,
    SameAccountRule,
)


def test_different_entry_rule_accepts_distinct_entry_ids(make_entry):
    evidence = DifferentEntryRule().evaluate(
        make_entry("entry-1"),
        make_entry("entry-2"),
    )

    assert evidence.verdict is MatchVerdict.MATCH
    assert evidence.reason_code == "entry.distinct"
    assert evidence.fact("left_entry_id") == "entry-1"
    assert evidence.fact("right_entry_id") == "entry-2"


def test_different_entry_rule_rejects_the_same_entry(make_entry):
    entry = make_entry("entry-1")

    evidence = DifferentEntryRule().evaluate(entry, entry)

    assert evidence.verdict is MatchVerdict.NO_MATCH
    assert evidence.reason_code == "entry.same"


def test_same_account_rule_accepts_equal_normalized_accounts(make_entry):
    evidence = SameAccountRule().evaluate(
        make_entry("entry-1", account_id="assets:checking"),
        make_entry("entry-2", account_id="assets:checking"),
    )

    assert evidence.verdict is MatchVerdict.MATCH
    assert evidence.reason_code == "account.same"
    assert evidence.fact("left_account_id") == "assets:checking"


def test_same_account_rule_rejects_different_accounts(make_entry):
    evidence = SameAccountRule().evaluate(
        make_entry("entry-1", account_id="assets:checking"),
        make_entry("entry-2", account_id="liabilities:card"),
    )

    assert evidence.verdict is MatchVerdict.NO_MATCH
    assert evidence.reason_code == "account.different"


def test_different_source_rule_accepts_cross_source_entries(make_entry):
    evidence = DifferentSourceRule().evaluate(
        make_entry("entry-1", source_id="bank-primary"),
        make_entry("entry-2", source_id="processor-primary"),
    )

    assert evidence.verdict is MatchVerdict.MATCH
    assert evidence.reason_code == "source.distinct"
    assert evidence.fact("right_source_id") == "processor-primary"


def test_different_source_rule_rejects_same_source_entries(make_entry):
    evidence = DifferentSourceRule().evaluate(
        make_entry("entry-1", source_id="bank-primary"),
        make_entry("entry-2", source_id="bank-primary"),
    )

    assert evidence.verdict is MatchVerdict.NO_MATCH
    assert evidence.reason_code == "source.same"


@pytest.mark.parametrize(
    "rule_type",
    [DifferentEntryRule, SameAccountRule, DifferentSourceRule],
)
def test_builtin_rule_ids_can_be_namespaced(rule_type, make_entry):
    rule = rule_type("policy.custom_rule")

    evidence = rule.evaluate(make_entry("entry-1"), make_entry("entry-2"))

    assert evidence.rule_id == "policy.custom_rule"


@pytest.mark.parametrize(
    "rule_type",
    [DifferentEntryRule, SameAccountRule, DifferentSourceRule],
)
def test_builtin_rules_reject_invalid_ids(rule_type):
    with pytest.raises(RuleError) as captured:
        rule_type("not_dotted")

    assert captured.value.code == "rule.id_format"


def test_builtin_rules_are_immutable():
    rule = SameAccountRule()

    with pytest.raises(FrozenInstanceError):
        rule.rule_id = "changed.rule"  # type: ignore[misc]
