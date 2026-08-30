from datetime import timedelta
from decimal import Decimal

import pytest

from ledgerloom.domain.identifiers import AccountId, EntryId, SourceId, SourceRecordId
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import LedgerEntry
from ledgerloom.domain.time import UtcTimestamp
from ledgerloom.reconciliation import (
    CandidateIndex,
    CandidateIndexError,
    CandidateSelectionTrace,
)


def entry(
    entry_id: str,
    *,
    account: str = "assets:checking",
    amount: str = "10",
    currency: str = "USD",
    occurred_at: str = "2026-08-28T12:00:00Z",
    reference: str | None = "invoice-42",
) -> LedgerEntry:
    return LedgerEntry(
        EntryId(entry_id),
        SourceId("bank"),
        SourceRecordId(f"record-{entry_id}"),
        AccountId(account),
        Money.parse(amount, currency),
        UtcTimestamp.parse(occurred_at),
        reference,
    )


def ids(entries):
    return tuple(str(item.entry_id) for item in entries)


def test_selection_trace_explains_each_narrowing_stage():
    anchor = entry("anchor")
    index = CandidateIndex(
        (
            anchor,
            entry("selected", occurred_at="2026-08-28T12:00:30Z"),
            entry("other-reference", reference="invoice-99"),
            entry("outside-time", occurred_at="2026-08-28T13:00:00Z"),
            entry("outside-amount", amount="11"),
            entry("other-account", account="liabilities:card"),
        )
    )

    selection = index.select_for(
        anchor,
        max_time_difference=timedelta(minutes=1),
        require_reference=True,
    )

    assert ids(selection.candidates) == ("selected",)
    assert selection.trace == CandidateSelectionTrace(6, 4, 3, 2, 1, 1)


def test_existing_candidate_lookup_delegates_to_traced_selection():
    anchor = entry("anchor")
    candidate = entry("candidate")
    index = CandidateIndex((candidate, anchor))
    options = {
        "max_time_difference": timedelta(0),
        "max_amount_difference": Decimal("0"),
        "require_reference": True,
    }

    assert index.candidates_for(anchor, **options) == index.select_for(
        anchor, **options
    ).candidates


def test_indexes_account_amount_and_present_reference():
    matching = entry("entry-1")
    other_account = entry("entry-2", account="liabilities:card")
    missing_reference = entry("entry-3", reference=None)
    index = CandidateIndex((other_account, missing_reference, matching))

    assert ids(index.by_account(AccountId("assets:checking"))) == ("entry-1", "entry-3")
    assert ids(index.by_amount(Money.parse("10.00", "USD"))) == (
        "entry-1",
        "entry-2",
        "entry-3",
    )
    assert ids(index.by_reference("invoice-42")) == ("entry-1", "entry-2")


def test_amount_index_keeps_currency_and_sign_distinct():
    index = CandidateIndex(
        (
            entry("usd-positive"),
            entry("eur-positive", currency="EUR"),
            entry("usd-negative", amount="-10"),
        )
    )

    assert ids(index.by_amount(Money.parse("10", "USD"))) == ("usd-positive",)


def test_amount_tolerance_is_inclusive_and_preserves_canonical_order():
    index = CandidateIndex(
        (
            entry("upper-outside", amount="10.010001", occurred_at="2026-08-28T11:00:00Z"),
            entry("upper-boundary", amount="10.01", occurred_at="2026-08-28T13:00:00Z"),
            entry("lower-boundary", amount="9.99", occurred_at="2026-08-28T12:00:00Z"),
            entry("other-currency", amount="10", currency="EUR"),
        )
    )

    found = index.within_amount_tolerance(Money.parse("10", "USD"), Decimal("0.01"))

    assert ids(found) == ("lower-boundary", "upper-boundary")


def test_time_window_is_inclusive_and_excludes_one_microsecond_beyond_each_side():
    index = CandidateIndex(
        (
            entry("before-outside", occurred_at="2026-08-28T09:59:59.999999Z"),
            entry("before-boundary", occurred_at="2026-08-28T10:00:00Z"),
            entry("after-boundary", occurred_at="2026-08-28T14:00:00Z"),
            entry("after-outside", occurred_at="2026-08-28T14:00:00.000001Z"),
        )
    )

    found = index.within_time_window(
        UtcTimestamp.parse("2026-08-28T12:00:00Z"),
        timedelta(hours=2),
    )

    assert ids(found) == ("before-boundary", "after-boundary")


