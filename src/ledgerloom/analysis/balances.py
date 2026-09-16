"""Exact source/account/currency totals without ambient Decimal rounding."""

from collections.abc import Iterable
from decimal import Decimal

from ledgerloom.application.errors import ApplicationError
from ledgerloom.domain import LedgerEntry

type BalanceKey = tuple[str, str, str]
SCALE = 18


def amount_units(value: Decimal) -> int:
    """Use integer arithmetic; bound magnitude before allocating large integers."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ApplicationError("analysis.amount", "$.amount", "amount must be a finite Decimal")
    sign, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)
    if exponent < -SCALE or len(digits) + exponent > 80:
        raise ApplicationError(
            "analysis.amount_range", "$.amount", "amount exceeds 80 integer or 18 decimal places"
        )
    coefficient = int("".join(map(str, digits)))
    return (-1 if sign else 1) * coefficient * int(10 ** (exponent + SCALE))


def amount_text(units: int) -> str:
    """Render exact totals as JSON-safe decimal strings, normalizing negative zero."""
    whole, fraction = divmod(abs(units), 10**SCALE)
    text = f"{whole}.{fraction:018d}".rstrip("0").rstrip(".")
    return ("-" if units < 0 else "") + text


def checked_entries(entries: Iterable[LedgerEntry]) -> tuple[LedgerEntry, ...]:
    """Snapshot one-pass inputs and reject double counting at the public boundary."""
    values = tuple(entries)
    identifiers = set()
    source_keys = set()
    for entry in values:
        if not isinstance(entry, LedgerEntry):
            raise ApplicationError("analysis.entry_type", "$.entries", "expected ledger entries")
        if entry.entry_id in identifiers or entry.source_key in source_keys:
            raise ApplicationError("analysis.duplicate", "$.entries", "entry identity is repeated")
        identifiers.add(entry.entry_id)
        source_keys.add(entry.source_key)
        amount_units(entry.amount.amount)
    return values


def balance_key(entry: LedgerEntry) -> BalanceKey:
    return str(entry.source_id), str(entry.account_id), str(entry.amount.currency)


def source_balances(entries: Iterable[LedgerEntry]) -> tuple[dict[str, object], ...]:
    """Separate signed inflows/outflows per source; never combine currencies or sources."""
    groups: dict[BalanceKey, list[int]] = {}
    for entry in checked_entries(entries):
        counts = groups.setdefault(balance_key(entry), [0, 0, 0])
        units = amount_units(entry.amount.amount)
        counts[0] += 1
        counts[1 if units >= 0 else 2] += abs(units)
    return tuple(
        {
            "source": source,
            "account": account,
            "currency": currency,
            "count": counts[0],
            "inflow": amount_text(counts[1]),
            "outflow": amount_text(counts[2]),
            "net": amount_text(counts[1] - counts[2]),
        }
        for (source, account, currency), counts in sorted(groups.items())
    )
