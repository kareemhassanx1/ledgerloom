"""Typed discovery of application objects stored in a verified journal."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ledgerloom.application.errors import CatalogError
from ledgerloom.domain import BatchId, EntryId, LedgerBatch
from ledgerloom.journal import JournalEntry, verify_journal_entries
from ledgerloom.reconciliation import (
    RECONCILIATION_REPORT_V1,
    ReconciliationClaimError,
    ReconciliationClaims,
    ReconciliationReport,
    parse_reconciliation_report_document,
)
from ledgerloom.serialization import LEDGER_BATCH_V1, domain_v1_registry
from ledgerloom.serialization.schema import SchemaName, SchemaRef

_SUPPORTED_SCHEMAS = {
    LEDGER_BATCH_V1.name: LEDGER_BATCH_V1,
    RECONCILIATION_REPORT_V1.name: RECONCILIATION_REPORT_V1,
}


@dataclass(frozen=True, slots=True)
class JournalCatalog:
    """A typed, immutable view of objects discovered in journal order."""

    batches: tuple[LedgerBatch, ...]
    reports: tuple[ReconciliationReport, ...]
    unrecognized_entries: tuple[JournalEntry, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.batches, tuple) or not all(
            isinstance(item, LedgerBatch) for item in self.batches
        ):
            raise CatalogError(
                "catalog.batches_type",
                "$.batches",
                "batches must be a tuple of LedgerBatch values",
            )
        if not isinstance(self.reports, tuple) or not all(
            isinstance(item, ReconciliationReport) for item in self.reports
        ):
            raise CatalogError(
                "catalog.reports_type",
                "$.reports",
                "reports must be a tuple of ReconciliationReport values",
            )
        if not isinstance(self.unrecognized_entries, tuple) or not all(
            isinstance(item, JournalEntry) for item in self.unrecognized_entries
        ):
            raise CatalogError(
                "catalog.unrecognized_type",
                "$.unrecognized_entries",
                "unrecognized entries must be a tuple of JournalEntry values",
            )
        identifiers = tuple(item.batch_id for item in self.batches) + tuple(
            item.batch_id for item in self.reports
        )
        if len(identifiers) != len(set(identifiers)):
            raise CatalogError(
                "catalog.batch_duplicate",
                "$.entries",
                "journal batch identifiers must be unique across stored objects",
            )
        try:
            ReconciliationClaims.from_reports(self.reports)
        except ReconciliationClaimError as error:
            raise CatalogError("catalog.claim_history", error.path, error.message) from error

    @classmethod
    def from_entries(cls, entries: Iterable[JournalEntry]) -> JournalCatalog:
        """Verify and decode every supported application document in order."""

        if isinstance(entries, str | bytes | bytearray) or not isinstance(entries, Iterable):
            raise CatalogError(
                "catalog.entries_type",
                "$.entries",
                "entries must be an iterable of JournalEntry values",
            )
        materialized = tuple(entries)
        verify_journal_entries(materialized)
        registry = domain_v1_registry()
        batches: list[LedgerBatch] = []
        reports: list[ReconciliationReport] = []
        unrecognized: list[JournalEntry] = []
        imported_entry_ids: set[EntryId] = set()
        for index, entry in enumerate(materialized):
            cls._require_supported_version(entry.document.schema, index=index)
            if entry.document.schema == LEDGER_BATCH_V1:
                value = registry.decode(entry.document)
                assert isinstance(value, LedgerBatch)
                batches.append(value)
                imported_entry_ids.update(item.entry_id for item in value.entries)
            elif entry.document.schema == RECONCILIATION_REPORT_V1:
                report = parse_reconciliation_report_document(
                    entry.document.content,
                    expected_fingerprint=entry.document.fingerprint,
                )
                cls._require_imported_entries(report, imported_entry_ids, index=index)
                reports.append(report)
            else:
                unrecognized.append(entry)
        return cls(tuple(batches), tuple(reports), tuple(unrecognized))

    @staticmethod
    def _require_imported_entries(
        report: ReconciliationReport,
        imported: set[EntryId],
        *,
        index: int,
    ) -> None:
        """A report may refer only to entries imported before that journal event."""
        for decision_index, decision in enumerate(report.decisions):
            references = [("anchor_id", decision.anchor_id)]
            references.extend(
                (f"candidates[{candidate_index}].candidate_id", candidate.candidate_id)
                for candidate_index, candidate in enumerate(decision.candidates)
            )
            references.extend(
                (f"competing_anchor_ids[{peer_index}]", entry_id)
                for peer_index, entry_id in enumerate(decision.competing_anchor_ids)
            )
            for field, entry_id in references:
                if entry_id not in imported:
                    raise CatalogError(
                        "catalog.report_entry_missing",
                        f"$.entries[{index}].document.payload.decisions[{decision_index}].{field}",
                        f"entry {entry_id} is not present in preceding imported batches",
                    )

    @staticmethod
    def _require_supported_version(schema: SchemaRef, *, index: int) -> None:
        supported = _SUPPORTED_SCHEMAS.get(schema.name)
        if supported is not None and schema != supported:
            raise CatalogError(
                "catalog.schema_version_unsupported",
                f"$.entries[{index}].document.schema.version",
                f"schema {schema.name} version {schema.version} is not supported",
            )

    @property
    def batch_ids(self) -> frozenset[BatchId]:
        return frozenset(item.batch_id for item in self.batches) | frozenset(
            item.batch_id for item in self.reports
        )

    def contains_batch(self, batch_id: BatchId) -> bool:
        if not isinstance(batch_id, BatchId):
            raise CatalogError(
                "catalog.batch_id_type",
                "$.batch_id",
                "batch_id must be a BatchId",
            )
        return batch_id in self.batch_ids

    @property
    def unrecognized_schema_names(self) -> tuple[SchemaName, ...]:
        return tuple(
            dict.fromkeys(entry.document.schema.name for entry in self.unrecognized_entries)
        )
