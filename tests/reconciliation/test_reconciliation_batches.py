from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal

import pytest

from ledgerloom.domain.identifiers import BatchId, EntryId
from ledgerloom.reconciliation import (
    BatchReconciler,
    CandidateIndex,
    CandidateSelectionPolicy,
    ConflictPolicy,
    ConflictStrategy,
    DecisionPolicy,
    DecisionStatus,
    MatchDecision,
    MatchScorer,
    ReconciliationBatch,
    ReconciliationBatchError,
    ReconciliationPolicySnapshot,
    ScoringRule,
    ScoringRuleSnapshot,
)
from ledgerloom.rules import AmountRule, ReferenceRule, SameAccountRule


def test_batch_workflow_produces_all_outcome_types_in_canonical_order(mixed_batch):
    assert tuple(str(item.anchor.entry_id) for item in mixed_batch.decisions) == (
        "anchor-match",
        "anchor-ambiguous",
        "anchor-conflict-a",
        "anchor-conflict-b",
        "anchor-unmatched",
    )
    assert tuple(item.status for item in mixed_batch.decisions) == (
        DecisionStatus.MATCHED,
        DecisionStatus.AMBIGUOUS,
        DecisionStatus.CONFLICT,
        DecisionStatus.CONFLICT,
        DecisionStatus.UNMATCHED,
    )
    assert mixed_batch.matched_pairs == (
        (EntryId("anchor-match"), EntryId("candidate-match")),
    )
    assert mixed_batch.policy_snapshot == ReconciliationPolicySnapshot(
        scoring_rules=(
            ScoringRuleSnapshot("ledgerloom.same_account", 20, True),
            ScoringRuleSnapshot("ledgerloom.amount", 50, False),
            ScoringRuleSnapshot("ledgerloom.reference", 30, False),
        ),
        minimum_score=60,
        minimum_margin=10,
        max_time_difference=timedelta(minutes=5),
        max_amount_difference=Decimal("0.01"),
        require_reference=False,
        conflict_strategy=ConflictStrategy.REJECT_ALL,
    )


def test_batch_result_is_independent_of_anchor_and_candidate_input_order(
    make_entry,
    batch_reconciler,
):
    anchors = (
        make_entry(
            "anchor-b",
            amount="20",
            reference="invoice-b",
            occurred_at="2026-08-30T10:02:00Z",
        ),
        make_entry(
            "anchor-a",
            amount="10",
            reference="invoice-a",
            occurred_at="2026-08-30T10:01:00Z",
        ),
    )
    candidates = (
        make_entry(
            "candidate-b",
            amount="20",
            reference="invoice-b",
            occurred_at="2026-08-30T10:02:30Z",
        ),
        make_entry(
            "candidate-a",
            amount="10",
            reference="invoice-a",
            occurred_at="2026-08-30T10:01:30Z",
        ),
    )

    forward = batch_reconciler.reconcile(
        BatchId("batch-order"),
        anchors,
        CandidateIndex(candidates),
    )
    reverse = batch_reconciler.reconcile(
        BatchId("batch-order"),
        reversed(anchors),
        CandidateIndex(reversed(candidates)),
    )

    assert reverse == forward


def test_required_reference_selection_can_yield_unmatched_before_scoring(make_entry):
    reconciler = BatchReconciler(
        scorer=MatchScorer((ScoringRule(SameAccountRule(), 10),)),
        decision_policy=DecisionPolicy(0),
        selection_policy=CandidateSelectionPolicy(
            timedelta(minutes=1),
            require_reference=True,
        ),
    )
    anchor = make_entry("anchor", reference=None)
    candidate = make_entry("candidate", reference=None)

    batch = reconciler.reconcile(
        BatchId("batch-reference"),
        (anchor,),
        CandidateIndex((candidate,)),
    )

    assert batch.decisions[0].status is DecisionStatus.UNMATCHED
    assert batch.decisions[0].reason_code == "decision.no_candidates"
    assert batch.decisions[0].ranking == ()


