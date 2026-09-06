from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ledgerloom.exception_aging import (
    AgingBucket,
    LedgerException,
    amount_by_currency,
    build_aging_report,
)


def test_aging_prioritizes_old_material_unowned_exception() -> None:
    now = datetime(2026, 9, 6, tzinfo=UTC)
    report = build_aging_report(
        [
            LedgerException("new", "1000", now, Decimal("10"), "usd", owner="a"),
            LedgerException("old", "2000", now - timedelta(days=12), Decimal("20000"), "usd"),
        ],
        now=now,
    )
    assert report.exceptions[0].exception.exception_id == "old"
    assert report.exceptions[0].bucket is AgingBucket.CRITICAL
    assert amount_by_currency(report) == {"USD": Decimal("20010")}
