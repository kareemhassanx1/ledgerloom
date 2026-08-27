"""Stable failures exposed by matching rule definitions and evaluations."""

from __future__ import annotations

from ledgerloom.domain.errors import LedgerLoomError


class RuleError(LedgerLoomError, ValueError):
    """Describe an invalid rule operation using a stable code and path."""

    def __init__(self, *, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message} [{code}]")