def test_prefer_higher_score_keeps_unique_cross_anchor_winner(make_entry):
    scorer = MatchScorer(
        (
            ScoringRule(SameAccountRule(), 20, required=True),
            ScoringRule(AmountRule(), 50),
            ScoringRule(ReferenceRule(), 30),
        )
    )
    reconciler = BatchReconciler(
        scorer,
        DecisionPolicy(60),
        CandidateSelectionPolicy(timedelta(minutes=5)),
        ConflictPolicy(ConflictStrategy.PREFER_HIGHER_SCORE),
    )
    strong = make_entry(
        "anchor-strong",
        amount="10",
        reference="invoice-shared",
        occurred_at="2026-08-30T10:00:00Z",
    )
    weak = make_entry(
        "anchor-weak",
        amount="10",
        reference=None,
        occurred_at="2026-08-30T10:01:00Z",
    )
    shared = make_entry(
        "candidate-shared",
        amount="10",
        reference="invoice-shared",
        occurred_at="2026-08-30T10:00:30Z",
    )

    batch = reconciler.reconcile(
        BatchId("batch-preference"),
        (weak, strong),
        CandidateIndex((shared,)),
    )

    assert batch.decision_for(strong.entry_id).status is DecisionStatus.MATCHED
    assert batch.decision_for(weak.entry_id).status is DecisionStatus.CONFLICT
    assert batch.decision_for(weak.entry_id).reason_code == "conflict.lower_score"


def test_batch_rejects_unresolved_candidate_reuse(make_entry):
    scorer = MatchScorer((ScoringRule(SameAccountRule(), 10),))
    policy = DecisionPolicy(0)
    shared = make_entry("candidate-shared")
    first_anchor = make_entry("anchor-first")
    second_anchor = make_entry("anchor-second")
    decisions = (
        policy.decide(first_anchor, scorer.rank(first_anchor, (shared,))),
        policy.decide(second_anchor, scorer.rank(second_anchor, (shared,))),
    )

    with pytest.raises(ReconciliationBatchError) as captured:
        ReconciliationBatch(BatchId("batch-unresolved"), decisions)

    assert captured.value.code == "batch.unresolved_conflict"


def test_reconciler_excludes_every_anchor_from_the_candidate_role(
    make_entry,
    batch_reconciler,
):
    first = make_entry("entry-first", reference="shared-reference")
    second = make_entry("entry-second", reference="shared-reference")

    batch = batch_reconciler.reconcile(
        BatchId("batch-role-partition"),
        (second, first),
        CandidateIndex((first, second)),
    )

    assert tuple(item.status for item in batch.decisions) == (
        DecisionStatus.UNMATCHED,
        DecisionStatus.UNMATCHED,
    )
    assert all(item.reason_code == "decision.no_candidates" for item in batch.decisions)


def test_batch_rejects_an_anchor_selected_as_another_anchors_candidate(make_entry):
    scorer = MatchScorer((ScoringRule(SameAccountRule(), 10),))
    policy = DecisionPolicy(0)
    first = make_entry("entry-first")
    second = make_entry("entry-second")
    decisions = (
        policy.decide(first, scorer.rank(first, (second,))),
        policy.decide(second, ()),
    )

    with pytest.raises(ReconciliationBatchError) as captured:
        ReconciliationBatch(BatchId("batch-role-overlap"), decisions)

    assert captured.value.code == "batch.entry_role_overlap"


def test_batch_rejects_a_conflict_without_a_competing_claim(make_entry):
    scorer = MatchScorer((ScoringRule(SameAccountRule(), 10),))
    anchor = make_entry("anchor")
    candidate = make_entry("candidate")
    score = scorer.score(anchor, candidate)
    conflict = MatchDecision(
        anchor,
        DecisionStatus.CONFLICT,
        "conflict.shared_candidate",
        (score,),
        contenders=(score,),
    )

    with pytest.raises(ReconciliationBatchError) as captured:
        ReconciliationBatch(BatchId("batch-orphan-conflict"), (conflict,))

    assert captured.value.code == "batch.orphan_conflict"


