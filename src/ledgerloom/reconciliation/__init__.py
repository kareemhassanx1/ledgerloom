"""Deterministic candidate selection, scoring, and match decisions."""

from ledgerloom.reconciliation.candidates import CandidateIndex
from ledgerloom.reconciliation.decisions import (
    ConflictPolicy,
    ConflictStrategy,
    DecisionPolicy,
    DecisionStatus,
    MatchDecision,
)
from ledgerloom.reconciliation.errors import (
    CandidateIndexError,
    DecisionError,
    ReconciliationError,
    ScoringError,
)
from ledgerloom.reconciliation.scoring import (
    BASIS_POINT_SCALE,
    MAX_RULE_WEIGHT,
    MAX_TOTAL_SCORE,
    CandidateScore,
    MatchScorer,
    ScoringRule,
)

__all__ = [
    "BASIS_POINT_SCALE",
    "MAX_RULE_WEIGHT",
    "MAX_TOTAL_SCORE",
    "CandidateIndex",
    "CandidateIndexError",
    "CandidateScore",
    "ConflictPolicy",
    "ConflictStrategy",
    "DecisionError",
    "DecisionPolicy",
    "DecisionStatus",
    "MatchDecision",
    "MatchScorer",
    "ReconciliationError",
    "ScoringError",
    "ScoringRule",
]
