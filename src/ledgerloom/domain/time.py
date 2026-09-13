"""Timezone-safe timestamps and half-open time ranges."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Self

from ledgerloom.domain.errors import DomainValidationError


@dataclass(frozen=True, slots=True, order=True)
class UtcTimestamp:
    """An instant normalized to UTC at construction time."""

    value: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.value, datetime):
            raise DomainValidationError(
                code="timestamp.type",
                field="timestamp",
                message="must be a datetime",
                value=self.value,
            )
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise DomainValidationError(
                code="timestamp.naive",
                field="timestamp",
                message="must include a UTC offset",
                value=self.value,
            )
        object.__setattr__(self, "value", self.value.astimezone(UTC))

    @classmethod
    def parse(cls, value: str) -> Self:
        """Parse an ISO 8601 timestamp that includes an explicit offset."""

        if not isinstance(value, str):
            raise DomainValidationError(
                code="timestamp.parse_type",
                field="timestamp",
                message="must be ISO 8601 text",
                value=value,
            )
        if not value or value != value.strip():
            raise DomainValidationError(
                code="timestamp.parse_format",
                field="timestamp",
                message="must be canonical ISO 8601 text without surrounding whitespace",
                value=value,
            )
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise DomainValidationError(
                code="timestamp.parse_format",
                field="timestamp",
                message="must be valid ISO 8601 text",
                value=value,
            ) from error
        return cls(parsed)

    @classmethod
    def epoch(cls) -> Self:
        return cls(datetime(1970, 1, 1, tzinfo=UTC))

    def canonical(self) -> str:
        """Render the instant in UTC using the shortest lossless precision."""

        timespec = "microseconds" if self.value.microsecond else "seconds"
        return self.value.isoformat(timespec=timespec).replace("+00:00", "Z")

    def shift(self, delta: timedelta) -> Self:
        if not isinstance(delta, timedelta):
            raise DomainValidationError(
                code="timestamp.delta_type",
                field="delta",
                message="must be a timedelta",
                value=delta,
            )
        try:
            return type(self)(self.value + delta)
        except OverflowError as error:
            raise DomainValidationError(
                code="timestamp.out_of_range",
                field="delta",
                message="moves the timestamp outside the supported datetime range",
                value=delta,
            ) from error

    def __str__(self) -> str:
        return self.canonical()


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A non-empty half-open interval: ``start <= value < end``."""

    start: UtcTimestamp
    end: UtcTimestamp

    def __post_init__(self) -> None:
        if not isinstance(self.start, UtcTimestamp):
            raise DomainValidationError(
                code="time_range.start_type",
                field="start",
                message="must be a UtcTimestamp",
                value=self.start,
            )
        if not isinstance(self.end, UtcTimestamp):
            raise DomainValidationError(
                code="time_range.end_type",
                field="end",
                message="must be a UtcTimestamp",
                value=self.end,
            )
        if self.start >= self.end:
            raise DomainValidationError(
                code="time_range.order",
                field="end",
                message="must be later than start",
                value=self.end,
            )

    @property
    def duration(self) -> timedelta:
        return self.end.value - self.start.value

    def contains(self, value: UtcTimestamp) -> bool:
        if not isinstance(value, UtcTimestamp):
            raise DomainValidationError(
                code="time_range.value_type",
                field="timestamp",
                message="must be a UtcTimestamp",
                value=value,
            )
        return self.start <= value < self.end

    def overlaps(self, other: TimeRange) -> bool:
        if not isinstance(other, TimeRange):
            raise DomainValidationError(
                code="time_range.other_type",
                field="other",
                message="must be a TimeRange",
                value=other,
            )
        return self.start < other.end and other.start < self.end

    def intersection(self, other: TimeRange) -> TimeRange | None:
        """Return the shared interval, or ``None`` for disjoint ranges."""

        if not self.overlaps(other):
            return None
        return TimeRange(max(self.start, other.start), min(self.end, other.end))
