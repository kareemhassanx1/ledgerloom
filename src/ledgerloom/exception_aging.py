"""Age financial reconciliation exceptions into operational buckets."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class AgingBucket(StrEnum):
    CURRENT = "current"
    WATCH = "watch"
    OVERDUE = "overdue"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class LedgerException:
    exception_id: str
    account: str
    opened_at: datetime
    amount: Decimal
    currency: str
    due_on: date | None = None
    owner: str | None = None
    disputed: bool = False

    def __post_init__(self) -> None:
        if not self.exception_id.strip() or not self.account.strip():
            raise ValueError("exception identity must not be blank")
        if self.opened_at.tzinfo is None:
            raise ValueError("opened_at must be timezone-aware")
        if not self.currency.strip() or len(self.currency) != 3:
            raise ValueError("currency must be a three-character code")


@dataclass(frozen=True, slots=True)
class AgingPolicy:
    watch_after: timedelta = timedelta(days=2)
    overdue_after: timedelta = timedelta(days=5)
    critical_after: timedelta = timedelta(days=10)
    material_amount: Decimal = Decimal("10000")

    def __post_init__(self) -> None:
        if not timedelta(0) < self.watch_after < self.overdue_after < self.critical_after:
            raise ValueError("aging windows must be positive and increasing")
        if self.material_amount < 0:
            raise ValueError("material_amount must not be negative")


DEFAULT_AGING_POLICY = AgingPolicy()


@dataclass(frozen=True, slots=True)
class AgedException:
    exception: LedgerException
    bucket: AgingBucket
    age: timedelta
    priority_score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgingReport:
    generated_at: datetime
    exceptions: tuple[AgedException, ...]
    total_absolute_amount: Decimal
    unassigned_count: int
    overdue_count: int


def age_exception(
    exception: LedgerException,
    *,
    policy: AgingPolicy = DEFAULT_AGING_POLICY,
    now: datetime | None = None,
) -> AgedException:
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    age = max(timedelta(0), clock.astimezone(UTC) - exception.opened_at.astimezone(UTC))
    reasons: list[str] = []
    if age >= policy.critical_after:
        bucket = AgingBucket.CRITICAL
        score = 70
        reasons.append("age-critical")
    elif age >= policy.overdue_after:
        bucket = AgingBucket.OVERDUE
        score = 45
        reasons.append("age-overdue")
    elif age >= policy.watch_after:
        bucket = AgingBucket.WATCH
        score = 20
        reasons.append("age-watch")
    else:
        bucket = AgingBucket.CURRENT
        score = 0
    if exception.due_on is not None and exception.due_on < clock.date():
        score += 20
        reasons.append("due-date-passed")
    if abs(exception.amount) >= policy.material_amount:
        score += 20
        reasons.append("material-amount")
    if exception.owner is None:
        score += 10
        reasons.append("owner-missing")
    if exception.disputed:
        score += 5
        reasons.append("disputed")
    return AgedException(exception, bucket, age, score, tuple(reasons))


def build_aging_report(
    exceptions: Iterable[LedgerException],
    *,
    policy: AgingPolicy = DEFAULT_AGING_POLICY,
    now: datetime | None = None,
) -> AgingReport:
    clock = now or datetime.now(UTC)
    unique: dict[str, LedgerException] = {}
    for exception in exceptions:
        prior = unique.get(exception.exception_id)
        if prior is not None and prior != exception:
            raise ValueError(f"conflicting exception id: {exception.exception_id}")
        unique[exception.exception_id] = exception
    aged = [age_exception(item, policy=policy, now=clock) for item in unique.values()]
    aged.sort(key=lambda item: (-item.priority_score, item.exception.exception_id))
    return AgingReport(
        generated_at=clock,
        exceptions=tuple(aged),
        total_absolute_amount=sum((abs(item.exception.amount) for item in aged), Decimal("0")),
        unassigned_count=sum(item.exception.owner is None for item in aged),
        overdue_count=sum(
            item.bucket in {AgingBucket.OVERDUE, AgingBucket.CRITICAL} for item in aged
        ),
    )


def amount_by_currency(report: AgingReport) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for item in report.exceptions:
        currency = item.exception.currency.upper()
        totals[currency] = totals.get(currency, Decimal("0")) + item.exception.amount
    return dict(sorted(totals.items()))


def oldest_exception(report: AgingReport) -> AgedException | None:
    return max(report.exceptions, key=lambda item: item.age, default=None)


def bucket_counts(report: AgingReport) -> dict[str, int]:
    """Count exceptions in every aging bucket, including empty buckets."""

    counts = {bucket.value: 0 for bucket in AgingBucket}
    for item in report.exceptions:
        counts[item.bucket.value] += 1
    return counts
