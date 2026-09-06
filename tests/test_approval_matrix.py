from datetime import UTC, datetime
from decimal import Decimal

from ledgerloom.approval_matrix import AdjustmentRequest, Approval, ApprovalRule, assess_approvals


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
