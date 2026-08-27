from dataclasses import FrozenInstanceError, dataclass

import pytest

from ledgerloom.rules import (
    EvidenceFact,
    MatchVerdict,
    RuleError,
    RuleEvidence,
    evaluate_rule,
)


@dataclass(frozen=True)
class StaticRule:
    rule_id: str
    verdict: MatchVerdict = MatchVerdict.MATCH

    def evaluate(self, left, right):
        return RuleEvidence(self.rule_id, self.verdict, "static.evaluated")


def test_verdict_values_are_stable_strings():
    assert tuple(MatchVerdict) == (
        MatchVerdict.MATCH,
        MatchVerdict.NO_MATCH,
        MatchVerdict.INDETERMINATE,
    )
    assert str(MatchVerdict.NO_MATCH) == "no_match"


def test_facts_are_sorted_by_name_for_deterministic_evidence():
    evidence = RuleEvidence(
        "test.example",
        MatchVerdict.MATCH,
        "test.accepted",
        facts=(EvidenceFact("zeta", 2), EvidenceFact("alpha", 1)),
    )

    assert evidence.facts == (EvidenceFact("alpha", 1), EvidenceFact("zeta", 2))


@pytest.mark.parametrize("value", ["text", "", 42, True, False, None])
def test_fact_accepts_canonical_scalar_values(value):
    assert EvidenceFact("sample", value).value == value


@pytest.mark.parametrize("value", [1.5, (), [], {}, b"bytes"])
def test_fact_rejects_noncanonical_values(value):
    with pytest.raises(RuleError) as captured:
        EvidenceFact("sample", value)

    assert captured.value.code == "rule.fact_value_type"


@pytest.mark.parametrize("name", ["", "Upper", "two.words", "has-hyphen", "two words"])
def test_fact_rejects_unstable_names(name):
    with pytest.raises(RuleError) as captured:
        EvidenceFact(name, 1)

    assert captured.value.code == "rule.fact_name_format"


@pytest.mark.parametrize("value", [" padded", "padded ", "line\nbreak"])
def test_fact_rejects_noncanonical_text(value):
    with pytest.raises(RuleError) as captured:
        EvidenceFact("sample", value)

    assert captured.value.code == "rule.fact_value_format"


def test_evidence_rejects_duplicate_fact_names():
    with pytest.raises(RuleError) as captured:
        RuleEvidence(
            "test.example",
            MatchVerdict.MATCH,
            "test.accepted",
            facts=(EvidenceFact("same", 1), EvidenceFact("same", 2)),
        )

    assert captured.value.code == "rule.fact_duplicate"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("rule_id", "not_dotted", "rule.id_format"),
        ("reason_code", "UPPER.invalid", "rule.reason_code_format"),
        ("verdict", "match", "rule.verdict_type"),
        ("facts", [], "rule.facts_type"),
        ("children", [], "rule.children_type"),
    ],
)
def test_evidence_validates_its_public_contract(field, value, code):
    values = {
        "rule_id": "test.example",
        "verdict": MatchVerdict.MATCH,
        "reason_code": "test.accepted",
        "facts": (),
        "children": (),
    }
    values[field] = value

    with pytest.raises(RuleError) as captured:
        RuleEvidence(**values)

    assert captured.value.code == code


def test_fact_lookup_returns_value_and_reports_missing_name():
    evidence = RuleEvidence(
        "test.example",
        MatchVerdict.MATCH,
        "test.accepted",
        facts=(EvidenceFact("account", "assets:cash"),),
    )

    assert evidence.fact("account") == "assets:cash"
    with pytest.raises(KeyError, match="missing"):
        evidence.fact("missing")


def test_walk_and_failures_preserve_nested_rule_order():
    accepted = RuleEvidence("test.accepted", MatchVerdict.MATCH, "test.yes")
    rejected = RuleEvidence("test.rejected", MatchVerdict.NO_MATCH, "test.no")
    nested = RuleEvidence(
        "test.nested",
        MatchVerdict.NO_MATCH,
        "test.child_failed",
        children=(accepted, rejected),
    )
    root = RuleEvidence(
        "test.root",
        MatchVerdict.NO_MATCH,
        "test.child_failed",
        children=(nested,),
    )

    assert tuple(item.rule_id for item in root.walk()) == (
        "test.root",
        "test.nested",
        "test.accepted",
        "test.rejected",
    )
    assert tuple(item.rule_id for item in root.failures) == (
        "test.root",
        "test.nested",
        "test.rejected",
    )


def test_evidence_is_immutable():
    evidence = RuleEvidence("test.example", MatchVerdict.MATCH, "test.accepted")

    with pytest.raises(FrozenInstanceError):
        evidence.verdict = MatchVerdict.NO_MATCH  # type: ignore[misc]


def test_evaluate_rule_accepts_a_structural_rule(make_entry):
    left = make_entry("entry-1")
    right = make_entry("entry-2")

    evidence = evaluate_rule(StaticRule("test.static"), left, right)

    assert evidence.rule_id == "test.static"
    assert evidence.is_match


@pytest.mark.parametrize("side", ["left", "right"])
def test_evaluate_rule_rejects_non_entry_operands(make_entry, side):
    left = make_entry("entry-1")
    right = make_entry("entry-2")
    if side == "left":
        left = object()
    else:
        right = object()

    with pytest.raises(RuleError) as captured:
        evaluate_rule(StaticRule("test.static"), left, right)

    assert captured.value.code == f"rule.{side}_type"


def test_evaluate_rule_rejects_an_incomplete_rule(make_entry):
    with pytest.raises(RuleError) as captured:
        evaluate_rule(object(), make_entry("entry-1"), make_entry("entry-2"))

    assert captured.value.code == "rule.type"


def test_evaluate_rule_rejects_a_non_evidence_result(make_entry):
    class BadResultRule:
        rule_id = "test.bad_result"

        def evaluate(self, left, right):
            return True

    with pytest.raises(RuleError) as captured:
        evaluate_rule(BadResultRule(), make_entry("entry-1"), make_entry("entry-2"))

    assert captured.value.code == "rule.result_type"


def test_evaluate_rule_rejects_evidence_for_another_rule(make_entry):
    class WrongIdRule:
        rule_id = "test.expected"

        def evaluate(self, left, right):
            return RuleEvidence("test.actual", MatchVerdict.MATCH, "test.accepted")

    with pytest.raises(RuleError) as captured:
        evaluate_rule(WrongIdRule(), make_entry("entry-1"), make_entry("entry-2"))

    assert captured.value.code == "rule.result_id_mismatch"
