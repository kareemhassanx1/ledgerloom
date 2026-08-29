import pytest

from ledgerloom.reconciliation import (
    MAX_TOTAL_SCORE,
    ConflictPolicy,
    ConflictStrategy,
    DecisionError,
    DecisionPolicy,
    DecisionStatus,
    MatchScorer,
    ScoringRule,
)
from ledgerloom.rules import AmountRule, ReferenceRule, SameAccountRule


def scorer() -> MatchScorer:
    return MatchScorer(
        (
            ScoringRule(SameAccountRule(), 20, required=True),
            ScoringRule(AmountRule(), 50),
            ScoringRule(ReferenceRule(), 30),
        )
    )


def score_candidates(anchor, *candidates):
    return scorer().rank(anchor, candidates)


def test_no_candidates_is_unmatched_with_a_distinct_reason(make_entry):
    decision = DecisionPolicy(80).decide(make_entry("anchor"), ())

    assert decision.status is DecisionStatus.UNMATCHED
    assert decision.reason_code == "decision.no_candidates"
    assert decision.selection is None
    assert decision.contenders == ()


def test_candidates_below_threshold_are_unmatched_but_ranking_is_retained(make_entry):
    anchor = make_entry("anchor", amount="10", reference="invoice-42")
    candidate = make_entry("candidate", amount="11", reference="invoice-42")

    decision = DecisionPolicy(80).decide(
        anchor,
        score_candidates(anchor, candidate),
    )

    assert decision.status is DecisionStatus.UNMATCHED
    assert decision.reason_code == "decision.none_qualified"
    assert decision.ranking[0].score == 50


def test_required_rule_failure_cannot_qualify_on_score_alone(make_entry):
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

    decision = DecisionPolicy(70).decide(
        anchor,
        score_candidates(anchor, candidate),
    )

    assert decision.ranking[0].score == 80
    assert decision.status is DecisionStatus.UNMATCHED


def test_one_qualified_candidate_is_selected(make_entry):
    anchor = make_entry("anchor", amount="10", reference="invoice-42")
    candidate = make_entry("candidate", amount="10", reference="invoice-42")

    decision = DecisionPolicy(80).decide(
        anchor,
        score_candidates(anchor, candidate),
    )

    assert decision.status is DecisionStatus.MATCHED
    assert decision.reason_code == "decision.unique_leader"
    assert decision.selection is decision.contenders[0]
    assert decision.selection.candidate == candidate


def test_equal_leaders_are_ambiguous_regardless_of_zero_margin(make_entry):
    anchor = make_entry("anchor", amount="10", reference="invoice-42")
    first = make_entry(
        "candidate-b",
        amount="10",
        reference="invoice-42",
        occurred_at="2026-08-29T10:00:00Z",
    )
    second = make_entry(
        "candidate-a",
        amount="10",
        reference="invoice-42",
        occurred_at="2026-08-29T10:00:00Z",
    )

    decision = DecisionPolicy(80, minimum_margin=0).decide(
        anchor,
        reversed(score_candidates(anchor, first, second)),
    )

    assert decision.status is DecisionStatus.AMBIGUOUS
    assert decision.selection is None
    assert tuple(str(item.candidate.entry_id) for item in decision.contenders) == (
        "candidate-a",
        "candidate-b",
    )


def test_leader_inside_required_margin_is_ambiguous(make_entry):
    anchor = make_entry("anchor", amount="10", reference="invoice-42")
    exact = make_entry("exact", amount="10", reference="invoice-42")
    missing_reference = make_entry("missing", amount="10", reference=None)
    scores = score_candidates(anchor, missing_reference, exact)

    ambiguous = DecisionPolicy(60, minimum_margin=31).decide(anchor, scores)
    selected = DecisionPolicy(60, minimum_margin=30).decide(anchor, scores)

    assert tuple(item.score for item in ambiguous.contenders) == (100, 70)
    assert ambiguous.status is DecisionStatus.AMBIGUOUS
    assert selected.status is DecisionStatus.MATCHED
    assert selected.selection.score == 100


@pytest.mark.parametrize(
    "field,value",
    [
        ("minimum_score", True),
        ("minimum_score", -1),
        ("minimum_score", MAX_TOTAL_SCORE + 1),
        ("minimum_margin", 1.5),
        ("minimum_margin", -1),
    ],
)
def test_decision_policy_rejects_invalid_integer_configuration(field, value):
    values = {"minimum_score": 0, "minimum_margin": 0}
    values[field] = value

    with pytest.raises(DecisionError):
        DecisionPolicy(**values)


@pytest.mark.parametrize("scores", [object(), "score", (object(),)])
def test_decision_policy_rejects_invalid_score_collections(make_entry, scores):
    with pytest.raises(DecisionError):
        DecisionPolicy(0).decide(make_entry("anchor"), scores)


def test_decision_rejects_scores_created_for_another_anchor(make_entry):
    requested = make_entry("anchor-requested")
    other = make_entry("anchor-other")
    candidate = make_entry("candidate")
    score = scorer().score(other, candidate)

    with pytest.raises(DecisionError) as captured:
        DecisionPolicy(0).decide(requested, (score,))

    assert captured.value.code == "decision.score_anchor_mismatch"


def test_decision_rejects_mixed_score_scales(make_entry):
    anchor = make_entry("anchor")
    full = scorer().score(anchor, make_entry("candidate-full"))
    narrow_scorer = MatchScorer((ScoringRule(SameAccountRule(), 10),))
    narrow = narrow_scorer.score(anchor, make_entry("candidate-narrow"))

    with pytest.raises(DecisionError) as captured:
        DecisionPolicy(0).decide(anchor, (full, narrow))

    assert captured.value.code == "decision.score_scale_mismatch"


