"""Stable failures exposed by import configuration and stream boundaries."""

from __future__ import annotations

from ledgerloom.domain.errors import LedgerLoomError


class IngestError(LedgerLoomError, ValueError):
    """Describe an invalid importer configuration or unusable stream."""

    def __init__(self, *, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message} [{code}]")
