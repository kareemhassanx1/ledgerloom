"""Stable failures exposed by journal implementations."""

from __future__ import annotations

from ledgerloom.domain.errors import LedgerLoomError


class JournalError(LedgerLoomError, ValueError):
    """Describe an invalid journal operation using a stable code and path."""

    def __init__(self, *, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message} [{code}]")


class JournalConflictError(JournalError):
    """Raised when an append uses a stale expected sequence."""


class JournalIntegrityError(JournalError):
    """Raised when stored entries no longer form a valid hash chain."""


class JournalReplayError(JournalError):
    """Raised when a verified entry cannot be decoded for replay."""
