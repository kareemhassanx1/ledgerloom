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
from typing import Never

from ledgerloom.analysis.control_totals import StatementControl
from ledgerloom.application import (
    CatalogError,
    JournalCatalog,
    export_journal_jsonl,
)
from ledgerloom.application.audit_pack import build_audit_pack
from ledgerloom.assurance.evidence import build_close_evidence
from ledgerloom.assurance.planning import CapacityWindow
from ledgerloom.domain import (
    AccountId,
    BatchId,
    Currency,
    Diagnostic,
    DiagnosticSeverity,
    DomainValidationError,
    EntryId,
    LedgerBatch,
    LedgerEntry,
    SourceId,
    TimeRange,
    UtcTimestamp,
    ValidationResult,
)
from ledgerloom.domain.errors import LedgerLoomError
from ledgerloom.ingest import (
    CsvImporter,
    ImportRowResult,
    JsonlImporter,
    SourceRecordMapper,
    reject_duplicate_source_records,
)
from ledgerloom.journal import (
    JournalConflictError,
    JournalIntegrityError,
    JournalStorageError,
)
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
from ledgerloom.serialization.fingerprints import ContentFingerprint
from ledgerloom.storage import SQLiteJournal, SQLiteJournalMode, StorageError
from ledgerloom.storage.backup import backup_journal


class ExitCode(IntEnum):
    """Process outcomes that form part of the CLI compatibility contract."""

    SUCCESS = 0
    INTERNAL_ERROR = 1
    INVALID_REQUEST = 2
    NOT_FOUND = 3
    STORAGE_FAILURE = 4
    PARTIAL_IMPORT = 5
    CONFLICT = 6


DEFAULT_MAX_IMPORT_RECORDS = 100_000


@dataclass(frozen=True, slots=True)
class _CliError(LedgerLoomError):
    code: str
    path: str
    message: str
    exit_code: ExitCode = ExitCode.INVALID_REQUEST

    def __str__(self) -> str:
        return f"{self.path}: {self.message} [{self.code}]"


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise _CliError("cli.usage", "$.arguments", message)