def test_batch_sorts_decisions_and_looks_them_up_by_typed_id(make_entry):
    policy = DecisionPolicy(0)
    later = make_entry("anchor-later", occurred_at="2026-08-30T11:00:00Z")
    earlier = make_entry("anchor-earlier", occurred_at="2026-08-30T10:00:00Z")
    batch = ReconciliationBatch(
        BatchId("batch-lookup"),
        (
            policy.decide(later, ()),
            policy.decide(earlier, ()),
        ),
    )

    assert batch.decisions[0].anchor == earlier
    assert batch.decision_for(EntryId("anchor-later")).anchor == later
    with pytest.raises(KeyError):
        batch.decision_for(EntryId("missing"))
    with pytest.raises(ReconciliationBatchError) as captured:
        batch.decision_for("anchor-later")  # type: ignore[arg-type]
    assert captured.value.code == "batch.anchor_id_type"


@pytest.mark.parametrize(
    "values,code",
    [
        ({"max_time_difference": 60}, "batch.window_type"),
        ({"max_time_difference": timedelta(seconds=-1)}, "batch.window_negative"),
        (
            {
                "max_time_difference": timedelta(0),
                "max_amount_difference": 1,
            },
            "batch.amount_tolerance_type",
        ),
        (
            {
                "max_time_difference": timedelta(0),
                "max_amount_difference": Decimal("-0.01"),
            },
            "batch.amount_tolerance_range",
        ),
        (
            {
                "max_time_difference": timedelta(0),
                "require_reference": 1,
            },
            "batch.require_reference_type",
        ),
    ],
)
def test_selection_policy_rejects_invalid_configuration(values, code):
    with pytest.raises(ReconciliationBatchError) as captured:
        CandidateSelectionPolicy(**values)

    assert captured.value.code == code


@pytest.mark.parametrize("anchors", [(), object(), "entry", (object(),)])
def test_reconciler_rejects_invalid_anchor_collections(
    batch_reconciler,
    anchors,
):
    with pytest.raises(ReconciliationBatchError):
        batch_reconciler.reconcile(BatchId("batch-invalid"), anchors, CandidateIndex(()))


def test_reconciler_rejects_duplicate_anchor_ids(make_entry, batch_reconciler):
    duplicate = make_entry("duplicate")

    with pytest.raises(ReconciliationBatchError) as captured:
        batch_reconciler.reconcile(
            BatchId("batch-duplicate"),
            (duplicate, duplicate),
            CandidateIndex(()),
        )

    assert captured.value.code == "batch.anchor_duplicate"


def test_batch_and_reconciler_are_immutable(mixed_batch, batch_reconciler):
    with pytest.raises(FrozenInstanceError):
        mixed_batch.batch_id = BatchId("changed")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        batch_reconciler.scorer = object()  # type: ignore[misc]


def test_policy_snapshot_rejects_duplicate_rule_identifiers():
    rules = (
        ScoringRuleSnapshot("ledgerloom.same_account", 10, True),
        ScoringRuleSnapshot("ledgerloom.same_account", 20, False),
    )

    with pytest.raises(ReconciliationBatchError) as captured:
        ReconciliationPolicySnapshot(
            rules,
            0,
            0,
            timedelta(0),
            Decimal("0"),
            False,
            ConflictStrategy.REJECT_ALL,
        )

    assert captured.value.code == "batch.policy_rule_duplicate"


def test_batch_rejects_scores_inconsistent_with_its_policy_snapshot(
    make_entry,
    batch_reconciler,
):
    anchor = make_entry("anchor")
    candidate = make_entry("candidate")
    decision = batch_reconciler.decision_policy.decide(
        anchor,
        batch_reconciler.scorer.rank(anchor, (candidate,)),
    )
    incompatible = ReconciliationPolicySnapshot(
        (ScoringRuleSnapshot("ledgerloom.same_account", 1, True),),
        0,
        0,
        timedelta(0),
        Decimal("0"),
        False,
        ConflictStrategy.REJECT_ALL,
    )

    with pytest.raises(ReconciliationBatchError) as captured:
        ReconciliationBatch(BatchId("batch-policy-mismatch"), (decision,), incompatible)

    assert captured.value.code == "batch.policy_rule_mismatch"


def test_policy_snapshot_exposes_score_scale_and_required_rules(batch_reconciler):
    snapshot = batch_reconciler.policy_snapshot

    assert snapshot.maximum_score == batch_reconciler.scorer.maximum_score
    assert snapshot.required_rule_ids == ("ledgerloom.same_account",)
