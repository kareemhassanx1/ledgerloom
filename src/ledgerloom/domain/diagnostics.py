"""Structured validation outcomes for data boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from ledgerloom.domain.errors import DomainValidationError

_DIAGNOSTIC_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_FIELD_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_MAX_MESSAGE_LENGTH = 512


class DiagnosticSeverity(StrEnum):
    """Machine-stable diagnostic severity levels."""

    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A safe, structured explanation of one validation concern.

    Diagnostics deliberately omit rejected values. Callers can log them without
    accidentally exposing source data retained by ``DomainValidationError``.
    """

    code: str
    field: str
    message: str
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _DIAGNOSTIC_CODE_PATTERN.fullmatch(self.code):
            raise DomainValidationError(
                code="diagnostic.code",
                field="code",
                message="must be a dotted lowercase identifier",
                value=self.code,
            )
        if not isinstance(self.field, str) or not _FIELD_PATTERN.fullmatch(self.field):
            raise DomainValidationError(
                code="diagnostic.field",
                field="field",
                message="must be a lowercase field path",
                value=self.field,
            )
        if not isinstance(self.message, str):
            raise DomainValidationError(
                code="diagnostic.message_type",
                field="message",
                message="must be a string",
                value=self.message,
            )
        if (
            not self.message
            or self.message != self.message.strip()
            or not self.message.isprintable()
        ):
            raise DomainValidationError(
                code="diagnostic.message_format",
                field="message",
                message="must be non-empty printable text without surrounding whitespace",
                value=self.message,
            )
        if len(self.message) > _MAX_MESSAGE_LENGTH:
            raise DomainValidationError(
                code="diagnostic.message_too_long",
                field="message",
                message=f"must contain at most {_MAX_MESSAGE_LENGTH} characters",
                value=self.message,
            )
        if not isinstance(self.severity, DiagnosticSeverity):
            raise DomainValidationError(
                code="diagnostic.severity_type",
                field="severity",
                message="must be a DiagnosticSeverity",
                value=self.severity,
            )

    @classmethod
    def from_error(cls, error: DomainValidationError) -> Self:
        """Translate a domain exception without copying its rejected value."""

        if not isinstance(error, DomainValidationError):
            raise DomainValidationError(
                code="diagnostic.error_type",
                field="error",
                message="must be a DomainValidationError",
                value=error,
            )
        return cls(code=error.code, field=error.field, message=error.message)


@dataclass(frozen=True, slots=True)
class ValidationResult[T]:
    """An immutable success-with-warnings or failure-with-errors outcome."""

    value: T | None
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.diagnostics, tuple):
            raise DomainValidationError(
                code="validation_result.diagnostics_type",
                field="diagnostics",
                message="must be a tuple",
                value=self.diagnostics,
            )
        if not all(isinstance(item, Diagnostic) for item in self.diagnostics):
            raise DomainValidationError(
                code="validation_result.diagnostic_type",
                field="diagnostics",
                message="must contain only Diagnostic values",
                value=self.diagnostics,
            )

        errors = tuple(
            item for item in self.diagnostics if item.severity is DiagnosticSeverity.ERROR
        )
        if self.value is None and not errors:
            raise DomainValidationError(
                code="validation_result.missing_error",
                field="diagnostics",
                message="must contain an error when value is absent",
                value=self.diagnostics,
            )
        if self.value is not None and errors:
            raise DomainValidationError(
                code="validation_result.value_with_error",
                field="diagnostics",
                message="must not contain errors when value is present",
                value=self.diagnostics,
            )

    @classmethod
    def success(cls, value: T, *warnings: Diagnostic) -> Self:
        """Create a successful result, optionally carrying warnings."""

        return cls(value=value, diagnostics=warnings)

    @classmethod
    def failure(cls, *errors: Diagnostic) -> Self:
        """Create a failed result from one or more error diagnostics."""

        return cls(value=None, diagnostics=errors)

    @property
    def is_valid(self) -> bool:
        return self.value is not None