def matched_decision(anchor, candidate, *, minimum_score=0):
    scores = score_candidates(anchor, candidate)
    decision = DecisionPolicy(minimum_score).decide(anchor, scores)
    assert decision.status is DecisionStatus.MATCHED
    return decision


def test_reject_all_marks_every_shared_selection_as_conflict(make_entry):
    shared = make_entry("shared", amount="10", reference="shared-ref")
    anchor_b = make_entry("anchor-b", amount="10", reference="shared-ref")
    anchor_a = make_entry("anchor-a", amount="10", reference="shared-ref")
    decisions = (
        matched_decision(anchor_b, shared),
        matched_decision(anchor_a, shared),
    )

    resolved = ConflictPolicy().resolve(decisions)

    assert tuple(str(item.anchor.entry_id) for item in resolved) == ("anchor-a", "anchor-b")
    assert tuple(item.status for item in resolved) == (
        DecisionStatus.CONFLICT,
        DecisionStatus.CONFLICT,
    )
    assert all(item.reason_code == "conflict.shared_candidate" for item in resolved)
    assert all(item.contenders[0].candidate == shared for item in resolved)


def test_prefer_higher_score_keeps_only_a_unique_winner(make_entry):
    shared = make_entry("shared", amount="10", reference="shared-ref")
    strong_anchor = make_entry("anchor-strong", amount="10", reference="shared-ref")
    weak_anchor = make_entry("anchor-weak", amount="10", reference=None)
    decisions = (
        matched_decision(weak_anchor, shared, minimum_score=60),
        matched_decision(strong_anchor, shared, minimum_score=60),
    )

    resolved = ConflictPolicy(ConflictStrategy.PREFER_HIGHER_SCORE).resolve(decisions)
    by_anchor = {str(item.anchor.entry_id): item for item in resolved}

    assert by_anchor["anchor-strong"].status is DecisionStatus.MATCHED
    assert by_anchor["anchor-strong"].selection.score == 100
    assert by_anchor["anchor-weak"].status is DecisionStatus.CONFLICT
    assert by_anchor["anchor-weak"].reason_code == "conflict.lower_score"


def test_prefer_higher_score_does_not_break_equal_top_ties_by_id(make_entry):
    shared = make_entry("shared", amount="10", reference="shared-ref")
    first = make_entry("anchor-a", amount="10", reference="shared-ref")
    second = make_entry("anchor-b", amount="10", reference="shared-ref")

    resolved = ConflictPolicy(ConflictStrategy.PREFER_HIGHER_SCORE).resolve(
        (matched_decision(second, shared), matched_decision(first, shared))
    )

    assert all(item.status is DecisionStatus.CONFLICT for item in resolved)
    assert all(item.reason_code == "conflict.equal_top_score" for item in resolved)


def test_nonconflicting_and_nonmatched_decisions_are_preserved(make_entry):
    anchor_a = make_entry("anchor-a", amount="10", reference="reference-a")
    anchor_b = make_entry("anchor-b", amount="20", reference="reference-b")
    candidate_a = make_entry("candidate-a", amount="10", reference="reference-a")
    candidate_b = make_entry("candidate-b", amount="20", reference="reference-b")
    unmatched = DecisionPolicy(100).decide(make_entry("anchor-c"), ())
    decisions = (
        unmatched,
        matched_decision(anchor_b, candidate_b),
        matched_decision(anchor_a, candidate_a),
    )

    resolved = ConflictPolicy().resolve(decisions)

    assert tuple(item.status for item in resolved) == (
        DecisionStatus.MATCHED,
        DecisionStatus.MATCHED,
        DecisionStatus.UNMATCHED,
    )


def test_conflict_policy_rejects_invalid_strategy():
    with pytest.raises(DecisionError) as captured:
        ConflictPolicy("reject_all")  # type: ignore[arg-type]

    assert captured.value.code == "conflict.strategy_type"


def test_conflict_policy_rejects_duplicate_anchor_decisions(make_entry):
    anchor = make_entry("anchor", amount="10", reference="shared-ref")
    first = matched_decision(anchor, make_entry("candidate-a", reference="shared-ref"))
    second = matched_decision(anchor, make_entry("candidate-b", reference="shared-ref"))

    with pytest.raises(DecisionError) as captured:
        ConflictPolicy().resolve((first, second))

    assert captured.value.code == "conflict.anchor_duplicate"


def test_conflict_policy_rejects_incomparable_score_scales(make_entry):
    shared = make_entry("shared")
    first_anchor = make_entry("anchor-first")
    second_anchor = make_entry("anchor-second")
    full = matched_decision(first_anchor, shared)
    narrow_scorer = MatchScorer((ScoringRule(SameAccountRule(), 10),))
    narrow_scores = narrow_scorer.rank(second_anchor, (shared,))
    narrow = DecisionPolicy(0).decide(second_anchor, narrow_scores)

    rejected = ConflictPolicy().resolve((full, narrow))

    with pytest.raises(DecisionError) as captured:
        ConflictPolicy(ConflictStrategy.PREFER_HIGHER_SCORE).resolve((full, narrow))

    assert all(item.status is DecisionStatus.CONFLICT for item in rejected)
    assert captured.value.code == "conflict.score_scale_mismatch"


@pytest.mark.parametrize("decisions", [object(), "decision", (object(),)])
def test_conflict_policy_rejects_invalid_decision_collections(decisions):
    with pytest.raises(DecisionError):
        ConflictPolicy().resolve(decisions)
