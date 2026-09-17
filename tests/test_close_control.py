from datetime import UTC, date, datetime, timedelta

import pytest

from ledgerloom.close_control import CloseControl, ControlExecution, ControlState, assess_close


@pytest.mark.parametrize("state", [ControlState.PASS, ControlState.WAIVED])
def test_future_execution_cannot_support_a_period_close(state) -> None:
    now = datetime(2026, 9, 17, tzinfo=UTC)
    control = CloseControl("bank", "Bank proof", ("statement",), "accountant")
    execution = ControlExecution(
        "bank",
        now + timedelta(microseconds=1),
        "alex",
        state,
        {"statement": "S1"},
        "pending" if state is ControlState.WAIVED else None,
        date(2026, 9, 18) if state is ControlState.WAIVED else None,
    )
    result = assess_close("2026-09", [control], [execution], now=now)
    assert not result.ready
    assert result.blocking_failures == ("bank",)
    assert "execution-in-future" in result.controls[0].reasons


def test_latest_future_execution_is_reported_instead_of_using_older_success() -> None:
    now = datetime(2026, 9, 17, tzinfo=UTC)
    control = CloseControl("bank", "Bank proof", ("statement",), "accountant")
    older = ControlExecution("bank", now, "alex", ControlState.PASS, {"statement": "S1"})
    future = ControlExecution(
        "bank", now + timedelta(days=1), "alex", ControlState.PASS, {"statement": "S2"}
    )
    result = assess_close("2026-09", [control], [older, future], now=now)
    assert not result.ready
    assert result.controls[0].execution == future


def test_close_accepts_fresh_control_with_complete_evidence() -> None:
    now = datetime(2026, 9, 6, tzinfo=UTC)
    control = CloseControl("bank", "Bank proof", ("statement", "tie-out"), "accountant")
    execution = ControlExecution(
        "bank", now, "alex", ControlState.PASS, {"statement": "S1", "tie-out": "T1"}
    )
    result = assess_close("2026-08", [control], [execution], now=now)
    assert result.ready
    assert result.blocking_failures == ()


def test_close_rejects_expired_waiver() -> None:
    now = datetime(2026, 9, 6, tzinfo=UTC)
    control = CloseControl("bank", "Bank proof", ("statement",), "accountant")
    execution = ControlExecution(
        "bank", now, "alex", ControlState.WAIVED, {"statement": "S1"}, "pending", date(2026, 9, 5)
    )
    assert not assess_close("2026-08", [control], [execution], now=now).ready
