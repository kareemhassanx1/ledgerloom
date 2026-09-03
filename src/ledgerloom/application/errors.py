"""Stable errors raised by application-level journal workflows."""

from __future__ import annotations

from dataclasses import dataclass

from ledgerloom.domain.errors import LedgerLoomError


@dataclass(frozen=True, slots=True)
class ApplicationError(LedgerLoomError):
    """A stable failure raised by a reusable application workflow."""

    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message} [{self.code}]"


class CatalogError(ApplicationError):
    """A journal is valid structurally but unusable by this application version."""


class ExportError(ApplicationError):
    """A verified journal export could not be published safely."""
