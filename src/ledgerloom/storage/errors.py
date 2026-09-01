"""Stable failures exposed by durable storage adapters."""

from __future__ import annotations

from ledgerloom.domain.errors import LedgerLoomError


class StorageError(LedgerLoomError, ValueError):
    """Describe an invalid or failed durable-storage operation."""

    def __init__(self, *, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message} [{code}]")