def test_zero_time_window_returns_all_entries_at_the_exact_instant():
    index = CandidateIndex(
        (
            entry("entry-b"),
            entry("entry-a"),
            entry("entry-c", occurred_at="2026-08-28T12:00:00.000001Z"),
        )
    )

    found = index.within_time_window(
        UtcTimestamp.parse("2026-08-28T12:00:00Z"),
        timedelta(0),
    )

    assert ids(found) == ("entry-a", "entry-b")


def test_maximum_time_window_clamps_to_supported_datetime_bounds():
    index = CandidateIndex(
        (
            entry("first", occurred_at="0001-01-01T00:00:00Z"),
            entry("last", occurred_at="9999-12-31T23:59:59.999999Z"),
        )
    )

    found = index.within_time_window(UtcTimestamp.epoch(), timedelta.max)

    assert ids(found) == ("first", "last")


def test_construction_order_never_changes_lookup_order():
    entries = (
        entry("entry-c", occurred_at="2026-08-28T13:00:00Z"),
        entry("entry-b", occurred_at="2026-08-28T12:00:00Z"),
        entry("entry-a", occurred_at="2026-08-28T12:00:00Z"),
    )

    forward = CandidateIndex(entries)
    reverse = CandidateIndex(reversed(entries))

    assert ids(forward.entries) == ("entry-a", "entry-b", "entry-c")
    assert reverse.entries == forward.entries
    assert reverse.by_amount(Money.parse("10", "USD")) == forward.by_amount(
        Money.parse("10", "USD")
    )


def test_candidates_for_intersects_indexes_excludes_self_and_optionally_matches_reference():
    anchor = entry("anchor", reference="invoice-42")
    exact = entry("exact", reference="invoice-42", occurred_at="2026-08-28T12:05:00Z")
    other_reference = entry(
        "other-reference",
        reference="invoice-99",
        occurred_at="2026-08-28T12:04:00Z",
    )
    outside = entry("outside", occurred_at="2026-08-28T12:05:00.000001Z")
    index = CandidateIndex((outside, other_reference, exact, anchor))

    without_reference = index.candidates_for(
        anchor,
        max_time_difference=timedelta(minutes=5),
    )
    with_reference = index.candidates_for(
        anchor,
        max_time_difference=timedelta(minutes=5),
        require_reference=True,
    )

    assert ids(without_reference) == ("other-reference", "exact")
    assert ids(with_reference) == ("exact",)


def test_candidates_for_uses_the_same_inclusive_amount_tolerance_as_amount_rule():
    anchor = entry("anchor", amount="10")
    boundary = entry("boundary", amount="10.01")
    outside = entry("outside", amount="10.010001")
    index = CandidateIndex((outside, boundary, anchor))

    found = index.candidates_for(
        anchor,
        max_time_difference=timedelta(0),
        max_amount_difference=Decimal("0.01"),
    )

    assert ids(found) == ("boundary",)


def test_candidates_for_missing_required_reference_is_empty():
    anchor = entry("anchor", reference=None)
    index = CandidateIndex((anchor, entry("candidate", reference=None)))

    assert index.candidates_for(
        anchor,
        max_time_difference=timedelta(0),
        require_reference=True,
    ) == ()


def test_duplicate_entry_identifiers_are_rejected():
    with pytest.raises(CandidateIndexError) as captured:
        CandidateIndex((entry("duplicate"), entry("duplicate", reference="other")))

    assert captured.value.code == "candidate.duplicate_entry"


@pytest.mark.parametrize("window", [-1, timedelta(microseconds=-1)])
def test_invalid_time_windows_are_rejected(window):
    index = CandidateIndex(())

    with pytest.raises(CandidateIndexError):
        index.within_time_window(UtcTimestamp.epoch(), window)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "tolerance",
    [1, Decimal("-0.01"), Decimal("Infinity"), Decimal("1E+38"), Decimal("1E-19")],
)
def test_invalid_amount_tolerances_are_rejected(tolerance):
    index = CandidateIndex(())

    with pytest.raises(CandidateIndexError):
        index.within_amount_tolerance(
            Money.parse("10", "USD"),
            tolerance,  # type: ignore[arg-type]
        )
