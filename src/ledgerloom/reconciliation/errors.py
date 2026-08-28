"""Stable failures exposed by candidate selection."""

from __future__ import annotations

from ledgerloom.domain.errors import LedgerLoomError


class CandidateIndexError(LedgerLoomError, ValueError):
    """Describe an invalid candidate-index operation."""

    def __init__(self, *, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message} [{code}]")
