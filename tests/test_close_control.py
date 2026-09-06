from datetime import UTC, date, datetime

from ledgerloom.close_control import CloseControl, ControlExecution, ControlState, assess_close


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
