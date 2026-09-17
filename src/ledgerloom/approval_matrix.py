"""Independent approval policy for reconciliation adjustments."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ApprovalRule:
    rule_id: str
    currency: str
    minimum_amount: Decimal
    required_roles: frozenset[str]
    minimum_approvals: int
    require_independent_requester: bool = True

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ValueError("rule_id must not be blank")
        if len(self.currency) != 3:
            raise ValueError("currency must be a three-character code")
        if self.minimum_amount < 0:
            raise ValueError("minimum_amount must not be negative")
        if not self.required_roles or any(not role.strip() for role in self.required_roles):
            raise ValueError("required_roles must not be empty")
        if not 1 <= self.minimum_approvals <= len(self.required_roles):
            raise ValueError("minimum_approvals is outside the role set")


@dataclass(frozen=True, slots=True)
class AdjustmentRequest:
    request_id: str
    account: str
    amount: Decimal
    currency: str
    requested_by: str
    requested_at: datetime
    reason: str

    def __post_init__(self) -> None:
        for name in ("request_id", "account", "requested_by", "reason"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if len(self.currency) != 3:
            raise ValueError("currency must be a three-character code")
        if self.requested_at.tzinfo is None:
            raise ValueError("requested_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Approval:
    approver: str
    role: str
    approved_at: datetime
    evidence_reference: str

    def __post_init__(self) -> None:
        for name in ("approver", "role", "evidence_reference"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if self.approved_at.tzinfo is None:
            raise ValueError("approved_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ApprovalAssessment:
    request: AdjustmentRequest
    rule: ApprovalRule | None
    approved: bool
    accepted_approvals: tuple[Approval, ...]
    rejected_approvals: tuple[Approval, ...]
    reasons: tuple[str, ...]


def select_rule(request: AdjustmentRequest, rules: Iterable[ApprovalRule]) -> ApprovalRule | None:
    matching = [
        rule
        for rule in rules
        if rule.currency.upper() == request.currency.upper()
        and abs(request.amount) >= rule.minimum_amount
    ]
    if not matching:
        return None
    return max(matching, key=lambda rule: (rule.minimum_amount, rule.rule_id))


def assess_approvals(
    request: AdjustmentRequest,
    approvals: Iterable[Approval],
    rules: Iterable[ApprovalRule],
    *,
    user_roles: Mapping[str, frozenset[str]] | None = None,
) -> ApprovalAssessment:
    """Check role coverage, independence, identity, and approval timing.

    A supplied roster is authoritative, including an empty roster. Omitting it
    preserves the API for callers that have already validated role membership.
    """

    rule = select_rule(request, rules)
    if rule is None:
        return ApprovalAssessment(request, None, False, (), (), ("approval-rule-missing",))
    accepted: list[Approval] = []
    rejected: list[Approval] = []
    reasons: list[str] = []
    seen_approvers: set[str] = set()
    roles = user_roles if user_roles is not None else {}
    for approval in sorted(approvals, key=lambda item: (item.approved_at, item.approver)):
        valid = True
        invalid_identity = approval.approver in seen_approvers
        invalid_timing = approval.approved_at < request.requested_at
        invalid_role = approval.role not in rule.required_roles
        unauthorized_role = bool(
            user_roles is not None
            and approval.role not in roles.get(approval.approver, frozenset())
        )
        requester_conflict = (
            rule.require_independent_requester and approval.approver == request.requested_by
        )
        if any(
            (
                invalid_identity,
                invalid_timing,
                invalid_role,
                unauthorized_role,
                requester_conflict,
            )
        ):
            valid = False
        if valid:
            accepted.append(approval)
            seen_approvers.add(approval.approver)
        else:
            rejected.append(approval)
    covered_roles = {approval.role for approval in accepted}
    missing_roles = sorted(rule.required_roles - covered_roles)
    if len(accepted) < rule.minimum_approvals:
        reasons.append("approval-count-insufficient")
    if missing_roles:
        reasons.extend(f"role-missing:{role}" for role in missing_roles)
    if rejected:
        reasons.append("one-or-more-approvals-rejected")
    return ApprovalAssessment(
        request,
        rule,
        len(accepted) >= rule.minimum_approvals and not missing_roles,
        tuple(accepted),
        tuple(rejected),
        tuple(reasons),
    )


def approval_evidence(assessment: ApprovalAssessment) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "approver": approval.approver,
            "role": approval.role,
            "approved_at": approval.approved_at.astimezone(UTC).isoformat(),
            "evidence_reference": approval.evidence_reference,
        }
        for approval in assessment.accepted_approvals
    )


def missing_approval_roles(assessment: ApprovalAssessment) -> tuple[str, ...]:
    """Return required roles not represented by accepted approvals."""

    if assessment.rule is None:
        return ()
    present = {approval.role for approval in assessment.accepted_approvals}
    return tuple(sorted(assessment.rule.required_roles - present))
