"""Opening and closing observed balances across half-open UTC periods."""

from collections.abc import Iterable

from ledgerloom.analysis.balances import (
    BalanceKey,
    amount_text,
    amount_units,
    balance_key,
    checked_entries,
)
from ledgerloom.application.errors import ApplicationError
from ledgerloom.domain import LedgerEntry, TimeRange


def period_balances(
    entries: Iterable[LedgerEntry], period: TimeRange
) -> tuple[dict[str, object], ...]:
    """Compute opening + movement = closing from imported history, not bank certification."""
    if not isinstance(period, TimeRange):
        raise ApplicationError("period.type", "$.period", "period must be a TimeRange")
    groups: dict[BalanceKey, list[int]] = {}
    for entry in checked_entries(entries):
        if entry.occurred_at >= period.end:
            continue
        counters = groups.setdefault(balance_key(entry), [0, 0, 0, 0])
        units = amount_units(entry.amount.amount)
        if entry.occurred_at < period.start:
            counters[0] += units
        else:
            counters[1 if units >= 0 else 2] += abs(units)
            counters[3] += 1
    return tuple(
        {
            "source": key[0],
            "account": key[1],
            "currency": key[2],
            "opening": amount_text(c[0]),
            "inflow": amount_text(c[1]),
            "outflow": amount_text(c[2]),
            "movement": amount_text(c[1] - c[2]),
            "closing": amount_text(c[0] + c[1] - c[2]),
            "period_count": c[3],
        }
        for key, c in sorted(groups.items())
    )
