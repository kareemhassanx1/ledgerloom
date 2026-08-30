"""Stable failures exposed by reconciliation services."""

from __future__ import annotations

from ledgerloom.domain.errors import LedgerLoomError


class ReconciliationError(LedgerLoomError, ValueError):
    """Describe an invalid reconciliation operation."""

    def __init__(self, *, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message} [{code}]")


class CandidateIndexError(ReconciliationError):
    """Describe an invalid candidate-index operation."""


class ScoringError(ReconciliationError):
    """Describe invalid score configuration, inputs, or results."""


class DecisionError(ReconciliationError):
    """Describe an invalid decision or conflict policy operation."""


class ReconciliationBatchError(ReconciliationError):
    """Describe invalid batch configuration, inputs, or outcomes."""


class ReconciliationReportError(ReconciliationError):
    """Describe an inconsistent structured reconciliation report."""
