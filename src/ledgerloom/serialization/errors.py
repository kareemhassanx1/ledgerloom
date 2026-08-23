"""Stable errors raised at serialization boundaries."""

from __future__ import annotations

from ledgerloom.domain.errors import LedgerLoomError


class SerializationError(LedgerLoomError, ValueError):
    """Describe why a value or document cannot cross a serialized boundary."""

    def __init__(self, *, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message} [{code}]")

