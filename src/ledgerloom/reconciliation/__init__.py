"""Deterministic candidate selection, scoring, and match decisions."""

from ledgerloom.reconciliation.batches import (
    BatchReconciler,
    CandidateSelectionPolicy,
    ReconciliationBatch,
    ReconciliationPolicySnapshot,
    ScoringRuleSnapshot,
)
from ledgerloom.reconciliation.candidates import (
    CandidateIndex,
    CandidateSelection,
    CandidateSelectionTrace,
)
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
    ReconciliationBatchError,
    ReconciliationError,
    ReconciliationReportError,
    ScoringError,
)
from ledgerloom.reconciliation.reports import (
    CandidateReport,
    DecisionReport,
    ReasonCount,
    ReconciliationReport,
    ReconciliationSummary,
    build_reconciliation_report,
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
    "BatchReconciler",
    "CandidateIndex",
    "CandidateIndexError",
    "CandidateReport",
    "CandidateScore",
    "CandidateSelection",
    "CandidateSelectionPolicy",
    "CandidateSelectionTrace",
    "ConflictPolicy",
    "ConflictStrategy",
    "DecisionError",
    "DecisionPolicy",
    "DecisionReport",
    "DecisionStatus",
    "MatchDecision",
    "MatchScorer",
    "ReasonCount",
    "ReconciliationBatch",
    "ReconciliationBatchError",
    "ReconciliationError",
    "ReconciliationPolicySnapshot",
    "ReconciliationReport",
    "ReconciliationReportError",
    "ReconciliationSummary",
    "ScoringError",
    "ScoringRule",
    "ScoringRuleSnapshot",
    "build_reconciliation_report",
]
