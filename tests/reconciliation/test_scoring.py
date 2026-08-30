from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from ledgerloom.reconciliation import (
    MAX_RULE_WEIGHT,
    CandidateScore,
    MatchScorer,
    ScoringError,
    ScoringRule,
)
from ledgerloom.rules import (
    AmountRule,
    MatchVerdict,
    ReferenceRule,
    RuleEvidence,
    SameAccountRule,
)


def scorer() -> MatchScorer:
    return MatchScorer(
        (
            ScoringRule(SameAccountRule(), 20, required=True),
            ScoringRule(AmountRule(Decimal("0.01")), 50),
            ScoringRule(ReferenceRule(), 30),
        )
    )


def test_exact_candidate_earns_every_integer_weight(make_entry):
    anchor = make_entry("anchor", amount="10", reference="invoice-42")
    candidate = make_entry("candidate", amount="10.00", reference="invoice-42")

    result = scorer().score(anchor, candidate)

    assert result.score == 100
    assert result.maximum_score == 100
    assert result.confidence_basis_points == 10_000
    assert result.required_rules_satisfied
    assert result.required_rule_ids == ("ledgerloom.same_account",)
    assert result.rejected_required_rule_ids == ()
    assert result.indeterminate_required_rule_ids == ()
    assert result.reason_code == "score.rules_evaluated"
    assert result.matched_rule_ids == (
        "ledgerloom.same_account",
        "ledgerloom.amount",
        "ledgerloom.reference",
    )


def test_no_match_and_indeterminate_rules_earn_no_points(make_entry):
    anchor = make_entry("anchor", amount="10", reference="invoice-42")
    outside = make_entry("outside", amount="11", reference="invoice-42")
    missing = make_entry("missing", amount="10", reference=None)

    outside_score = scorer().score(anchor, outside)
    missing_score = scorer().score(anchor, missing)

    assert outside_score.score == 50
    assert outside_score.confidence_basis_points == 5_000
    assert missing_score.score == 70
    assert missing_score.confidence_basis_points == 7_000
    assert missing_score.evidence[-1].verdict is MatchVerdict.INDETERMINATE


def test_required_rejection_disqualifies_even_a_high_scoring_candidate(make_entry):
    anchor = make_entry(
        "anchor",
        account_id="assets:checking",
        amount="10",
        reference="invoice-42",
    )
    candidate = make_entry(
        "candidate",
        account_id="liabilities:card",
        amount="10",
        reference="invoice-42",
    )

    result = scorer().score(anchor, candidate)

    assert result.score == 80
    assert not result.required_rules_satisfied
    assert result.reason_code == "score.required_rule_rejected"
    assert result.rejected_required_rule_ids == ("ledgerloom.same_account",)


def test_required_indeterminate_has_a_distinct_reason(make_entry):
    configured = MatchScorer((ScoringRule(ReferenceRule(), 10, required=True),))

    result = configured.score(
        make_entry("anchor", reference=None),
        make_entry("candidate", reference="invoice-42"),
    )

    assert result.score == 0
    assert not result.required_rules_satisfied
    assert result.reason_code == "score.required_rule_indeterminate"
    assert result.required_rule_ids == ("ledgerloom.reference",)
    assert result.indeterminate_required_rule_ids == ("ledgerloom.reference",)


def test_all_rules_are_evaluated_and_preserve_configuration_order(make_entry):
    result = scorer().score(
        make_entry("anchor", amount="10", reference="invoice-42"),
        make_entry("candidate", amount="20", reference=None),
    )

    assert tuple(item.rule_id for item in result.evidence) == (
        "ledgerloom.same_account",
        "ledgerloom.amount",
        "ledgerloom.reference",
    )


def test_ranking_is_independent_of_candidate_input_order(make_entry):
    anchor = make_entry("anchor", amount="10", reference="invoice-42")
    exact = make_entry(
        "exact",
        amount="10",
        reference="invoice-42",
        occurred_at="2026-08-29T10:03:00Z",
    )
    missing = make_entry(
        "missing",
        amount="10",
        reference=None,
        occurred_at="2026-08-29T10:01:00Z",
    )
    outside = make_entry(
        "outside",
        amount="11",
        reference="invoice-42",
        occurred_at="2026-08-29T10:00:00Z",
    )

    forward = scorer().rank(anchor, (outside, exact, missing))
    reverse = scorer().rank(anchor, (missing, exact, outside))

    assert tuple(str(item.candidate.entry_id) for item in forward) == (
        "exact",
        "missing",
        "outside",
    )
    assert reverse == forward


