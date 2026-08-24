"""Bounded, append-only in-memory journal storage."""

from __future__ import annotations

from threading import RLock

from ledgerloom.journal.entries import MAX_JOURNAL_SEQUENCE, JournalEntry, verify_journal_entries
from ledgerloom.journal.errors import JournalConflictError, JournalError
from ledgerloom.journal.integrity import JournalIntegrityReport, inspect_journal_entries
from ledgerloom.serialization.fingerprints import ContentFingerprint
from ledgerloom.serialization.schema import SchemaDocument

DEFAULT_MAX_READ_LIMIT = 1_000
MAX_CONFIGURED_READ_LIMIT = 100_000


def _integer(value: object, *, code: str, path: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise JournalError(code=code, path=path, message=f"{label} must be an integer")
    return value


class InMemoryJournal:
    """Store a verified event chain in process memory.

    Reads return immutable snapshots. A caller that coordinates concurrent
    writers can pass ``expected_sequence`` to reject an append based on a stale
    view of the journal head.
    """

    def __init__(self, *, max_read_limit: int = DEFAULT_MAX_READ_LIMIT) -> None:
        resolved_limit = _integer(
            max_read_limit,
            code="journal.max_read_limit_type",
            path="$.max_read_limit",
            label="max read limit",
        )
        if not 1 <= resolved_limit <= MAX_CONFIGURED_READ_LIMIT:
            raise JournalError(
                code="journal.max_read_limit_range",
                path="$.max_read_limit",
                message=(
                    f"max read limit must be between 1 and {MAX_CONFIGURED_READ_LIMIT}"
                ),
            )
        self._max_read_limit = resolved_limit
        self._entries: list[JournalEntry] = []
        self._lock = RLock()

    @property
    def max_read_limit(self) -> int:
        return self._max_read_limit

    @property
    def next_sequence(self) -> int:
        with self._lock:
            self._verify_locked()
            return len(self._entries) + 1

    @property
    def head_fingerprint(self) -> ContentFingerprint | None:
        with self._lock:
            self._verify_locked()
            return self._entries[-1].fingerprint if self._entries else None

    def append(
        self,
        document: SchemaDocument,
        *,
        expected_sequence: int | None = None,
    ) -> JournalEntry:
        """Append one document atomically and return its immutable journal entry."""

        if not isinstance(document, SchemaDocument):
            raise JournalError(
                code="journal.document_type",
                path="$.document",
                message="document must be a SchemaDocument",
            )
        if expected_sequence is not None:
            resolved_expected = _integer(
                expected_sequence,
                code="journal.expected_sequence_type",
                path="$.expected_sequence",
                label="expected sequence",
            )
            if not 1 <= resolved_expected <= MAX_JOURNAL_SEQUENCE:
                raise JournalError(
                    code="journal.expected_sequence_range",
                    path="$.expected_sequence",
                    message=f"expected sequence must be between 1 and {MAX_JOURNAL_SEQUENCE}",
                )
        else:
            resolved_expected = None

        with self._lock:
            self._verify_locked()
            sequence = len(self._entries) + 1
            if sequence > MAX_JOURNAL_SEQUENCE:
                raise JournalError(
                    code="journal.sequence_exhausted",
                    path="$.sequence",
                    message="journal has exhausted the supported sequence range",
                )
            if resolved_expected is not None and resolved_expected != sequence:
                raise JournalConflictError(
                    code="journal.sequence_conflict",
                    path="$.expected_sequence",
                    message=(
                        f"expected sequence {resolved_expected}, current sequence is {sequence}"
                    ),
                )
            previous = self._entries[-1].fingerprint if self._entries else None
            entry = JournalEntry.create(sequence, document, previous)
            self._entries.append(entry)
            return entry

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[JournalEntry, ...]:
        """Read entries after an exclusive sequence cursor, bounded by ``limit``."""

        resolved_after = _integer(
            after_sequence,
            code="journal.after_sequence_type",
            path="$.after_sequence",
            label="after sequence",
        )
        if not 0 <= resolved_after <= MAX_JOURNAL_SEQUENCE:
            raise JournalError(
                code="journal.after_sequence_range",
                path="$.after_sequence",
                message=f"after sequence must be between 0 and {MAX_JOURNAL_SEQUENCE}",
            )
        resolved_limit = self._max_read_limit if limit is None else _integer(
            limit,
            code="journal.read_limit_type",
            path="$.limit",
            label="read limit",
        )
        if not 1 <= resolved_limit <= self._max_read_limit:
            raise JournalError(
                code="journal.read_limit_range",
                path="$.limit",
                message=f"read limit must be between 1 and {self._max_read_limit}",
            )

        with self._lock:
            self._verify_locked()
            start = min(resolved_after, len(self._entries))
            return tuple(self._entries[start : start + resolved_limit])

    def verify(self) -> None:
        """Verify all sequencing, event fingerprints, and hash-chain links."""

        with self._lock:
            self._verify_locked()

    def integrity_report(
        self,
        *,
        expected_count: int | None = None,
        expected_head: ContentFingerprint | None = None,
    ) -> JournalIntegrityReport:
        """Inspect storage without hiding the diagnostic behind a read failure."""

        with self._lock:
            return inspect_journal_entries(
                self._entries,
                expected_count=expected_count,
                expected_head=expected_head,
            )

    def _verify_locked(self) -> None:
        verify_journal_entries(self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
