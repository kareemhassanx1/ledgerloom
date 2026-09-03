"""Stable command-line workflows for local LedgerLoom batch runs."""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from enum import IntEnum
from pathlib import Path

from ledgerloom.application import (
    CatalogError,
    JournalCatalog,
    export_journal_jsonl,
)
from ledgerloom.domain import (
    BatchId,
    Diagnostic,
    DiagnosticSeverity,
    DomainValidationError,
    EntryId,
    LedgerBatch,
    LedgerEntry,
    SourceId,
    ValidationResult,
)
from ledgerloom.domain.errors import LedgerLoomError
from ledgerloom.ingest import (
    CsvImporter,
    JsonlImporter,
    SourceRecordMapper,
    reject_duplicate_source_records,
)
from ledgerloom.journal import JournalIntegrityError, JournalStorageError
from ledgerloom.reconciliation import (
    BatchReconciler,
    CandidateIndex,
    CandidateSelectionPolicy,
    ConflictPolicy,
    ConflictStrategy,
    DecisionPolicy,
    MatchScorer,
    ReconciliationClaims,
    ScoringRule,
    build_reconciliation_report,
    build_reconciliation_report_document,
)
from ledgerloom.rules import (
    AmountRule,
    DifferentSourceRule,
    ReferenceRule,
    SameAccountRule,
    TimeWindowRule,
)
from ledgerloom.serialization import (
    canonical_json_text,
    domain_v1_registry,
)
from ledgerloom.storage import SQLiteJournal, SQLiteJournalMode, StorageError


class ExitCode(IntEnum):
    """Process outcomes that form part of the CLI compatibility contract."""

    SUCCESS = 0
    INTERNAL_ERROR = 1
    INVALID_REQUEST = 2
    NOT_FOUND = 3
    STORAGE_FAILURE = 4
    PARTIAL_IMPORT = 5


