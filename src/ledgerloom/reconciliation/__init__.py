"""Deterministic candidate selection, scoring, and match decisions."""

from ledgerloom.reconciliation.batches import (
    BatchReconciler,
    BatchSelectionTrace,
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
from ledgerloom.reconciliation.report_v1 import (
    RECONCILIATION_REPORT_V1,
    build_reconciliation_report_document,
    encode_reconciliation_report,
)
from ledgerloom.reconciliation.reports import (
    CandidateReport,
    DecisionReport,
    ReasonCount,
    ReconciliationReport,
    ReconciliationSummary,
    SelectionReport,
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
    "RECONCILIATION_REPORT_V1",
    "BatchReconciler",
    "BatchSelectionTrace",
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
    "SelectionReport",
    "build_reconciliation_report",
    "build_reconciliation_report_document",
    "encode_reconciliation_report",
]