def test_equal_scores_use_occurrence_then_entry_id_as_stable_tie_breakers(make_entry):
    anchor = make_entry("anchor", amount="10", reference="invoice-42")
    later = make_entry(
        "candidate-c",
        amount="10",
        reference="invoice-42",
        occurred_at="2026-08-29T10:01:00Z",
    )
    same_b = make_entry(
        "candidate-b",
        amount="10",
        reference="invoice-42",
        occurred_at="2026-08-29T10:00:00Z",
    )
    same_a = make_entry(
        "candidate-a",
        amount="10",
        reference="invoice-42",
        occurred_at="2026-08-29T10:00:00Z",
    )

    ranked = scorer().rank(anchor, (later, same_b, same_a))

    assert tuple(str(item.candidate.entry_id) for item in ranked) == (
        "candidate-a",
        "candidate-b",
        "candidate-c",
    )


def test_basis_points_use_exact_floor_rounding(make_entry):
    evidence = RuleEvidence("test.rule", MatchVerdict.MATCH, "test.matched")
    result = CandidateScore(
        make_entry("anchor"),
        make_entry("candidate"),
        1,
        3,
        (evidence,),
        (),
        True,
        "score.rules_evaluated",
    )

    assert result.confidence_basis_points == 3_333


def test_score_rejects_required_metadata_inconsistent_with_evidence(make_entry):
    result = scorer().score(make_entry("anchor"), make_entry("candidate"))

    with pytest.raises(ScoringError) as unknown:
        replace(result, required_rule_ids=("unknown.rule",))
    with pytest.raises(ScoringError) as status:
        replace(result, required_rules_satisfied=False)
    with pytest.raises(ScoringError) as reason:
        replace(result, reason_code="score.required_rule_rejected")

    assert unknown.value.code == "score.required_rule_unknown"
    assert status.value.code == "score.required_status_mismatch"
    assert reason.value.code == "score.reason_mismatch"


@pytest.mark.parametrize("weight", [True, 0, -1, 1.5, MAX_RULE_WEIGHT + 1])
def test_scoring_rule_rejects_invalid_weights(weight):
    with pytest.raises(ScoringError):
        ScoringRule(SameAccountRule(), weight)  # type: ignore[arg-type]


def test_scoring_rule_requires_boolean_required_flag():
    with pytest.raises(ScoringError) as captured:
        ScoringRule(SameAccountRule(), 10, required=1)  # type: ignore[arg-type]

    assert captured.value.code == "score.required_type"


def test_scorer_rejects_empty_non_tuple_and_duplicate_rules():
    with pytest.raises(ScoringError, match="at least one"):
        MatchScorer(())
    with pytest.raises(ScoringError, match="tuple"):
        MatchScorer([ScoringRule(SameAccountRule(), 10)])  # type: ignore[arg-type]
    with pytest.raises(ScoringError) as captured:
        MatchScorer(
            (
                ScoringRule(SameAccountRule(), 10),
                ScoringRule(SameAccountRule(), 20),
            )
        )

    assert captured.value.code == "score.rule_id_duplicate"


def test_scorer_rejects_self_and_duplicate_candidates(make_entry):
    anchor = make_entry("anchor")
    duplicate = make_entry("duplicate")

    with pytest.raises(ScoringError) as self_error:
        scorer().score(anchor, anchor)
    with pytest.raises(ScoringError) as duplicate_error:
        scorer().rank(anchor, (duplicate, duplicate))

    assert self_error.value.code == "score.self_candidate"
    assert duplicate_error.value.code == "score.candidate_duplicate"


def test_rank_validates_anchor_even_when_candidates_are_empty():
    with pytest.raises(ScoringError) as captured:
        scorer().rank(object(), ())  # type: ignore[arg-type]

    assert captured.value.code == "score.anchor_type"


@pytest.mark.parametrize("candidates", [object(), "entry", (object(),)])
def test_rank_rejects_invalid_candidate_collections(make_entry, candidates):
    with pytest.raises(ScoringError):
        scorer().rank(make_entry("anchor"), candidates)


def test_score_results_and_configuration_are_immutable(make_entry):
    configured = scorer()
    result = configured.score(make_entry("anchor"), make_entry("candidate"))

    with pytest.raises(FrozenInstanceError):
        result.score = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        configured._maximum_score = 0  # type: ignore[misc]
