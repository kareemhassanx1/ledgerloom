"""Compare full reconciliation evidence, including policy-only revisions."""

from ledgerloom.application.errors import ApplicationError
from ledgerloom.reconciliation import ReconciliationReport, build_reconciliation_report_document


def report_changes(before: ReconciliationReport, after: ReconciliationReport) -> dict[str, object]:
    """Describe revisions without treating the comparison as a new committed decision."""
    if not isinstance(before, ReconciliationReport) or not isinstance(after, ReconciliationReport):
        raise ApplicationError("changes.report", "$.reports", "expected reconciliation reports")
    old = {decision.anchor_id: decision for decision in before.decisions}
    new = {decision.anchor_id: decision for decision in after.decisions}
    rows = []
    for identifier in sorted(old.keys() | new.keys(), key=str):
        left, right = old.get(identifier), new.get(identifier)
        if left == right:
            continue
        rows.append(
            {
                "anchor": str(identifier),
                "change": "added" if left is None else "removed" if right is None else "changed",
                "before_status": None if left is None else str(left.status),
                "after_status": None if right is None else str(right.status),
                "before_candidate": (
                    None
                    if left is None or left.selected_candidate_id is None
                    else str(left.selected_candidate_id)
                ),
                "after_candidate": (
                    None
                    if right is None or right.selected_candidate_id is None
                    else str(right.selected_candidate_id)
                ),
                "before_reason": None if left is None else left.reason_code,
                "after_reason": None if right is None else right.reason_code,
            }
        )
    return {
        "before_report": str(before.batch_id),
        "after_report": str(after.batch_id),
        "before_fingerprint": str(build_reconciliation_report_document(before).fingerprint),
        "after_fingerprint": str(build_reconciliation_report_document(after).fingerprint),
        "policy_changed": before.policy_snapshot != after.policy_snapshot,
        "changes": rows,
    }
