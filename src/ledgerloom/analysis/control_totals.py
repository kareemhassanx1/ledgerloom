"""Compare an import against independently supplied statement control totals."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from ledgerloom.analysis.balances import amount_text, amount_units, source_balances
from ledgerloom.application.errors import ApplicationError
from ledgerloom.domain import AccountId, Currency, LedgerEntry, SourceId


@dataclass(frozen=True, slots=True)
class StatementControl:
    source: SourceId
    account: AccountId
    currency: Currency
    count: int
    inflow: Decimal
    outflow: Decimal

    def __post_init__(self) -> None:
        if not (
            isinstance(self.source, SourceId)
            and isinstance(self.account, AccountId)
            and isinstance(self.currency, Currency)
        ):
            raise ApplicationError("control.identity", "$.controls", "invalid control identity")
        if type(self.count) is not int or self.count < 0:
            raise ApplicationError(
                "control.count", "$.count", "count must be a nonnegative integer"
            )
        if amount_units(self.inflow) < 0 or amount_units(self.outflow) < 0:
            raise ApplicationError(
                "control.amount", "$.controls", "gross totals cannot be negative"
            )


def check_statement_controls(
    entries: Iterable[LedgerEntry], controls: Iterable[StatementControl]
) -> dict[str, object]:
    """Missing/unexpected groups fail as well as count and gross-total differences.

    Comparing inflow and outflow independently detects offsetting omissions that
    a net-only comparison cannot. Empty expected groups are supported explicitly.
    """
    actual = {
        (str(r["source"]), str(r["account"]), str(r["currency"])): r
        for r in source_balances(entries)
    }
    expected = {}
    for control in controls:
        if not isinstance(control, StatementControl):
            raise ApplicationError("control.type", "$.controls", "expected StatementControl values")
        key = str(control.source), str(control.account), str(control.currency)
        if key in expected:
            raise ApplicationError("control.duplicate", "$.controls", "duplicate statement group")
        expected[key] = control
    rows = []
    for key in sorted(actual.keys() | expected.keys()):
        observed = actual.get(key)
        wanted = expected.get(key)
        issues = []
        if wanted is None:
            issues.append("unexpected_group")
        else:
            values = observed or {"count": 0, "inflow": "0", "outflow": "0"}
            if values["count"] != wanted.count:
                issues.append("count_mismatch")
            if values["inflow"] != amount_text(amount_units(wanted.inflow)):
                issues.append("inflow_mismatch")
            if values["outflow"] != amount_text(amount_units(wanted.outflow)):
                issues.append("outflow_mismatch")
        rows.append(
            {
                "source": key[0],
                "account": key[1],
                "currency": key[2],
                "issues": issues,
                "actual": observed,
            }
        )
    return {"passed": all(not row["issues"] for row in rows), "groups": rows}
