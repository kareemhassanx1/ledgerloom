"""Durable one-to-one claims derived from committed reconciliation reports."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from ledgerloom.domain import EntryId, LedgerEntry
from ledgerloom.reconciliation.decisions import DecisionStatus
from ledgerloom.reconciliation.errors import ReconciliationClaimError
from ledgerloom.reconciliation.reports import (
    DecisionReport,
    ReconciliationReport,
    ReconciliationSummary,
)


@dataclass(frozen=True, slots=True)
class EntryClaim:
    """Record the earlier anchors that committed a ledger entry to a match."""

    entry_id: EntryId
    anchor_ids: tuple[EntryId, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.entry_id, EntryId):
            raise ReconciliationClaimError(
                code="claim.entry_id_type",
                path="$.claim.entry_id",
                message="entry_id must be an EntryId",
            )
        if (
            not isinstance(self.anchor_ids, tuple)
            or not self.anchor_ids
            or not all(isinstance(item, EntryId) for item in self.anchor_ids)
        ):
            raise ReconciliationClaimError(
                code="claim.anchor_ids_type",
                path="$.claim.anchor_ids",
                message="anchor_ids must be a non-empty tuple of EntryId values",
            )
        if len(self.anchor_ids) != len(set(self.anchor_ids)):
            raise ReconciliationClaimError(
                code="claim.anchor_duplicate",
                path="$.claim.anchor_ids",
                message="anchor_ids must not contain duplicates",
            )


@dataclass(frozen=True, slots=True)
class ReconciliationClaims:
    """Index committed matches and reject later reuse of either participant."""

    entries: tuple[EntryClaim, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple) or not all(
            isinstance(item, EntryClaim) for item in self.entries
        ):
            raise ReconciliationClaimError(
                code="claim.entries_type",
                path="$.claims",
                message="claims must be a tuple of EntryClaim values",
            )
        entry_ids = tuple(item.entry_id for item in self.entries)
        if len(entry_ids) != len(set(entry_ids)):
            raise ReconciliationClaimError(
                code="claim.entry_duplicate",
                path="$.claims",
                message="each claimed entry must occur once",
            )

    @classmethod
    def from_reports(
        cls,
        reports: Iterable[ReconciliationReport],
    ) -> ReconciliationClaims:
        if isinstance(reports, str | bytes) or not isinstance(reports, Iterable):
            raise ReconciliationClaimError(
                code="claim.reports_type",
                path="$.reports",
                message="reports must be an iterable of ReconciliationReport values",
            )
        owners: dict[EntryId, set[EntryId]] = {}
        for index, report in enumerate(reports):
            if not isinstance(report, ReconciliationReport):
                raise ReconciliationClaimError(
                    code="claim.report_type",
                    path=f"$.reports[{index}]",
                    message="reports must contain only ReconciliationReport values",
                )
            for decision_index, decision in enumerate(report.decisions):
                if decision.status is not DecisionStatus.MATCHED:
                    continue
                candidate_id = decision.selected_candidate_id
                assert candidate_id is not None
                for field, entry_id in (
                    ("anchor_id", decision.anchor_id),
                    ("selected_candidate_id", candidate_id),
                ):
                    if entry_id in owners:
                        raise ReconciliationClaimError(
                            code="claim.history_entry_reused",
                            path=f"$.reports[{index}].decisions[{decision_index}].{field}",
                            message=(
                                f"entry {entry_id} participates in more than one committed match"
                            ),
                        )
                    owners[entry_id] = {decision.anchor_id}
        return cls(
            tuple(
                EntryClaim(
                    entry_id,
                    tuple(sorted(anchor_ids, key=str)),
                )
                for entry_id, anchor_ids in sorted(owners.items(), key=lambda item: str(item[0]))
            )
        )

    @property
    def claimed_entry_ids(self) -> frozenset[EntryId]:
        return frozenset(item.entry_id for item in self.entries)

    def owners_for(self, entry_id: EntryId) -> tuple[EntryId, ...]:
        if not isinstance(entry_id, EntryId):
            raise ReconciliationClaimError(
                code="claim.entry_id_type",
                path="$.entry_id",
                message="entry_id must be an EntryId",
            )
        for claim in self.entries:
            if claim.entry_id == entry_id:
                return claim.anchor_ids
        return ()

    def require_available_anchors(self, anchors: Iterable[LedgerEntry]) -> None:
        if isinstance(anchors, str | bytes) or not isinstance(anchors, Iterable):
            raise ReconciliationClaimError(
                code="claim.anchors_type",
                path="$.anchors",
                message="anchors must be an iterable of LedgerEntry values",
            )
        for index, anchor in enumerate(anchors):
            if not isinstance(anchor, LedgerEntry):
                raise ReconciliationClaimError(
                    code="claim.anchor_type",
                    path=f"$.anchors[{index}]",
                    message="anchors must contain only LedgerEntry values",
                )
            if self.owners_for(anchor.entry_id):
                raise ReconciliationClaimError(
                    code="claim.anchor_already_matched",
                    path=f"$.anchors[{index}].entry_id",
                    message=f"anchor {anchor.entry_id} already participates in a committed match",
                )

    def resolve(self, report: ReconciliationReport) -> ReconciliationReport:
        """Turn selections of previously claimed entries into explicit conflicts."""

        if not isinstance(report, ReconciliationReport):
            raise ReconciliationClaimError(
                code="claim.report_type",
                path="$.report",
                message="report must be a ReconciliationReport",
            )
        resolved = tuple(self._resolve_decision(item) for item in report.decisions)
        if resolved == report.decisions:
            return report
        matched = sum(item.status is DecisionStatus.MATCHED for item in resolved)
        summary = ReconciliationSummary(
            total=len(resolved),
            matched=matched,
            unmatched=sum(item.status is DecisionStatus.UNMATCHED for item in resolved),
            ambiguous=sum(item.status is DecisionStatus.AMBIGUOUS for item in resolved),
            conflict=sum(item.status is DecisionStatus.CONFLICT for item in resolved),
            evaluated_candidates=sum(len(item.candidates) for item in resolved),
            coverage_basis_points=(matched * 10_000) // len(resolved),
        )
        return ReconciliationReport(
            report.batch_id,
            summary,
            resolved,
            report.policy_snapshot,
        )

    def _resolve_decision(self, decision: DecisionReport) -> DecisionReport:
        candidate_id = decision.selected_candidate_id
        if candidate_id is None:
            return decision
        owners = tuple(
            owner for owner in self.owners_for(candidate_id) if owner != decision.anchor_id
        )
        if not owners:
            return decision
        competitors = tuple(dict.fromkeys((*decision.competing_anchor_ids, *owners)))
        return replace(
            decision,
            status=DecisionStatus.CONFLICT,
            reason_code="conflict.candidate_already_claimed",
            selected_candidate_id=None,
            contender_ids=(candidate_id,),
            competing_anchor_ids=competitors,
        )


__all__ = ["EntryClaim", "ReconciliationClaims"]
