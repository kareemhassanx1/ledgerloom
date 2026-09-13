"""Consistent read models for one durable journal transaction."""

from __future__ import annotations

from dataclasses import dataclass

from ledgerloom.journal import JournalEntry, JournalIntegrityReport
from ledgerloom.storage.errors import StorageError


@dataclass(frozen=True, slots=True)
class StoredJournalSnapshot:
    """Pair an integrity result with the entries in its verified prefix."""

    verified_entries: tuple[JournalEntry, ...]
    integrity: JournalIntegrityReport

    def __post_init__(self) -> None:
        if not isinstance(self.verified_entries, tuple) or not all(
            isinstance(item, JournalEntry) for item in self.verified_entries
        ):
            raise StorageError(
                code="storage.snapshot_entries_type",
                path="$.verified_entries",
                message="verified_entries must be a tuple of JournalEntry values",
            )
        if not isinstance(self.integrity, JournalIntegrityReport):
            raise StorageError(
                code="storage.snapshot_integrity_type",
                path="$.integrity",
                message="integrity must be a JournalIntegrityReport",
            )
        if len(self.verified_entries) != self.integrity.valid_entries:
            raise StorageError(
                code="storage.snapshot_prefix_mismatch",
                path="$.verified_entries",
                message="verified entry count must match the integrity report",
            )

    def require_valid(self) -> tuple[JournalEntry, ...]:
        """Return all entries or raise the first integrity issue."""

        self.integrity.require_valid()
        return self.verified_entries


__all__ = ["StoredJournalSnapshot"]
