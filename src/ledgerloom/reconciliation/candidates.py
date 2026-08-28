"""Immutable indexes for deterministic reconciliation candidate selection."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from types import MappingProxyType

from ledgerloom.domain.identifiers import AccountId, EntryId
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import LedgerEntry
from ledgerloom.domain.time import UtcTimestamp
from ledgerloom.reconciliation.errors import CandidateIndexError

type AmountKey = tuple[str, str]


def _entry_order(entry: LedgerEntry) -> tuple[UtcTimestamp, EntryId]:
    return (entry.occurred_at, entry.entry_id)


def _amount_key(amount: Money) -> AmountKey:
    return (amount.currency.code, amount.canonical_amount())


def _freeze_buckets[Key](
    buckets: dict[Key, list[LedgerEntry]],
) -> Mapping[Key, tuple[LedgerEntry, ...]]:
    return MappingProxyType(
        {key: tuple(sorted(entries, key=_entry_order)) for key, entries in buckets.items()}
    )


def _require_window(value: object, *, path: str) -> timedelta:
    if not isinstance(value, timedelta):
        raise CandidateIndexError(
            code="candidate.window_type",
            path=path,
            message="time window must be a timedelta",
        )
    if value < timedelta(0):
        raise CandidateIndexError(
            code="candidate.window_negative",
            path=path,
            message="time window must not be negative",
        )
    return value


def _require_amount_tolerance(value: object, *, path: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise CandidateIndexError(
            code="candidate.amount_tolerance_type",
            path=path,
            message="amount tolerance must be a Decimal",
        )
    if not value.is_finite() or value < 0:
        raise CandidateIndexError(
            code="candidate.amount_tolerance_range",
            path=path,
            message="amount tolerance must be finite and non-negative",
        )
    _, digits, exponent = value.as_tuple()
    integer_digits = max(len(digits) + exponent, 0)
    if len(digits) > 38 or max(-exponent, 0) > 18 or integer_digits > 38:
        raise CandidateIndexError(
            code="candidate.amount_tolerance_precision",
            path=path,
            message="amount tolerance exceeds the supported decimal precision",
        )
    return value


@dataclass(frozen=True, slots=True, init=False)
class CandidateIndex:
    """Index one immutable entry set and return candidates in canonical order.

    Every lookup is independent of construction order. Time-window boundaries
    are inclusive, which matches :class:`ledgerloom.rules.TimeWindowRule`.
    """

    _by_account: Mapping[AccountId, tuple[LedgerEntry, ...]]
    _by_amount: Mapping[AmountKey, tuple[LedgerEntry, ...]]
    _by_reference: Mapping[str, tuple[LedgerEntry, ...]]
    _amount_timelines: Mapping[str, tuple[LedgerEntry, ...]]
    _amount_values: Mapping[str, tuple[Decimal, ...]]
    _entries: tuple[LedgerEntry, ...]
    _timeline: tuple[LedgerEntry, ...]
    _timeline_values: tuple[datetime, ...]

    def __init__(self, entries: Iterable[LedgerEntry]) -> None:
        if isinstance(entries, str | bytes) or not isinstance(entries, Iterable):
            raise CandidateIndexError(
                code="candidate.entries_type",
                path="$.entries",
                message="entries must be an iterable of LedgerEntry values",
            )

        materialized = tuple(entries)
        if not all(isinstance(entry, LedgerEntry) for entry in materialized):
            raise CandidateIndexError(
                code="candidate.entry_type",
                path="$.entries",
                message="entries must contain only LedgerEntry values",
            )

        entry_ids = tuple(entry.entry_id for entry in materialized)
        if len(entry_ids) != len(set(entry_ids)):
            raise CandidateIndexError(
                code="candidate.duplicate_entry",
                path="$.entries",
                message="entry identifiers must be unique within an index",
            )

        account_buckets: dict[AccountId, list[LedgerEntry]] = {}
        amount_buckets: dict[AmountKey, list[LedgerEntry]] = {}
        currency_buckets: dict[str, list[LedgerEntry]] = {}
        reference_buckets: dict[str, list[LedgerEntry]] = {}
        for entry in materialized:
            account_buckets.setdefault(entry.account_id, []).append(entry)
            amount_buckets.setdefault(_amount_key(entry.amount), []).append(entry)
            currency_buckets.setdefault(entry.amount.currency.code, []).append(entry)
            if entry.reference is not None:
                reference_buckets.setdefault(entry.reference, []).append(entry)

        amount_timelines: dict[str, tuple[LedgerEntry, ...]] = {}
        for currency, currency_entries in sorted(currency_buckets.items()):
            amount_timelines[currency] = tuple(
                sorted(
                    currency_entries,
                    key=lambda entry: (entry.amount.amount, *_entry_order(entry)),
                )
            )

        ordered_entries = tuple(sorted(materialized, key=_entry_order))
        object.__setattr__(self, "_entries", ordered_entries)
        object.__setattr__(self, "_by_account", _freeze_buckets(account_buckets))
        object.__setattr__(self, "_by_amount", _freeze_buckets(amount_buckets))
        object.__setattr__(self, "_by_reference", _freeze_buckets(reference_buckets))
        object.__setattr__(self, "_amount_timelines", MappingProxyType(amount_timelines))
        object.__setattr__(
            self,
            "_amount_values",
            MappingProxyType(
                {
                    currency: tuple(entry.amount.amount for entry in entries)
                    for currency, entries in amount_timelines.items()
                }
            ),
        )
        object.__setattr__(self, "_timeline", ordered_entries)
        object.__setattr__(
            self,
            "_timeline_values",
            tuple(entry.occurred_at.value for entry in ordered_entries),
        )

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        """Return every indexed entry in canonical order."""

        return self._entries

    def by_account(self, account_id: AccountId) -> tuple[LedgerEntry, ...]:
        if not isinstance(account_id, AccountId):
            raise CandidateIndexError(
                code="candidate.account_type",
                path="$.account_id",
                message="account_id must be an AccountId",
            )
        return self._by_account.get(account_id, ())

    def by_amount(self, amount: Money) -> tuple[LedgerEntry, ...]:
        if not isinstance(amount, Money):
            raise CandidateIndexError(
                code="candidate.amount_type",
                path="$.amount",
                message="amount must be Money",
            )
        return self._by_amount.get(_amount_key(amount), ())

    def within_amount_tolerance(
        self,
        amount: Money,
        max_difference: Decimal,
    ) -> tuple[LedgerEntry, ...]:
        """Return same-currency amounts within an inclusive decimal tolerance."""

        if not isinstance(amount, Money):
            raise CandidateIndexError(
                code="candidate.amount_type",
                path="$.amount",
                message="amount must be Money",
            )
        tolerance = _require_amount_tolerance(max_difference, path="$.max_difference")
        with localcontext() as context:
            context.prec = 76
            start = amount.amount - tolerance
            end = amount.amount + tolerance
        currency = amount.currency.code
        values = self._amount_values.get(currency, ())
        timeline = self._amount_timelines.get(currency, ())
        first = bisect_left(values, start)
        last = bisect_right(values, end)
        selected_ids = {entry.entry_id for entry in timeline[first:last]}
        return tuple(entry for entry in self._entries if entry.entry_id in selected_ids)

    def by_reference(self, reference: str) -> tuple[LedgerEntry, ...]:
        if not isinstance(reference, str):
            raise CandidateIndexError(
                code="candidate.reference_type",
                path="$.reference",
                message="reference must be text",
            )
        return self._by_reference.get(reference, ())

    def within_time_window(
        self,
        occurred_at: UtcTimestamp,
        max_difference: timedelta,
    ) -> tuple[LedgerEntry, ...]:
        """Return entries at most ``max_difference`` from an instant, inclusively."""

        if not isinstance(occurred_at, UtcTimestamp):
            raise CandidateIndexError(
                code="candidate.timestamp_type",
                path="$.occurred_at",
                message="occurred_at must be a UtcTimestamp",
            )
        window = _require_window(max_difference, path="$.max_difference")
        try:
            start = occurred_at.value - window
        except OverflowError:
            start = datetime.min.replace(tzinfo=UTC)
        try:
            end = occurred_at.value + window
        except OverflowError:
            end = datetime.max.replace(tzinfo=UTC)
        first = bisect_left(self._timeline_values, start)
        last = bisect_right(self._timeline_values, end)
        return self._timeline[first:last]

    def candidates_for(
        self,
        entry: LedgerEntry,
        *,
        max_time_difference: timedelta,
        max_amount_difference: Decimal = Decimal("0"),
        require_reference: bool = False,
    ) -> tuple[LedgerEntry, ...]:
        """Intersect core indexes for one entry and exclude the entry itself.

        Candidates always share account and currency and fall within the inclusive
        amount and time tolerances. When ``require_reference`` is true, a missing
        reference yields no candidates and present references must match exactly.
        """

        if not isinstance(entry, LedgerEntry):
            raise CandidateIndexError(
                code="candidate.entry_type",
                path="$.entry",
                message="entry must be a LedgerEntry",
            )
        if not isinstance(require_reference, bool):
            raise CandidateIndexError(
                code="candidate.require_reference_type",
                path="$.require_reference",
                message="require_reference must be a boolean",
            )

        candidate_ids = {item.entry_id for item in self.by_account(entry.account_id)}
        candidate_ids.intersection_update(
            item.entry_id
            for item in self.within_amount_tolerance(entry.amount, max_amount_difference)
        )
        candidate_ids.intersection_update(
            item.entry_id
            for item in self.within_time_window(entry.occurred_at, max_time_difference)
        )
        if require_reference:
            if entry.reference is None:
                return ()
            candidate_ids.intersection_update(
                item.entry_id for item in self.by_reference(entry.reference)
            )
        candidate_ids.discard(entry.entry_id)
        return tuple(item for item in self._entries if item.entry_id in candidate_ids)