@dataclass(frozen=True, slots=True)
class _CliError(LedgerLoomError):
    code: str
    path: str
    message: str
    exit_code: ExitCode = ExitCode.INVALID_REQUEST

    def __str__(self) -> str:
        return f"{self.path}: {self.message} [{self.code}]"


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _CliError("cli.usage", "$.arguments", message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="ledgerloom", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    import_parser = commands.add_parser("import", help="import CSV or JSON Lines records")
    import_parser.add_argument("input", type=Path)
    import_parser.add_argument("--database", type=Path, required=True)
    import_parser.add_argument("--source", required=True)
    import_parser.add_argument("--batch", required=True)
    import_parser.add_argument("--format", choices=("csv", "jsonl"))
    import_parser.add_argument("--delimiter", default=",")
    import_parser.set_defaults(handler=_run_import)

    reconcile_parser = commands.add_parser(
        "reconcile", help="reconcile one imported batch against the others"
    )
    reconcile_parser.add_argument("--database", type=Path, required=True)
    reconcile_parser.add_argument("--anchor-batch", required=True)
    reconcile_parser.add_argument("--batch", required=True)
    reconcile_parser.add_argument("--amount-tolerance", default="0.01")
    reconcile_parser.add_argument("--time-window-seconds", type=int, default=300)
    reconcile_parser.add_argument("--minimum-score", type=int, default=80)
    reconcile_parser.add_argument("--minimum-margin", type=int, default=10)
    reconcile_parser.add_argument("--require-reference", action="store_true")
    reconcile_parser.add_argument(
        "--conflict-strategy",
        choices=tuple(str(item) for item in ConflictStrategy),
        default=str(ConflictStrategy.REJECT_ALL),
    )
    reconcile_parser.set_defaults(handler=_run_reconcile)

    inspect_parser = commands.add_parser("inspect", help="verify and describe a journal")
    inspect_parser.add_argument("--database", type=Path, required=True)
    inspect_parser.set_defaults(handler=_run_inspect)

    export_parser = commands.add_parser("export", help="export verified journal events")
    export_parser.add_argument("--database", type=Path, required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument("--force", action="store_true")
    export_parser.set_defaults(handler=_run_export)
    return parser


def _entry_id(source_id: SourceId, record_id: object) -> EntryId:
    identity = f"{source_id}\0{record_id}".encode()
    return EntryId(f"entry-{hashlib.sha256(identity).hexdigest()}")


def _infer_format(path: Path, explicit: str | None) -> str:
    if explicit is not None:
        return explicit
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix in {".jsonl", ".ndjson"}:
        return "jsonl"
    raise _CliError(
        "cli.import_format",
        "$.input",
        "format must be supplied for files without a .csv, .jsonl, or .ndjson suffix",
    )


def _diagnostic_counts(batch: LedgerBatch) -> tuple[list[dict[str, object]], int, int]:
    counts: dict[str, int] = {}
    warnings = errors = 0
    for located in batch.diagnostics:
        diagnostic = located.diagnostic
        counts[diagnostic.code] = counts.get(diagnostic.code, 0) + 1
        if diagnostic.severity is DiagnosticSeverity.WARNING:
            warnings += 1
        else:
            errors += 1
    return ([{"code": code, "count": counts[code]} for code in sorted(counts)], warnings, errors)


def _run_import(arguments: argparse.Namespace) -> tuple[dict[str, object], ExitCode]:
    source_id = SourceId(arguments.source)
    batch_id = BatchId(arguments.batch)
    input_format = _infer_format(arguments.input, arguments.format)
    mapper = SourceRecordMapper(source_id)
    importer = (
        CsvImporter(mapper, delimiter=arguments.delimiter)
        if input_format == "csv"
        else JsonlImporter(mapper)
    )

    with SQLiteJournal(arguments.database) as journal:
        entries = journal.inspect_snapshot().require_valid()
        catalog = JournalCatalog.from_entries(entries)
        if catalog.contains_batch(batch_id):
            raise _CliError(
                "cli.batch_exists", "$.batch", "batch identifier already exists"
            )
        existing_source_keys = {
            entry.source_key for batch in catalog.batches for entry in batch.entries
        }
        existing_entry_ids = {
            entry.entry_id for batch in catalog.batches for entry in batch.entries
        }

        try:
            with arguments.input.open("r", encoding="utf-8", newline="") as stream:
                rows = tuple(reject_duplicate_source_records(importer.import_rows(stream)))
        except UnicodeError as error:
            raise _CliError(
                "cli.input_encoding", "$.input", "input must be valid UTF-8 text"
            ) from error
        except OSError as error:
            raise _CliError(
                "cli.input_read", "$.input", "input file could not be read"
            ) from error
        if not rows:
            raise _CliError(
                "cli.import_empty", "$.input", "input must contain at least one data record"
            )

        converted: list[ValidationResult[LedgerEntry]] = []
        accepted_source_keys = set(existing_source_keys)
        accepted_entry_ids = set(existing_entry_ids)
        for row in rows:
            record = row.record
            if record is None:
                converted.append(ValidationResult.failure(*row.diagnostics))
                continue
            resolved_entry_id = _entry_id(record.source_id, record.record_id)
            if record.source_key in accepted_source_keys:
                converted.append(
                    ValidationResult.failure(
                        Diagnostic(
                            "cli.import_duplicate",
                            "record_id",
                            "source record identifier already exists in the journal",
                        )
                    )
                )
                continue
            if resolved_entry_id in accepted_entry_ids:
                converted.append(
                    ValidationResult.failure(
                        Diagnostic(
                            "cli.import_entry_conflict",
                            "record_id",
                            "source record maps to an existing entry identifier",
                        )
                    )
                )
                continue
            try:
                ledger_entry = LedgerEntry.from_source_record(resolved_entry_id, record)
            except DomainValidationError as error:
                converted.append(ValidationResult.failure(Diagnostic.from_error(error)))
                continue
            converted.append(ValidationResult.success(ledger_entry, *row.diagnostics))
            accepted_source_keys.add(record.source_key)
            accepted_entry_ids.add(resolved_entry_id)

        batch = LedgerBatch(batch_id, source_id, tuple(converted))
        document = domain_v1_registry().encode(batch)
        appended = journal.append(document, expected_sequence=len(entries) + 1)

    diagnostic_counts, warnings, errors = _diagnostic_counts(batch)
    partial = not batch.is_complete
    result: dict[str, object] = {
        "accepted": batch.accepted_count,
        "batch_id": str(batch.batch_id),
        "diagnostic_counts": diagnostic_counts,
        "document_fingerprint": str(document.fingerprint),
        "errors": errors,
        "format": input_format,
        "journal_fingerprint": str(appended.fingerprint),
        "rejected": batch.rejected_count,
        "sequence": appended.sequence,
        "source_id": str(batch.source_id),
        "status": "partial" if partial else "complete",
        "total": len(batch.results),
        "warnings": warnings,
    }
    return result, ExitCode.PARTIAL_IMPORT if partial else ExitCode.SUCCESS


def _parse_decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise _CliError(
            "cli.decimal", "$.amount_tolerance", "amount tolerance must be decimal text"
        ) from error


def _default_reconciler(arguments: argparse.Namespace) -> BatchReconciler:
    tolerance = _parse_decimal(arguments.amount_tolerance)
    try:
        window = timedelta(seconds=arguments.time_window_seconds)
    except OverflowError as error:
        raise _CliError(
            "cli.time_window_range",
            "$.time_window_seconds",
            "time window is outside the supported duration range",
        ) from error
    return BatchReconciler(
        scorer=MatchScorer(
            (
                ScoringRule(DifferentSourceRule(), 10, required=True),
                ScoringRule(SameAccountRule(), 20, required=True),
                ScoringRule(AmountRule(tolerance), 40, required=True),
                ScoringRule(ReferenceRule(), 20),
                ScoringRule(TimeWindowRule(window), 10),
            )
        ),
        decision_policy=DecisionPolicy(
            minimum_score=arguments.minimum_score,
            minimum_margin=arguments.minimum_margin,
        ),
        selection_policy=CandidateSelectionPolicy(
            max_time_difference=window,
            max_amount_difference=tolerance,
            require_reference=arguments.require_reference,
        ),
        conflict_policy=ConflictPolicy(ConflictStrategy(arguments.conflict_strategy)),
    )


def _run_reconcile(arguments: argparse.Namespace) -> tuple[dict[str, object], ExitCode]:
    anchor_batch_id = BatchId(arguments.anchor_batch)
    result_batch_id = BatchId(arguments.batch)
    reconciler = _default_reconciler(arguments)
    with SQLiteJournal(arguments.database, mode=SQLiteJournalMode.OPEN) as journal:
        entries = journal.inspect_snapshot().require_valid()
        catalog = JournalCatalog.from_entries(entries)
        if catalog.contains_batch(result_batch_id):
            raise _CliError(
                "cli.batch_exists", "$.batch", "batch identifier already exists"
            )
        anchor_batch = next(
            (item for item in catalog.batches if item.batch_id == anchor_batch_id), None
        )
        if anchor_batch is None:
            raise _CliError(
                "cli.anchor_batch_not_found",
                "$.anchor_batch",
                "anchor batch was not found",
                ExitCode.NOT_FOUND,
            )
        if not anchor_batch.entries:
            raise _CliError(
                "cli.anchor_batch_empty",
                "$.anchor_batch",
                "anchor batch has no accepted entries",
            )
        claims = ReconciliationClaims.from_reports(catalog.reports)
        claims.require_available_anchors(anchor_batch.entries)
        candidates = tuple(
            entry
            for batch in catalog.batches
            if batch.batch_id != anchor_batch.batch_id
            for entry in batch.entries
        )
        reconciliation = reconciler.reconcile(
            result_batch_id,
            anchor_batch.entries,
            CandidateIndex(candidates),
        )
        report = claims.resolve(build_reconciliation_report(reconciliation))
        document = build_reconciliation_report_document(report)
        appended = journal.append(document, expected_sequence=len(entries) + 1)

    summary = report.summary
    return {
        "ambiguous": summary.ambiguous,
        "anchor_batch_id": str(anchor_batch.batch_id),
        "batch_id": str(report.batch_id),
        "candidate_entries": len(candidates),
        "conflict": summary.conflict,
        "coverage_basis_points": summary.coverage_basis_points,
        "document_fingerprint": str(document.fingerprint),
        "evaluated_candidates": summary.evaluated_candidates,
        "journal_fingerprint": str(appended.fingerprint),
        "matched": summary.matched,
        "sequence": appended.sequence,
        "total": summary.total,
        "unmatched": summary.unmatched,
    }, ExitCode.SUCCESS


def _run_inspect(arguments: argparse.Namespace) -> tuple[dict[str, object], ExitCode]:
    with SQLiteJournal(
        arguments.database,
        mode=SQLiteJournalMode.READ_ONLY,
        verify_on_open=False,
    ) as journal:
        snapshot = journal.inspect_snapshot()
        report = snapshot.integrity
        entries = snapshot.verified_entries
    events = [
        {
            "document_fingerprint": str(entry.document.fingerprint),
            "journal_fingerprint": str(entry.fingerprint),
            "schema": str(entry.document.schema.name),
            "sequence": entry.sequence,
            "version": str(entry.document.schema.version),
        }
        for entry in entries
    ]
    return {
        "entry_count": len(entries),
        "events": events,
        "head_fingerprint": (
            None if report.head_fingerprint is None else str(report.head_fingerprint)
        ),
        "integrity": {
            "is_valid": report.is_valid,
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "path": issue.path,
                    "sequence": issue.sequence,
                }
                for issue in report.issues
            ],
            "valid_entries": report.valid_entries,
        },
    }, ExitCode.SUCCESS if report.is_valid else ExitCode.STORAGE_FAILURE


