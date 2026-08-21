from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ledgerloom.domain.errors import DomainValidationError
from ledgerloom.domain.time import TimeRange, UtcTimestamp


def test_normalizes_positive_offset_to_utc():
    source = datetime(2026, 8, 21, 15, 30, tzinfo=timezone(timedelta(hours=3)))

    timestamp = UtcTimestamp(source)

    assert timestamp.value == datetime(2026, 8, 21, 12, 30, tzinfo=UTC)
    assert timestamp.value.tzinfo is UTC


def test_normalizes_negative_offset_to_utc_across_day_boundary():
    source = datetime(2026, 8, 21, 22, 30, tzinfo=timezone(timedelta(hours=-4)))

    timestamp = UtcTimestamp(source)

    assert timestamp.canonical() == "2026-08-22T02:30:00Z"


def test_rejects_naive_datetime():
    with pytest.raises(DomainValidationError) as captured:
        UtcTimestamp(datetime(2026, 8, 21, 12, 30))

    assert captured.value.code == "timestamp.naive"


def test_rejects_non_datetime_value():
    with pytest.raises(DomainValidationError) as captured:
        UtcTimestamp("2026-08-21T12:30:00Z")  # type: ignore[arg-type]

    assert captured.value.code == "timestamp.type"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1970-01-01T00:00:00Z", "1970-01-01T00:00:00Z"),
        ("2026-08-21T15:30:00+03:00", "2026-08-21T12:30:00Z"),
        ("2026-08-21T12:30:00.123456+00:00", "2026-08-21T12:30:00.123456Z"),
    ],
)
def test_parse_accepts_offset_aware_iso_text(text, expected):
    assert UtcTimestamp.parse(text).canonical() == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        " 2026-08-21T12:30:00Z",
        "2026-08-21T12:30:00Z ",
        "not-a-timestamp",
        "2026-08-21T12:30:00",
    ],
)
def test_parse_rejects_invalid_or_offset_free_text(text):
    with pytest.raises(DomainValidationError) as captured:
        UtcTimestamp.parse(text)

    assert captured.value.code in {"timestamp.parse_format", "timestamp.naive"}


def test_epoch_returns_unix_epoch():
    assert UtcTimestamp.epoch().canonical() == "1970-01-01T00:00:00Z"


def test_shift_returns_a_new_timestamp():
    original = UtcTimestamp.parse("2026-08-21T12:00:00Z")

    shifted = original.shift(timedelta(minutes=90))

    assert shifted.canonical() == "2026-08-21T13:30:00Z"
    assert original.canonical() == "2026-08-21T12:00:00Z"


def test_shift_rejects_non_timedelta():
    with pytest.raises(DomainValidationError) as captured:
        UtcTimestamp.epoch().shift(60)  # type: ignore[arg-type]

    assert captured.value.code == "timestamp.delta_type"


def test_shift_translates_datetime_overflow_to_domain_error():
    final_instant = UtcTimestamp(datetime.max.replace(tzinfo=UTC))

    with pytest.raises(DomainValidationError) as captured:
        final_instant.shift(timedelta(microseconds=1))

    assert captured.value.code == "timestamp.out_of_range"


def test_timestamp_is_immutable_and_orderable():
    earlier = UtcTimestamp.parse("2026-08-21T12:00:00Z")
    later = UtcTimestamp.parse("2026-08-21T13:00:00Z")

    with pytest.raises(FrozenInstanceError):
        earlier.value = later.value  # type: ignore[misc]

    assert earlier < later
    assert sorted([later, earlier]) == [earlier, later]


def make_range(start: str, end: str) -> TimeRange:
    return TimeRange(UtcTimestamp.parse(start), UtcTimestamp.parse(end))


def test_time_range_requires_end_after_start():
    timestamp = UtcTimestamp.parse("2026-08-21T12:00:00Z")

    with pytest.raises(DomainValidationError) as captured:
        TimeRange(timestamp, timestamp)

    assert captured.value.code == "time_range.order"


def test_time_range_uses_half_open_boundaries():
    interval = make_range("2026-08-21T12:00:00Z", "2026-08-21T13:00:00Z")

    assert interval.contains(UtcTimestamp.parse("2026-08-21T12:00:00Z"))
    assert interval.contains(UtcTimestamp.parse("2026-08-21T12:59:59.999999Z"))
    assert not interval.contains(UtcTimestamp.parse("2026-08-21T13:00:00Z"))


def test_adjacent_time_ranges_do_not_overlap():
    first = make_range("2026-08-21T12:00:00Z", "2026-08-21T13:00:00Z")
    second = make_range("2026-08-21T13:00:00Z", "2026-08-21T14:00:00Z")

    assert not first.overlaps(second)
    assert first.intersection(second) is None


def test_intersection_returns_shared_half_open_range():
    first = make_range("2026-08-21T12:00:00Z", "2026-08-21T14:00:00Z")
    second = make_range("2026-08-21T13:00:00Z", "2026-08-21T15:00:00Z")

    overlap = first.intersection(second)

    assert overlap == make_range("2026-08-21T13:00:00Z", "2026-08-21T14:00:00Z")


def test_range_duration_uses_normalized_instants():
    interval = make_range("2026-08-21T12:00:00+03:00", "2026-08-21T12:30:00+02:00")

    assert interval.duration == timedelta(hours=1, minutes=30)

