from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from ledgerloom.application.errors import ApplicationError
from ledgerloom.exception_aging import (
    AgingBucket,
    AgingPolicy,
    LedgerException,
    amount_by_currency,
    build_aging_report,
)


def test_aging_amounts_and_materiality_are_exact_under_low_precision() -> None:
    now = datetime(2026, 9, 17, tzinfo=UTC)
    items = [
        LedgerException("a", "1000", now, Decimal("9999.99"), "usd"),
        LedgerException("b", "1000", now, Decimal("-0.01"), "usd"),
    ]
    with localcontext() as context:
        context.prec = 3
        report = build_aging_report(iter(items), policy=AgingPolicy(), now=now)
        totals = amount_by_currency(report)
    assert report.total_absolute_amount == Decimal("10000")
    assert totals == {"USD": Decimal("9999.98")}
    assert all("material-amount" not in item.reasons for item in report.exceptions)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("1E-19")])
def test_exception_amounts_reject_nonfinite_or_unsupported_scale(value) -> None:
    with pytest.raises(ApplicationError):
        LedgerException("a", "1000", datetime(2026, 9, 17, tzinfo=UTC), value, "USD")


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
