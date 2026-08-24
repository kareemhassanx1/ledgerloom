"""Verified, resumable replay of journal events into caller-owned state."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ledgerloom.journal.entries import MAX_JOURNAL_SEQUENCE, JournalEntry
from ledgerloom.journal.errors import JournalError, JournalReplayError
from ledgerloom.journal.integrity import inspect_journal_entries
from ledgerloom.serialization.errors import SerializationError
from ledgerloom.serialization.fingerprints import ContentFingerprint
from ledgerloom.serialization.schema import SchemaRef, SchemaRegistry


@dataclass(frozen=True, slots=True)
class ReplayCursor:
    """Anchor a replay position to a verified journal chain fingerprint."""

    sequence: int
    fingerprint: ContentFingerprint | None

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise JournalError(
                code="journal.cursor_sequence_type",
                path="$.cursor.sequence",
                message="cursor sequence must be an integer",
            )
        if not 0 <= self.sequence <= MAX_JOURNAL_SEQUENCE:
            raise JournalError(
                code="journal.cursor_sequence_range",
                path="$.cursor.sequence",
                message=f"cursor sequence must be between 0 and {MAX_JOURNAL_SEQUENCE}",
            )
        if self.fingerprint is not None and not isinstance(
            self.fingerprint, ContentFingerprint
        ):
            raise JournalError(
                code="journal.cursor_fingerprint_type",
                path="$.cursor.fingerprint",
                message="cursor fingerprint must be a ContentFingerprint or None",
            )
        if self.sequence == 0 and self.fingerprint is not None:
            raise JournalError(
                code="journal.cursor_genesis_fingerprint",
                path="$.cursor.fingerprint",
                message="the genesis cursor must not have a fingerprint",
            )
        if self.sequence > 0 and self.fingerprint is None:
            raise JournalError(
                code="journal.cursor_fingerprint_missing",
                path="$.cursor.fingerprint",
                message="a cursor after genesis must include its chain fingerprint",
            )

    @classmethod
    def genesis(cls) -> ReplayCursor:
        return cls(0, None)

    @classmethod
    def from_entry(cls, entry: JournalEntry) -> ReplayCursor:
        if not isinstance(entry, JournalEntry):
            raise JournalError(
                code="journal.cursor_entry_type",
                path="$.entry",
                message="entry must be a JournalEntry",
            )
        return cls(entry.sequence, entry.fingerprint)


@dataclass(frozen=True, slots=True)
class DecodedJournalEvent:
    """A decoded value plus the immutable metadata that established its order."""

    sequence: int
    schema: SchemaRef
    value: object
    event_fingerprint: ContentFingerprint
    chain_fingerprint: ContentFingerprint

    @classmethod
    def from_entry(cls, entry: JournalEntry, value: object) -> DecodedJournalEvent:
        return cls(
            sequence=entry.sequence,
            schema=entry.document.schema,
            value=value,
            event_fingerprint=entry.document.fingerprint,
            chain_fingerprint=entry.fingerprint,
        )


@dataclass(frozen=True, slots=True)
class ReplayResult[T]:
    """Return projected state and a cursor suitable for the next segment."""

    state: T
    started_from: ReplayCursor
    cursor: ReplayCursor
    applied_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.started_from, ReplayCursor):
            raise JournalError(
                code="journal.replay_start_cursor_type",
                path="$.started_from",
                message="started_from must be a ReplayCursor",
            )
        if not isinstance(self.cursor, ReplayCursor):
            raise JournalError(
                code="journal.replay_cursor_type",
                path="$.cursor",
                message="cursor must be a ReplayCursor",
            )
        if isinstance(self.applied_count, bool) or not isinstance(self.applied_count, int):
            raise JournalError(
                code="journal.replay_count_type",
                path="$.applied_count",
                message="applied count must be an integer",
            )
        if self.applied_count < 0:
            raise JournalError(
                code="journal.replay_count_range",
                path="$.applied_count",
                message="applied count must not be negative",
            )
        if self.cursor.sequence - self.started_from.sequence != self.applied_count:
            raise JournalError(
                code="journal.replay_count_mismatch",
                path="$.applied_count",
                message="applied count must equal the cursor sequence advance",
            )


def _decode_all(
    entries: tuple[JournalEntry, ...],
    registry: SchemaRegistry,
) -> tuple[DecodedJournalEvent, ...]:
    decoded: list[DecodedJournalEvent] = []
    for offset, entry in enumerate(entries):
        try:
            value = registry.decode(entry.document)
        except SerializationError as error:
            suffix = error.path.removeprefix("$")
            code = (
                "journal.replay_unknown_schema"
                if error.code == "schema.unknown"
                else "journal.replay_decode"
            )
            raise JournalReplayError(
                code=code,
                path=f"$[{offset}].document{suffix}",
                message=f"event {entry.sequence} cannot be decoded: {error.message}",
            ) from error
        decoded.append(DecodedJournalEvent.from_entry(entry, value))
    return tuple(decoded)


def replay_entries[T](
    entries: Iterable[JournalEntry],
    *,
    registry: SchemaRegistry,
    initial_state: T,
    reducer: Callable[[T, DecodedJournalEvent], T],
    cursor: ReplayCursor | None = None,
) -> ReplayResult[T]:
    """Verify and decode a segment completely before reducing any event.

    Reducers are called in sequence order only after the entire segment passes
    hash-chain and schema decoding checks. This prevents corrupted or unknown
    events near the tail from causing a partially applied projection.
    """

    if isinstance(entries, (str, bytes, bytearray)) or not isinstance(entries, Iterable):
        raise JournalError(
            code="journal.entries_type",
            path="$",
            message="entries must be an iterable of JournalEntry values",
        )
    if not isinstance(registry, SchemaRegistry):
        raise JournalError(
            code="journal.replay_registry_type",
            path="$.registry",
            message="registry must be a SchemaRegistry",
        )
    if not callable(reducer):
        raise JournalError(
            code="journal.replay_reducer_type",
            path="$.reducer",
            message="reducer must be callable",
        )
    start = ReplayCursor.genesis() if cursor is None else cursor
    if not isinstance(start, ReplayCursor):
        raise JournalError(
            code="journal.replay_cursor_type",
            path="$.cursor",
            message="cursor must be a ReplayCursor",
        )

    materialized = tuple(entries)
    if start.sequence == MAX_JOURNAL_SEQUENCE:
        if materialized:
            raise JournalError(
                code="journal.sequence_exhausted",
                path="$[0].sequence",
                message="cannot replay entries after the maximum supported sequence",
            )
        return ReplayResult(initial_state, start, start, 0)

    report = inspect_journal_entries(
        materialized,
        starting_sequence=start.sequence + 1,
        previous_fingerprint=start.fingerprint,
    )
    report.require_valid()
    decoded = _decode_all(materialized, registry)

    state = initial_state
    for event in decoded:
        state = reducer(state, event)

    end = start if not materialized else ReplayCursor.from_entry(materialized[-1])
    return ReplayResult(
        state=state,
        started_from=start,
        cursor=end,
        applied_count=len(decoded),
    )


class JournalReplayer:
    """Reusable replay service bound to one schema registry."""

    def __init__(self, registry: SchemaRegistry) -> None:
        if not isinstance(registry, SchemaRegistry):
            raise JournalError(
                code="journal.replay_registry_type",
                path="$.registry",
                message="registry must be a SchemaRegistry",
            )
        self._registry = registry

    @property
    def registry(self) -> SchemaRegistry:
        return self._registry

    def replay[T](
        self,
        entries: Iterable[JournalEntry],
        *,
        initial_state: T,
        reducer: Callable[[T, DecodedJournalEvent], T],
        cursor: ReplayCursor | None = None,
    ) -> ReplayResult[T]:
        return replay_entries(
            entries,
            registry=self._registry,
            initial_state=initial_state,
            reducer=reducer,
            cursor=cursor,
        )

