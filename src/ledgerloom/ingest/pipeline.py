"""Composable streaming validation stages for imported rows."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from ledgerloom.domain.diagnostics import Diagnostic, ValidationResult
from ledgerloom.domain.identifiers import SourceId, SourceRecordId
from ledgerloom.ingest.errors import IngestError
from ledgerloom.ingest.results import ImportRowResult


def reject_duplicate_source_records(
    rows: Iterable[ImportRowResult],
) -> Iterator[ImportRowResult]:
    """Reject later valid records with a previously accepted source-scoped key.

    Invalid rows pass through unchanged because they do not contain a trusted
    source identity. The set grows only as valid unique records are consumed,
    and results remain lazy.
    """

    if isinstance(rows, str | bytes) or not isinstance(rows, Iterable):
        raise IngestError(
            code="ingest.results_type",
            path="$.rows",
            message="rows must be an iterable of ImportRowResult values",
        )
    return _reject_duplicate_source_records(rows)


def _reject_duplicate_source_records(
    rows: Iterable[ImportRowResult],
) -> Iterator[ImportRowResult]:
    seen: set[tuple[SourceId, SourceRecordId]] = set()
    for row in rows:
        if not isinstance(row, ImportRowResult):
            raise IngestError(
                code="ingest.result_type",
                path="$.rows",
                message="rows must contain only ImportRowResult values",
            )
        record = row.record
        if record is None:
            yield row
            continue
        if record.source_key in seen:
            yield ImportRowResult(
                row.location,
                ValidationResult.failure(
                    Diagnostic(
                        "ingest.duplicate_source_record",
                        "record_id",
                        "source record identifier has already been accepted",
                    )
                ),
            )
            continue
        seen.add(record.source_key)
        yield row
