"""Deterministic financial audit views bound to one verified journal head."""

import hashlib
from collections.abc import Iterable

from ledgerloom.analysis.balances import source_balances
from ledgerloom.analysis.control_totals import StatementControl, check_statement_controls
from ledgerloom.analysis.open_items import open_items
from ledgerloom.analysis.period_balances import period_balances
from ledgerloom.analysis.report_changes import report_changes
from ledgerloom.analysis.settlements import catalog_entries, settlement_variances
from ledgerloom.analysis.source_coverage import source_coverage
from ledgerloom.application.catalog import JournalCatalog
from ledgerloom.application.errors import ApplicationError
from ledgerloom.domain import BatchId, TimeRange, UtcTimestamp
from ledgerloom.journal import JournalEntry
from ledgerloom.serialization import canonical_json_bytes


def build_audit_pack(
    entries: Iterable[JournalEntry],
    *,
    as_of: UtcTimestamp,
    period: TimeRange | None = None,
    controls: Iterable[StatementControl] | None = None,
    compare_reports: tuple[BatchId, BatchId] | None = None,
) -> dict[str, object]:
    """Verify before analysis. Source occurrence dates do not date journal commitments."""
    if period is not None and not isinstance(period, TimeRange):
        raise ApplicationError("period.type", "$.period", "period must be a TimeRange")
    snapshot = tuple(entries)
    catalog = JournalCatalog.from_entries(snapshot)
    imported = tuple(catalog_entries(catalog).values())
    opened = open_items(catalog, as_of=as_of)
    coverage = source_coverage(catalog)
    variance = settlement_variances(catalog)
    change: dict[str, object] | None = None
    if compare_reports is not None:
        if (
            not isinstance(compare_reports, tuple)
            or len(compare_reports) != 2
            or not all(isinstance(item, BatchId) for item in compare_reports)
        ):
            raise ApplicationError(
                "audit.report_pair", "$.compare_reports", "expected two BatchIds"
            )
        reports = {report.batch_id: report for report in catalog.reports}
        if any(identifier not in reports for identifier in compare_reports):
            raise ApplicationError("audit.report_missing", "$.compare_reports", "report not found")
        change = report_changes(reports[compare_reports[0]], reports[compare_reports[1]])
    payload: dict[str, object] = {
        "schema": "ledgerloom.financial_audit.v1",
        "as_of": as_of.canonical(),
        "state_basis": "current_journal_snapshot; as_of_is_source_age_only",
        "entry_count": len(snapshot),
        "head_fingerprint": str(snapshot[-1].fingerprint) if snapshot else None,
        "balances": list(source_balances(imported)),
        "open_items": list(opened),
        "source_coverage": list(coverage),
        "settlements": list(variance),
        "unknown_schemas": [str(name) for name in catalog.unrecognized_schema_names],
        "statement_controls": None
        if controls is None
        else check_statement_controls(imported, controls),
        "period": None
        if period is None
        else {
            "start": period.start.canonical(),
            "end": period.end.canonical(),
            "balances": list(period_balances(imported, period)),
        },
        "report_changes": change,
    }
    return {
        **payload,
        "fingerprint": "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    }
