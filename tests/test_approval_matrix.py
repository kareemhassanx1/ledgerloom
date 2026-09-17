from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ledgerloom.approval_matrix import AdjustmentRequest, Approval, ApprovalRule, assess_approvals


@pytest.mark.parametrize("roster", [{}, {"someone-else": frozenset({"controller"})}])
def test_explicit_roster_requires_the_actual_approver_role(roster) -> None:
    now = datetime(2026, 9, 17, tzinfo=UTC)
    rule = ApprovalRule("usd", "USD", Decimal("0"), frozenset({"controller"}), 1)
    request = AdjustmentRequest("A-2", "1000", Decimal("10"), "USD", "requester", now, "close")
    approval = Approval("reviewer", "controller", now, "E2")
    result = assess_approvals(request, [approval], [rule], user_roles=roster)
    assert not result.approved
    assert result.accepted_approvals == ()
    assert result.rejected_approvals == (approval,)
    assert "role-missing:controller" in result.reasons


def test_omitted_roster_preserves_caller_validated_role_contract() -> None:
    now = datetime(2026, 9, 17, tzinfo=UTC)
    rule = ApprovalRule("usd", "USD", Decimal("0"), frozenset({"controller"}), 1)
    request = AdjustmentRequest("A-2", "1000", Decimal("10"), "USD", "requester", now, "close")
    approval = Approval("reviewer", "controller", now, "E2")
    assert assess_approvals(request, [approval], [rule]).approved


def test_approval_matrix_requires_distinct_authorized_roles() -> None:
    now = datetime(2026, 9, 6, tzinfo=UTC)
    rule = ApprovalRule(
        "large-usd", "USD", Decimal("1000"), frozenset({"controller", "treasury"}), 2
    )
    request = AdjustmentRequest("A-1", "1000", Decimal("5000"), "USD", "requester", now, "close")
    approvals = [
        Approval("c", "controller", now, "E1"),
        Approval("t", "treasury", now, "E2"),
    ]
    result = assess_approvals(
        request,
        approvals,
        [rule],
        user_roles={"c": frozenset({"controller"}), "t": frozenset({"treasury"})},
    )
    assert result.approved
    assert len(result.accepted_approvals) == 2
