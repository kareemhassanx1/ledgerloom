from dataclasses import dataclass, field

import pytest

from ledgerloom.rules import (
    AllOfRule,
    AnyOfRule,
    DifferentEntryRule,
    DifferentSourceRule,
    MatchVerdict,
    NotRule,
    RuleError,
    RuleEvidence,
    SameAccountRule,
)


@dataclass
class StubRule:
    rule_id: str
    verdict: MatchVerdict
    calls: list[tuple[str, str]] = field(default_factory=list)

    def evaluate(self, left, right):
        self.calls.append((str(left.entry_id), str(right.entry_id)))
        return RuleEvidence(self.rule_id, self.verdict, "stub.evaluated")


def stub(rule_id: str, verdict: MatchVerdict) -> StubRule:
    return StubRule(rule_id, verdict)


@pytest.mark.parametrize(
    "verdicts,expected,reason",
    [
        ((MatchVerdict.MATCH, MatchVerdict.MATCH), MatchVerdict.MATCH, "composite.all_matched"),
        (
            (MatchVerdict.MATCH, MatchVerdict.INDETERMINATE),
            MatchVerdict.INDETERMINATE,
            "composite.child_indeterminate",
        ),
        (
            (MatchVerdict.INDETERMINATE, MatchVerdict.NO_MATCH),
            MatchVerdict.NO_MATCH,
            "composite.child_rejected",
        ),
    ],
)
def test_all_of_uses_three_valued_conjunction(make_entry, verdicts, expected, reason):
    rule = AllOfRule(
        "policy.required",
        tuple(stub(f"test.child_{index}", verdict) for index, verdict in enumerate(verdicts)),
    )

    evidence = rule.evaluate(make_entry("entry-1"), make_entry("entry-2"))

    assert evidence.verdict is expected
    assert evidence.reason_code == reason
    assert evidence.fact("match_count") == verdicts.count(MatchVerdict.MATCH)
    assert evidence.fact("no_match_count") == verdicts.count(MatchVerdict.NO_MATCH)
    assert evidence.fact("indeterminate_count") == verdicts.count(MatchVerdict.INDETERMINATE)


@pytest.mark.parametrize(
    "verdicts,expected,reason",
    [
        (
            (MatchVerdict.NO_MATCH, MatchVerdict.MATCH),
            MatchVerdict.MATCH,
            "composite.child_matched",
        ),
        (
            (MatchVerdict.NO_MATCH, MatchVerdict.INDETERMINATE),
            MatchVerdict.INDETERMINATE,
            "composite.child_indeterminate",
        ),
        (
            (MatchVerdict.NO_MATCH, MatchVerdict.NO_MATCH),
            MatchVerdict.NO_MATCH,
            "composite.none_matched",
        ),
    ],
)
def test_any_of_uses_three_valued_disjunction(make_entry, verdicts, expected, reason):
    rule = AnyOfRule(
        "policy.alternatives",
        tuple(stub(f"test.child_{index}", verdict) for index, verdict in enumerate(verdicts)),
    )

    evidence = rule.evaluate(make_entry("entry-1"), make_entry("entry-2"))

    assert evidence.verdict is expected
    assert evidence.reason_code == reason


@pytest.mark.parametrize(
    "child_verdict,expected,reason",
    [
        (MatchVerdict.MATCH, MatchVerdict.NO_MATCH, "composite.negated_match"),
        (MatchVerdict.NO_MATCH, MatchVerdict.MATCH, "composite.negated_no_match"),
        (
            MatchVerdict.INDETERMINATE,
            MatchVerdict.INDETERMINATE,
            "composite.negated_indeterminate",
        ),
    ],
)
def test_not_inverts_only_definite_verdicts(
    make_entry,
    child_verdict,
    expected,
    reason,
):
    child = stub("test.child", child_verdict)

    evidence = NotRule("policy.negated", child).evaluate(
        make_entry("entry-1"),
        make_entry("entry-2"),
    )

    assert evidence.verdict is expected
    assert evidence.reason_code == reason
    assert evidence.children[0].verdict is child_verdict


@pytest.mark.parametrize("composite_type", [AllOfRule, AnyOfRule])
def test_composites_evaluate_every_child_without_short_circuiting(
    composite_type,
    make_entry,
):
    first = stub("test.first", MatchVerdict.NO_MATCH)
    second = stub("test.second", MatchVerdict.MATCH)
    rule = composite_type("policy.complete_evidence", (first, second))

    evidence = rule.evaluate(make_entry("entry-1"), make_entry("entry-2"))

    assert len(first.calls) == 1
    assert len(second.calls) == 1
    assert tuple(child.rule_id for child in evidence.children) == ("test.first", "test.second")


def test_nested_policy_retains_a_complete_explanation_tree(make_entry):
    safeguards = AllOfRule(
        "policy.safeguards",
        (DifferentEntryRule(), DifferentSourceRule(), SameAccountRule()),
    )
    policy = NotRule("policy.reject_eligible", safeguards)

    evidence = policy.evaluate(
        make_entry("entry-1", source_id="bank-primary"),
        make_entry("entry-2", source_id="processor-primary"),
    )

    assert tuple(item.rule_id for item in evidence.walk()) == (
        "policy.reject_eligible",
        "policy.safeguards",
        "ledgerloom.different_entry",
        "ledgerloom.different_source",
        "ledgerloom.same_account",
    )
    assert evidence.verdict is MatchVerdict.NO_MATCH


@pytest.mark.parametrize("composite_type", [AllOfRule, AnyOfRule])
def test_composite_requires_at_least_one_child(composite_type):
    with pytest.raises(RuleError) as captured:
        composite_type("policy.empty", ())

    assert captured.value.code == "rule.children_empty"


@pytest.mark.parametrize("composite_type", [AllOfRule, AnyOfRule])
def test_composite_requires_a_tuple_of_children(composite_type):
    with pytest.raises(RuleError) as captured:
        composite_type("policy.list", [stub("test.child", MatchVerdict.MATCH)])

    assert captured.value.code == "rule.children_type"


@pytest.mark.parametrize("composite_type", [AllOfRule, AnyOfRule])
def test_composite_rejects_duplicate_child_identifiers(composite_type):
    with pytest.raises(RuleError) as captured:
        composite_type(
            "policy.duplicates",
            (
                stub("test.same", MatchVerdict.MATCH),
                stub("test.same", MatchVerdict.NO_MATCH),
            ),
        )

    assert captured.value.code == "rule.child_id_duplicate"


@pytest.mark.parametrize("composite_type", [AllOfRule, AnyOfRule])
def test_composite_rejects_direct_self_reference_by_identifier(composite_type):
    with pytest.raises(RuleError) as captured:
        composite_type(
            "policy.recursive",
            (stub("policy.recursive", MatchVerdict.MATCH),),
        )

    assert captured.value.code == "rule.child_id_recursive"


def test_not_rejects_direct_self_reference_by_identifier():
    with pytest.raises(RuleError) as captured:
        NotRule("policy.recursive", stub("policy.recursive", MatchVerdict.MATCH))

    assert captured.value.code == "rule.child_id_recursive"
