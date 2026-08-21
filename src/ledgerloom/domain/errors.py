"""Exceptions raised when domain values cannot be constructed."""

from __future__ import annotations

from typing import Any


class LedgerLoomError(Exception):
    """Base class for errors intentionally exposed by LedgerLoom."""


class DomainValidationError(LedgerLoomError, ValueError):
    """Describe one invariant violation in a domain value.

    The stable ``code`` and ``field`` attributes let importers translate the
    failure into structured row diagnostics without parsing an error message.
    ``value`` is retained for local diagnostics and is deliberately omitted
    from ``str(error)`` because source values may contain sensitive data.
    """

    def __init__(
        self,
        *,
        code: str,
        field: str,
        message: str,
        value: Any = None,
    ) -> None:
        self.code = code
        self.field = field
        self.message = message
        self.value = value
        super().__init__(f"{field}: {message} [{code}]")