def _positive_record_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if limit < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return limit


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
    import_parser.add_argument(
        "--max-records",
        type=_positive_record_limit,
        default=DEFAULT_MAX_IMPORT_RECORDS,
        metavar="COUNT",
        help=(
            "reject inputs containing more than COUNT records "
            f"(default: {DEFAULT_MAX_IMPORT_RECORDS})"
        ),
    )
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
    inspect_parser.add_argument("--expected-count", type=int)
    inspect_parser.add_argument("--expected-head")
    inspect_parser.set_defaults(handler=_run_inspect)

    export_parser = commands.add_parser("export", help="export verified journal events")
    export_parser.add_argument("--database", type=Path, required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument("--force", action="store_true")
    export_parser.set_defaults(handler=_run_export)

    audit_parser = commands.add_parser(
        "audit", help="review source balances and settlement evidence"
    )
    audit_parser.add_argument("--database", type=Path, required=True)
    audit_parser.add_argument(
        "--as-of", required=True, help="explicit timestamp used for source aging"
    )
    audit_parser.add_argument("--period-start")
    audit_parser.add_argument("--period-end")
    audit_parser.add_argument(
        "--control",
        nargs=6,
        action="append",
        default=[],
        metavar=("SOURCE", "ACCOUNT", "CURRENCY", "COUNT", "INFLOW", "OUTFLOW"),
    )
    audit_parser.add_argument("--compare-reports", nargs=2, metavar=("BEFORE", "AFTER"))
    audit_parser.add_argument("--expected-count", type=int)
    audit_parser.add_argument("--expected-head")
    audit_parser.add_argument(
        "--close-evidence",
        action="store_true",
        help="include journal-bound close assessment and review plan",
    )
    audit_parser.add_argument("--review-due-at")
    audit_parser.add_argument("--review-capacity", type=_positive_record_limit)
    audit_parser.add_argument("--review-currency", action="append", default=[])
    audit_parser.add_argument("--evidence-actor")
    audit_parser.set_defaults(handler=_run_audit)

    backup_parser = commands.add_parser("backup", help="create a verified online SQLite backup")
    backup_parser.add_argument("--database", type=Path, required=True)
    backup_parser.add_argument("--output", type=Path, required=True)
    backup_parser.add_argument("--expected-count", type=int)
    backup_parser.add_argument("--expected-head")
    backup_parser.set_defaults(handler=_run_backup)
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
            raise _CliError("cli.batch_exists", "$.batch", "batch identifier already exists")
        existing_source_keys = {
            entry.source_key for batch in catalog.batches for entry in batch.entries
        }
        existing_entry_ids = {
            entry.entry_id for batch in catalog.batches for entry in batch.entries
        }

        try:
            with arguments.input.open("r", encoding="utf-8", newline="") as stream:
                rows: list[ImportRowResult] = []
                for row in reject_duplicate_source_records(importer.import_rows(stream)):
                    if len(rows) >= arguments.max_records:
                        raise _CliError(
                            "cli.import_record_limit",
                            "$.input",
                            "input exceeds the configured maximum record count",
                        )
                    rows.append(row)
        except UnicodeError as error:
            raise _CliError(
                "cli.input_encoding", "$.input", "input must be valid UTF-8 text"
            ) from error
        except OSError as error:
            raise _CliError("cli.input_read", "$.input", "input file could not be read") from error
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
            raise _CliError("cli.batch_exists", "$.batch", "batch identifier already exists")
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
    expected_head = None
    if arguments.expected_head is not None:
        try:
            expected_head = ContentFingerprint.parse(arguments.expected_head)
        except LedgerLoomError as error:
            raise _CliError(
                "cli.expected_head",
                "$.expected_head",
                "expected head must be a sha256 fingerprint",
            ) from error
    with SQLiteJournal(
        arguments.database,
        mode=SQLiteJournalMode.READ_ONLY,
        verify_on_open=False,
    ) as journal:
        snapshot = journal.inspect_snapshot(
            expected_count=arguments.expected_count,
            expected_head=expected_head,
        )
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


def _run_audit(arguments: argparse.Namespace) -> tuple[dict[str, object], ExitCode]:
    assurance_options = (
        arguments.review_due_at,
        arguments.review_capacity,
        arguments.review_currency,
        arguments.evidence_actor,
    )
    if arguments.close_evidence:
        if not arguments.review_due_at or not arguments.evidence_actor:
            raise _CliError(
                "cli.close_evidence",
                "$.close_evidence",
                "close evidence requires review-due-at and evidence-actor",
            )
        if bool(arguments.review_capacity) != bool(arguments.review_currency):
            raise _CliError(
                "cli.review_capacity",
                "$.review_capacity",
                "review capacity and supported currencies must be supplied together",
            )
    elif any(assurance_options):
        raise _CliError(
            "cli.close_evidence",
            "$.close_evidence",
            "review and actor options require close-evidence",
        )
    if bool(arguments.period_start) != bool(arguments.period_end):
        raise _CliError("cli.period", "$.period", "both period boundaries are required")
    period = (
        None
        if arguments.period_start is None
        else TimeRange(
            UtcTimestamp.parse(arguments.period_start), UtcTimestamp.parse(arguments.period_end)
        )
    )
    controls = []
    for source, account, currency, count, inflow, outflow in arguments.control:
        try:
            controls.append(
                StatementControl(
                    SourceId(source),
                    AccountId(account),
                    Currency(currency),
                    int(count),
                    Decimal(inflow),
                    Decimal(outflow),
                )
            )
        except (ValueError, InvalidOperation) as error:
            raise _CliError("cli.control", "$.control", "invalid numeric control value") from error
    with SQLiteJournal(arguments.database, mode=SQLiteJournalMode.READ_ONLY) as journal:
        entries = journal.inspect_snapshot(
            expected_count=arguments.expected_count,
            expected_head=None
            if arguments.expected_head is None
            else ContentFingerprint.parse(arguments.expected_head),
        ).require_valid()
    comparisons = (
        None
        if arguments.compare_reports is None
        else (BatchId(arguments.compare_reports[0]), BatchId(arguments.compare_reports[1]))
    )
    result = build_audit_pack(
        entries,
        as_of=UtcTimestamp.parse(arguments.as_of),
        period=period,
        controls=controls if arguments.control else None,
        compare_reports=comparisons,
    )
    if arguments.close_evidence:
        as_of = UtcTimestamp.parse(arguments.as_of)
        due_at = UtcTimestamp.parse(arguments.review_due_at)
        try:
            windows = (
                ()
                if arguments.review_capacity is None
                else (
                    CapacityWindow(
                        "declared-review-desk",
                        as_of.value,
                        due_at.value,
                        arguments.review_capacity,
                        frozenset(
                            "currency:" + str(Currency(value))
                            for value in arguments.review_currency
                        ),
                    ),
                )
            )
            evidence = build_close_evidence(
                entries,
                as_of=as_of,
                due_at=due_at,
                controls=controls or None,
                windows=windows,
                actor=arguments.evidence_actor,
            )
            return {"audit": result, "close_evidence": evidence}, ExitCode.SUCCESS
        except ValueError as error:
            raise _CliError("cli.close_evidence", "$.close_evidence", str(error)) from error
    return result, ExitCode.SUCCESS


def _run_backup(arguments: argparse.Namespace) -> tuple[dict[str, object], ExitCode]:
    result = backup_journal(
        arguments.database,
        arguments.output,
        expected_count=arguments.expected_count,
        expected_head=None
        if arguments.expected_head is None
        else ContentFingerprint.parse(arguments.expected_head),
    )
    return result, ExitCode.SUCCESS


def _command_name(arguments: Sequence[str]) -> str:
    return (
        arguments[0]
        if arguments
        and arguments[0] in {"import", "reconcile", "inspect", "export", "audit", "backup"}
        else "ledgerloom"
    )


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
    except JournalConflictError as error:
        _emit(_error_payload(command, error))
        return int(ExitCode.CONFLICT)
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
