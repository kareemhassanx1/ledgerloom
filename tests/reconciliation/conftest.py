from datetime import timedelta
from decimal import Decimal

import pytest

from ledgerloom.domain.identifiers import BatchId
from ledgerloom.reconciliation import (
    BatchReconciler,
    CandidateIndex,
    CandidateSelectionPolicy,
    ConflictPolicy,
    ConflictStrategy,
    DecisionPolicy,
    MatchScorer,
    ScoringRule,
)
from ledgerloom.rules import AmountRule, ReferenceRule, SameAccountRule


@pytest.fixture
def batch_reconciler() -> BatchReconciler:
    return BatchReconciler(
        scorer=MatchScorer(
            (
                ScoringRule(SameAccountRule(), 20, required=True),
                ScoringRule(AmountRule(Decimal("0.01")), 50),
                ScoringRule(ReferenceRule(), 30),
            )
        ),
        decision_policy=DecisionPolicy(minimum_score=60, minimum_margin=10),
        selection_policy=CandidateSelectionPolicy(
            max_time_difference=timedelta(minutes=5),
            max_amount_difference=Decimal("0.01"),
        ),
        conflict_policy=ConflictPolicy(),
    )


@pytest.fixture
def mixed_batch(make_entry, batch_reconciler):
    anchors = (
        make_entry(
            "anchor-unmatched",
            amount="20",
            reference="invoice-missing",
            occurred_at="2026-08-30T10:05:00Z",
        ),
        make_entry(
            "anchor-conflict-b",
            amount="40",
            reference="invoice-shared",
            occurred_at="2026-08-30T10:04:30Z",
        ),
        make_entry(
            "anchor-ambiguous",
            amount="30",
            reference=None,
            occurred_at="2026-08-30T10:02:30Z",
        ),
        make_entry(
            "anchor-match",
            amount="10",
            reference="invoice-match",
            occurred_at="2026-08-30T10:01:00Z",
        ),
        make_entry(
            "anchor-conflict-a",
            amount="40",
            reference="invoice-shared",
            occurred_at="2026-08-30T10:03:30Z",
        ),
    )
    candidates = (
        make_entry(
            "candidate-ambiguous-b",
            source_id="processor",
            amount="30",
            reference=None,
            occurred_at="2026-08-30T10:03:00Z",
        ),
        make_entry(
            "candidate-shared",
            source_id="processor",
            amount="40",
            reference="invoice-shared",
            occurred_at="2026-08-30T10:04:00Z",
        ),
        make_entry(
            "candidate-match",
            source_id="processor",
            amount="10",
            reference="invoice-match",
            occurred_at="2026-08-30T10:00:00Z",
        ),
        make_entry(
            "candidate-ambiguous-a",
            source_id="processor",
            amount="30",
            reference=None,
            occurred_at="2026-08-30T10:02:00Z",
        ),
    )
    return batch_reconciler.reconcile(
        BatchId("reconcile-2026-08-30"),
        anchors,
        CandidateIndex(candidates),
    )


@pytest.fixture
def preferred_conflict_batch(make_entry, batch_reconciler):
    reconciler = BatchReconciler(
        batch_reconciler.scorer,
        batch_reconciler.decision_policy,
        batch_reconciler.selection_policy,
        ConflictPolicy(ConflictStrategy.PREFER_HIGHER_SCORE),
    )
    strong = make_entry(
        "anchor-strong",
        amount="50",
        reference="invoice-shared",
        occurred_at="2026-08-30T11:00:00Z",
    )
    weak = make_entry(
        "anchor-weak",
        amount="50",
        reference=None,
        occurred_at="2026-08-30T11:01:00Z",
    )
    shared = make_entry(
        "candidate-shared",
        source_id="processor",
        amount="50",
        reference="invoice-shared",
        occurred_at="2026-08-30T11:00:30Z",
    )
    return reconciler.reconcile(
        BatchId("reconcile-preferred-conflict"),
        (weak, strong),
        CandidateIndex((shared,)),
    )
