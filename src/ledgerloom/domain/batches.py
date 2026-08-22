"""Immutable ordered batches of ledger validation outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from ledgerloom.domain.diagnostics import Diagnostic, ValidationResult
from ledgerloom.domain.errors import DomainValidationError
from ledgerloom.domain.identifiers import BatchId, EntryId, SourceId, SourceRecordId
from ledgerloom.domain.records import LedgerEntry


@dataclass(frozen=True, slots=True)
class LocatedDiagnostic:
    """A diagnostic paired with its one-based position in a batch."""

    position: int
    diagnostic: Diagnostic

    def __post_init__(self) -> None:
        if isinstance(self.position, bool) or not isinstance(self.position, int):
            raise DomainValidationError(
                code="located_diagnostic.position_type",
                field="position",
                message="must be an integer",
                value=self.position,
            )
        if self.position < 1:
            raise DomainValidationError(
                code="located_diagnostic.position_range",
                field="position",
                message="must be at least one",
                value=self.position,
            )
        if not isinstance(self.diagnostic, Diagnostic):
            raise DomainValidationError(
                code="located_diagnostic.diagnostic_type",
                field="diagnostic",
                message="must be a Diagnostic",
                value=self.diagnostic,
            )


@dataclass(frozen=True, slots=True)
class LedgerBatch:
    """An ordered, source-scoped batch preserving every item outcome."""

    batch_id: BatchId
    source_id: SourceId
    results: tuple[ValidationResult[LedgerEntry], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.batch_id, BatchId):
            raise DomainValidationError(
                code="ledger_batch.batch_id_type",
                field="batch_id",
                message="must be a BatchId",
                value=self.batch_id,
            )
        if not isinstance(self.source_id, SourceId):
            raise DomainValidationError(
                code="ledger_batch.source_id_type",
                field="source_id",
                message="must be a SourceId",
                value=self.source_id,
            )
        if not isinstance(self.results, tuple):
            raise DomainValidationError(
                code="ledger_batch.results_type",
                field="results",
                message="must be a tuple",
                value=self.results,
            )
        if not self.results:
            raise DomainValidationError(
                code="ledger_batch.empty",
                field="results",
                message="must contain at least one result",
                value=self.results,
            )
        if not all(isinstance(result, ValidationResult) for result in self.results):
            raise DomainValidationError(
                code="ledger_batch.result_type",
                field="results",
                message="must contain only ValidationResult values",
                value=self.results,
            )

        entry_ids: set[EntryId] = set()
        source_keys: set[tuple[SourceId, SourceRecordId]] = set()
        for result in self.results:
            entry = result.value
            if entry is None:
                continue
            if not isinstance(entry, LedgerEntry):
                raise DomainValidationError(
                    code="ledger_batch.value_type",
                    field="results",
                    message="successful results must contain LedgerEntry values",
                    value=entry,
                )
            if entry.source_id != self.source_id:
                raise DomainValidationError(
                    code="ledger_batch.source_mismatch",
                    field="results",
                    message="entry source must match the batch source",
                    value=entry.source_id,
                )
            if entry.entry_id in entry_ids:
                raise DomainValidationError(
                    code="ledger_batch.duplicate_entry",
                    field="results",
                    message="entry identifiers must be unique within a batch",
                    value=entry.entry_id,
                )
            if entry.source_key in source_keys:
                raise DomainValidationError(
                    code="ledger_batch.duplicate_source_record",
                    field="results",
                    message="source records must occur at most once within a batch",
                    value=entry.source_key,
                )
            entry_ids.add(entry.entry_id)
            source_keys.add(entry.source_key)

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        """Return accepted entries in their original input order."""

        return tuple(result.value for result in self.results if result.value is not None)

    @property
    def diagnostics(self) -> tuple[LocatedDiagnostic, ...]:
        """Return all diagnostics with stable one-based input positions."""

        return tuple(
            LocatedDiagnostic(position, diagnostic)
            for position, result in enumerate(self.results, start=1)
            for diagnostic in result.diagnostics
        )

    @property
    def accepted_count(self) -> int:
        return len(self.entries)

    @property
    def rejected_count(self) -> int:
        return sum(not result.is_valid for result in self.results)

    @property
    def is_complete(self) -> bool:
        """Whether every submitted item produced an entry."""

        return self.rejected_count == 0
