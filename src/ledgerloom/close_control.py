"""Period-close controls for reconciled financial ledgers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum


class ControlState(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WAIVED = "waived"


@dataclass(frozen=True, slots=True)
class CloseControl:
    control_id: str
    description: str
    required_evidence: tuple[str, ...]
    owner_role: str
    blocking: bool = True

    def __post_init__(self) -> None:
        if not self.control_id.strip() or not self.description.strip():
            raise ValueError("control identity and description must not be blank")
        if not self.owner_role.strip():
            raise ValueError("owner_role must not be blank")
        if not self.required_evidence or any(not item.strip() for item in self.required_evidence):
            raise ValueError("required_evidence must contain non-blank values")
        if len(self.required_evidence) != len(set(self.required_evidence)):
            raise ValueError("required_evidence must be unique")


@dataclass(frozen=True, slots=True)
class ControlExecution:
    control_id: str
    executed_at: datetime
    executed_by: str
    state: ControlState
    evidence: Mapping[str, str]
    waiver_reason: str | None = None
    waiver_expires_on: date | None = None

    def __post_init__(self) -> None:
        if not self.control_id.strip() or not self.executed_by.strip():
            raise ValueError("execution identity must not be blank")
        if self.executed_at.tzinfo is None:
            raise ValueError("executed_at must be timezone-aware")
        if self.state is ControlState.WAIVED and not self.waiver_reason:
            raise ValueError("waived execution requires a reason")
        if self.state is not ControlState.WAIVED and (
            self.waiver_reason is not None or self.waiver_expires_on is not None
        ):
            raise ValueError("only waived executions may include waiver fields")


@dataclass(frozen=True, slots=True)
class ClosePolicy:
    maximum_execution_age: timedelta = timedelta(days=2)
    allow_active_waivers: bool = True

    def __post_init__(self) -> None:
        if self.maximum_execution_age <= timedelta(0):
            raise ValueError("maximum_execution_age must be positive")


DEFAULT_CLOSE_POLICY = ClosePolicy()


@dataclass(frozen=True, slots=True)
class ControlAssessment:
    control: CloseControl
    execution: ControlExecution | None
    satisfied: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CloseAssessment:
    period: str
    ready: bool
    controls: tuple[ControlAssessment, ...]
    blocking_failures: tuple[str, ...]
    generated_at: datetime


def assess_control(
    control: CloseControl,
    execution: ControlExecution | None,
    *,
    policy: ClosePolicy = DEFAULT_CLOSE_POLICY,
    now: datetime | None = None,
) -> ControlAssessment:
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if execution is None:
        return ControlAssessment(control, None, False, ("execution-missing",))
    if execution.control_id != control.control_id:
        raise ValueError("execution belongs to another control")
    reasons: list[str] = []
    if execution.executed_at.astimezone(UTC) > clock.astimezone(UTC):
        reasons.append("execution-in-future")
    if clock.astimezone(UTC) - execution.executed_at.astimezone(UTC) > policy.maximum_execution_age:
        reasons.append("execution-stale")
    missing_evidence = sorted(set(control.required_evidence) - set(execution.evidence))
    reasons.extend(f"evidence-missing:{name}" for name in missing_evidence)
    if any(not value.strip() for value in execution.evidence.values()):
        reasons.append("evidence-reference-blank")
    if execution.state is ControlState.FAIL:
        reasons.append("control-failed")
    if execution.state is ControlState.WAIVED:
        if not policy.allow_active_waivers:
            reasons.append("waivers-disabled")
        elif execution.waiver_expires_on is None:
            reasons.append("waiver-expiry-missing")
        elif execution.waiver_expires_on < clock.date():
            reasons.append("waiver-expired")
    return ControlAssessment(control, execution, not reasons, tuple(reasons))


def assess_close(
    period: str,
    controls: Iterable[CloseControl],
    executions: Iterable[ControlExecution],
    *,
    policy: ClosePolicy = DEFAULT_CLOSE_POLICY,
    now: datetime | None = None,
) -> CloseAssessment:
    """Evaluate the latest execution of each control for a period close."""

    if not period.strip():
        raise ValueError("period must not be blank")
    clock = now or datetime.now(UTC)
    control_by_id: dict[str, CloseControl] = {}
    for control in controls:
        if control.control_id in control_by_id:
            raise ValueError(f"duplicate control id: {control.control_id}")
        control_by_id[control.control_id] = control
    latest: dict[str, ControlExecution] = {}
    for execution in executions:
        if execution.control_id not in control_by_id:
            raise ValueError(f"unknown control execution: {execution.control_id}")
        prior = latest.get(execution.control_id)
        if prior is None or execution.executed_at > prior.executed_at:
            latest[execution.control_id] = execution
    assessments = tuple(
        assess_control(control_by_id[key], latest.get(key), policy=policy, now=clock)
        for key in sorted(control_by_id)
    )
    blocking = tuple(
        item.control.control_id
        for item in assessments
        if item.control.blocking and not item.satisfied
    )
    return CloseAssessment(period, not blocking, assessments, blocking, clock)


def close_evidence_index(assessment: CloseAssessment) -> dict[str, tuple[str, ...]]:
    """List evidence names recorded for every assessed control."""

    return {
        item.control.control_id: tuple(sorted(item.execution.evidence)) if item.execution else ()
        for item in assessment.controls
    }


def unsatisfied_control_reasons(assessment: CloseAssessment) -> dict[str, tuple[str, ...]]:
    """Expose failed control evidence without losing deterministic order."""

    return {
        item.control.control_id: item.reasons for item in assessment.controls if not item.satisfied
    }