def _run_export(arguments: argparse.Namespace) -> tuple[dict[str, object], ExitCode]:
    database = arguments.database.resolve()
    with SQLiteJournal(arguments.database, mode=SQLiteJournalMode.READ_ONLY) as journal:
        entries = journal.inspect_snapshot().require_valid()
    protected = (
        database,
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
        Path(f"{database}-journal"),
    )
    exported = export_journal_jsonl(
        entries,
        arguments.output,
        force=arguments.force,
        protected_paths=protected,
    )
    return {
        "bytes": exported.bytes_written,
        "entry_count": exported.entry_count,
        "fingerprint": str(exported.fingerprint),
        "format": "ledgerloom-journal-jsonl-v1",
        "output": str(exported.output),
    }, ExitCode.SUCCESS


def _command_name(arguments: Sequence[str]) -> str:
    return arguments[0] if arguments and arguments[0] in {
        "import", "reconcile", "inspect", "export"
    } else "ledgerloom"


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(canonical_json_text(payload) + "\n")


def _error_payload(command: str, error: object) -> dict[str, object]:
    return {
        "command": command,
        "error": {
            "code": getattr(error, "code", "cli.internal"),
            "message": getattr(error, "message", "unexpected internal error"),
            "path": getattr(error, "path", getattr(error, "field", "$")),
        },
        "ok": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and emit exactly one canonical JSON result document."""

    raw_arguments = tuple(sys.argv[1:] if argv is None else argv)
    command = _command_name(raw_arguments)
    try:
        arguments = _build_parser().parse_args(raw_arguments)
        result, exit_code = arguments.handler(arguments)
        _emit({"command": arguments.command, "ok": True, "result": result})
        return int(exit_code)
    except _CliError as error:
        _emit(_error_payload(command, error))
        return int(error.exit_code)
    except JournalIntegrityError as error:
        _emit(_error_payload(command, error))
        return int(ExitCode.STORAGE_FAILURE)
    except CatalogError as error:
        _emit(_error_payload(command, error))
        return int(ExitCode.STORAGE_FAILURE)
    except JournalStorageError as error:
        _emit(_error_payload(command, error))
        exit_code = (
            ExitCode.NOT_FOUND
            if error.code == "journal.storage_not_found"
            else ExitCode.STORAGE_FAILURE
        )
        return int(exit_code)
    except StorageError as error:
        _emit(_error_payload(command, error))
        return int(ExitCode.STORAGE_FAILURE)
    except LedgerLoomError as error:
        _emit(_error_payload(command, error))
        return int(ExitCode.INVALID_REQUEST)
    except OSError:
        _emit(
            _error_payload(
                command,
                _CliError("cli.io_error", "$", "file operation failed"),
            )
        )
        return int(ExitCode.INVALID_REQUEST)
    except Exception:
        _emit(_error_payload(command, object()))
        return int(ExitCode.INTERNAL_ERROR)


__all__ = ["ExitCode", "main"]
