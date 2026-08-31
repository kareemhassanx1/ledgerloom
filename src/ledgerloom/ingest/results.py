"""Immutable row-level outcomes shared by streaming import adapters."""

from __future__ import annotations

from dataclasses import dataclass

from ledgerloom.domain.diagnostics import Diagnostic, ValidationResult
from ledgerloom.domain.records import SourceRecord
from ledgerloom.ingest.errors import IngestError


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
