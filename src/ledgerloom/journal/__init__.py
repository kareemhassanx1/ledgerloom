"""Append-only journals with sequenced, verifiable event chains."""

from ledgerloom.journal.entries import (
    MAX_JOURNAL_SEQUENCE,
    JournalEntry,
    verify_journal_entries,
)
from ledgerloom.journal.errors import (
    JournalConflictError,
    JournalError,
    JournalIntegrityError,
    JournalReplayError,
    ProjectionError,
    ProjectionSnapshotError,
)
from ledgerloom.journal.integrity import (
    JournalIntegrityIssue,
    JournalIntegrityReport,
    inspect_journal_entries,
)
from ledgerloom.journal.memory import (
    DEFAULT_MAX_READ_LIMIT,
    MAX_CONFIGURED_READ_LIMIT,
    InMemoryJournal,
)
from ledgerloom.journal.projections import (
    PROJECTION_SNAPSHOT_SCHEMA,
    Projection,
    ProjectionCheckpoint,
    ProjectionRunner,
    ProjectionSnapshot,
)
from ledgerloom.journal.replay import (
    DecodedJournalEvent,
    JournalReplayer,
    ReplayCursor,
    ReplayResult,
    replay_entries,
)

__all__ = [
    "DEFAULT_MAX_READ_LIMIT",
    "MAX_CONFIGURED_READ_LIMIT",
    "MAX_JOURNAL_SEQUENCE",
    "PROJECTION_SNAPSHOT_SCHEMA",
    "DecodedJournalEvent",
    "InMemoryJournal",
    "JournalConflictError",
    "JournalEntry",
    "JournalError",
    "JournalIntegrityError",
    "JournalIntegrityIssue",
    "JournalIntegrityReport",
    "JournalReplayError",
    "JournalReplayer",
    "Projection",
    "ProjectionCheckpoint",
    "ProjectionError",
    "ProjectionRunner",
    "ProjectionSnapshot",
    "ProjectionSnapshotError",
    "ReplayCursor",
    "ReplayResult",
    "inspect_journal_entries",
    "replay_entries",
    "verify_journal_entries",
]
