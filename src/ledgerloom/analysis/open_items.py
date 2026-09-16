"""Unsettled imported entries with actual last-decision evidence and UTC aging."""

from ledgerloom.analysis.balances import amount_text, amount_units
from ledgerloom.analysis.settlements import catalog_entries
from ledgerloom.application.catalog import JournalCatalog
from ledgerloom.application.errors import ApplicationError
from ledgerloom.domain import UtcTimestamp
from ledgerloom.reconciliation import ReconciliationClaims


def open_items(catalog: JournalCatalog, *, as_of: UtcTimestamp) -> tuple[dict[str, object], ...]:
    """Age current snapshot claims, not a reconstruction of historical match state.

    Future-dated source entries are explicit rather than silently disappearing.
    Both sides of a committed match are removed from the open inventory.
    """
    if not isinstance(as_of, UtcTimestamp):
        raise ApplicationError("analysis.as_of", "$.as_of", "expected an explicit UTC timestamp")
    entries = catalog_entries(catalog)
    claimed = ReconciliationClaims.from_reports(catalog.reports).claimed_entry_ids
    latest = {
        decision.anchor_id: (report.batch_id, decision)
        for report in catalog.reports
        for decision in report.decisions
    }
    rows: list[dict[str, object]] = []
    for identifier, entry in sorted(entries.items(), key=lambda item: str(item[0])):
        if identifier in claimed:
            continue
        previous = latest.get(identifier)
        elapsed = as_of.value - entry.occurred_at.value
        future = elapsed.total_seconds() < 0
        age = None if future else elapsed.days
        bucket = (
            "future"
            if age is None
            else "0-7"
            if age <= 7
            else "8-30"
            if age <= 30
            else "31-90"
            if age <= 90
            else "91+"
        )
        rows.append(
            {
                "entry": str(identifier),
                "source": str(entry.source_id),
                "source_record": str(entry.source_record_id),
                "account": str(entry.account_id),
                "currency": str(entry.amount.currency),
                "amount": amount_text(amount_units(entry.amount.amount)),
                "occurred_at": entry.occurred_at.canonical(),
                "age_days": age,
                "age_bucket": bucket,
                "last_report": None if previous is None else str(previous[0]),
                "status": "unreviewed" if previous is None else str(previous[1].status),
                "reason": None if previous is None else previous[1].reason_code,
            }
        )
    return tuple(rows)
