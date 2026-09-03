"""Stable errors raised by application-level journal workflows."""

from __future__ import annotations

from dataclasses import dataclass

from ledgerloom.domain.errors import LedgerLoomError


@dataclass(frozen=True, slots=True)
class CatalogError(LedgerLoomError):
    """A journal is valid structurally but unusable by this application version."""

    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message} [{self.code}]"

