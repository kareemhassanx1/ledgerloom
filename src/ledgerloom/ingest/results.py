"""Immutable row-level outcomes shared by streaming import adapters."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ledgerloom.domain.diagnostics import (
    Diagnostic,
    DiagnosticSeverity,
    ValidationResult,
)
from ledgerloom.domain.records import SourceRecord
from ledgerloom.ingest.errors import IngestError

_DIAGNOSTIC_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


def _require_positive_integer(value: object, *, path: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IngestError(
            code="ingest.position_type",
            path=path,
            message=f"{label} must be an integer",
        )
    if value < 1:
        raise IngestError(
            code="ingest.position_range",
            path=path,
            message=f"{label} must be at least one",
        )
    return value


@dataclass(frozen=True, slots=True, order=True)
class ImportLocation:
    """Stable one-based logical record and physical starting-line position."""

    record_number: int
    line_number: int

    def __post_init__(self) -> None:
        _require_positive_integer(
            self.record_number,
            path="$.location.record_number",
            label="record number",
        )
        _require_positive_integer(
            self.line_number,
            path="$.location.line_number",
            label="line number",
        )


@dataclass(frozen=True, slots=True)
class ImportRowResult:
    """One source record outcome without retaining rejected source values."""

    location: ImportLocation
    validation: ValidationResult[SourceRecord]

    def __post_init__(self) -> None:
        if not isinstance(self.location, ImportLocation):
            raise IngestError(
                code="ingest.location_type",
                path="$.result.location",
                message="location must be an ImportLocation",
            )
        if not isinstance(self.validation, ValidationResult):
            raise IngestError(
                code="ingest.validation_type",
                path="$.result.validation",
                message="validation must be a ValidationResult",
            )
        if self.validation.value is not None and not isinstance(
            self.validation.value, SourceRecord
        ):
            raise IngestError(
                code="ingest.value_type",
                path="$.result.validation.value",
                message="successful imports must contain a SourceRecord",
            )

    @property
    def record(self) -> SourceRecord | None:
        return self.validation.value

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return self.validation.diagnostics

    @property
    def is_valid(self) -> bool:
        return self.validation.is_valid


@dataclass(frozen=True, slots=True, order=True)
class ImportDiagnosticCount:
    """Count one stable diagnostic code across a consumed import stream."""

    code: str
    count: int

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _DIAGNOSTIC_CODE_PATTERN.fullmatch(
            self.code
        ):
            raise IngestError(
                code="ingest.summary_code",
                path="$.summary.diagnostic_counts.code",
                message="diagnostic code must be a dotted lowercase identifier",
            )
        _require_positive_integer(
            self.count,
            path="$.summary.diagnostic_counts.count",
            label="diagnostic count",
        )


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Deterministic aggregate produced after a stream has been consumed."""

    total: int
    accepted: int
    rejected: int
    warnings: int
    errors: int
    diagnostic_counts: tuple[ImportDiagnosticCount, ...] = ()

    def __post_init__(self) -> None:
        for name in ("total", "accepted", "rejected", "warnings", "errors"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise IngestError(
                    code="ingest.summary_count_type",
                    path=f"$.summary.{name}",
                    message=f"{name} must be an integer",
                )
            if value < 0:
                raise IngestError(
                    code="ingest.summary_count_range",
                    path=f"$.summary.{name}",
                    message=f"{name} must not be negative",
                )
        if self.accepted + self.rejected != self.total:
            raise IngestError(
                code="ingest.summary_result_mismatch",
                path="$.summary",
                message="accepted and rejected counts must sum to total",
            )
        if self.errors < self.rejected:
            raise IngestError(
                code="ingest.summary_error_mismatch",
                path="$.summary.errors",
                message="every rejected result must contribute at least one error",
            )
        if not isinstance(self.diagnostic_counts, tuple) or not all(
            isinstance(item, ImportDiagnosticCount) for item in self.diagnostic_counts
        ):
            raise IngestError(
                code="ingest.summary_diagnostics_type",
                path="$.summary.diagnostic_counts",
                message="diagnostic_counts must contain ImportDiagnosticCount values",
            )
        if tuple(sorted(self.diagnostic_counts)) != self.diagnostic_counts:
            raise IngestError(
                code="ingest.summary_diagnostics_order",
                path="$.summary.diagnostic_counts",
                message="diagnostic counts must be ordered by code",
            )
        codes = tuple(item.code for item in self.diagnostic_counts)
        if len(codes) != len(set(codes)):
            raise IngestError(
                code="ingest.summary_diagnostics_duplicate",
                path="$.summary.diagnostic_counts",
                message="diagnostic codes must be unique",
            )
        if sum(item.count for item in self.diagnostic_counts) != self.warnings + self.errors:
            raise IngestError(
                code="ingest.summary_diagnostic_mismatch",
                path="$.summary.diagnostic_counts",
                message="diagnostic counts must equal warning and error totals",
            )


def summarize_import(rows: Iterable[ImportRowResult]) -> ImportSummary:
    """Consume a row stream and return aggregate counts without retaining records."""

    if isinstance(rows, str | bytes) or not isinstance(rows, Iterable):
        raise IngestError(
            code="ingest.results_type",
            path="$.rows",
            message="rows must be an iterable of ImportRowResult values",
        )
    total = accepted = rejected = warnings = errors = 0
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, ImportRowResult):
            raise IngestError(
                code="ingest.result_type",
                path="$.rows",
                message="rows must contain only ImportRowResult values",
            )
        total += 1
        if row.is_valid:
            accepted += 1
        else:
            rejected += 1
        for diagnostic in row.diagnostics:
            counts[diagnostic.code] = counts.get(diagnostic.code, 0) + 1
            if diagnostic.severity is DiagnosticSeverity.WARNING:
                warnings += 1
            else:
                errors += 1
    diagnostic_counts = tuple(
        ImportDiagnosticCount(code, counts[code]) for code in sorted(counts)
    )
    return ImportSummary(
        total,
        accepted,
        rejected,
        warnings,
        errors,
        diagnostic_counts,
    )
