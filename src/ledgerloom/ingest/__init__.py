"""Streaming external-record import with structured row diagnostics."""

from ledgerloom.ingest.csv import CsvHeaderPolicy, CsvImporter, CsvRowPolicy
from ledgerloom.ingest.errors import IngestError
from ledgerloom.ingest.jsonl import JsonlImporter, JsonlPolicy
from ledgerloom.ingest.mapping import SourceRecordColumns, SourceRecordMapper
from ledgerloom.ingest.pipeline import reject_duplicate_source_records
from ledgerloom.ingest.results import (
    ImportDiagnosticCount,
    ImportLocation,
    ImportRowResult,
    ImportSummary,
    summarize_import,
)

__all__ = [
    "CsvHeaderPolicy",
    "CsvImporter",
    "CsvRowPolicy",
    "ImportDiagnosticCount",
    "ImportLocation",
    "ImportRowResult",
    "ImportSummary",
    "IngestError",
    "JsonlImporter",
    "JsonlPolicy",
    "SourceRecordColumns",
    "SourceRecordMapper",
    "reject_duplicate_source_records",
    "summarize_import",
]
