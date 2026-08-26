"""Deterministic projections with resumable checkpoints and snapshots."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import cast

from ledgerloom.journal.entries import JournalEntry
from ledgerloom.journal.errors import JournalError, ProjectionError, ProjectionSnapshotError
from ledgerloom.journal.replay import (
    DecodedJournalEvent,
    JournalReplayer,
    ReplayCursor,
)
from ledgerloom.serialization.canonical import (
    JsonValue,
    canonical_json_bytes,
    loads_canonical_json,
)
from ledgerloom.serialization.errors import SerializationError
from ledgerloom.serialization.fingerprints import ContentFingerprint
from ledgerloom.serialization.schema import SchemaDocument, SchemaRef, SchemaRegistry

PROJECTION_SNAPSHOT_SCHEMA = SchemaRef.parse("ledgerloom.projection_snapshot", "1.0")

_PROJECTION_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_MAX_PROJECTION_NAME_LENGTH = 128
_SNAPSHOT_FIELDS = frozenset({"cursor", "projection", "state"})
_CURSOR_FIELDS = frozenset({"fingerprint", "sequence"})
_STATE_FIELDS = frozenset({"document", "fingerprint"})


def _projection_name(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not _PROJECTION_NAME_PATTERN.fullmatch(value):
        raise ProjectionError(
            code="projection.name_format",
            path=path,
            message="projection name must be a dotted lowercase identifier",
        )
    if len(value) > _MAX_PROJECTION_NAME_LENGTH:
        raise ProjectionError(
            code="projection.name_too_long",
            path=path,
            message=(
                f"projection name must contain at most {_MAX_PROJECTION_NAME_LENGTH} characters"
            ),
        )
    return value


def _require_fields(value: object, expected: frozenset[str], *, path: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ProjectionSnapshotError(
            code="projection.snapshot_object",
            path=path,
            message="snapshot value must be an object",
        )
    fields = frozenset(value)
    if fields != expected:
        missing = sorted(expected - fields)
        extra = sorted(fields - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise ProjectionSnapshotError(
            code="projection.snapshot_fields",
            path=path,
            message="snapshot fields are invalid: " + "; ".join(details),
        )
    return value


def _parse_fingerprint(value: object, *, path: str) -> ContentFingerprint:
    if not isinstance(value, str):
        raise ProjectionSnapshotError(
            code="projection.snapshot_fingerprint_type",
            path=path,
            message="fingerprint must be text",
        )
    try:
        return ContentFingerprint.parse(value)
    except SerializationError as error:
        raise ProjectionSnapshotError(
            code="projection.snapshot_fingerprint",
            path=path,
            message=error.message,
        ) from error


def _state_envelope(document: SchemaDocument) -> JsonValue:
    """Return the complete canonical state envelope as a JSON value."""

    return loads_canonical_json(document.content)


def _canonical_snapshot_payload(
    projection: str,
    cursor: ReplayCursor,
    state_document: SchemaDocument,
) -> dict[str, object]:
    return {
        "cursor": {
            "fingerprint": None if cursor.fingerprint is None else str(cursor.fingerprint),
            "sequence": cursor.sequence,
        },
        "projection": projection,
        "state": {
            "document": _state_envelope(state_document),
            "fingerprint": str(state_document.fingerprint),
        },
    }


@dataclass(frozen=True, slots=True)
class Projection[T]:
    """Name one deterministic reducer and its genesis state."""

    name: str
    initial_state: T
    reducer: Callable[[T, DecodedJournalEvent], T]

    def __post_init__(self) -> None:
        _projection_name(self.name, path="$.projection.name")
        if not callable(self.reducer):
            raise ProjectionError(
                code="projection.reducer_type",
                path="$.projection.reducer",
                message="projection reducer must be callable",
            )


@dataclass(frozen=True, slots=True)
class ProjectionCheckpoint[T]:
    """Pair projected state with the exact journal position that produced it."""

    projection: str
    state: T
    cursor: ReplayCursor

    def __post_init__(self) -> None:
        _projection_name(self.projection, path="$.checkpoint.projection")
        if not isinstance(self.cursor, ReplayCursor):
            raise ProjectionError(
                code="projection.checkpoint_cursor_type",
                path="$.checkpoint.cursor",
                message="checkpoint cursor must be a ReplayCursor",
            )


@dataclass(frozen=True, slots=True)
class ProjectionSnapshot:
    """Canonical, fingerprinted serialization of one projection checkpoint."""

    projection: str
    cursor: ReplayCursor
    state_document: SchemaDocument
    document: SchemaDocument

    def __post_init__(self) -> None:
        _projection_name(self.projection, path="$.snapshot.projection")
        if not isinstance(self.cursor, ReplayCursor):
            raise ProjectionSnapshotError(
                code="projection.snapshot_cursor_type",
                path="$.snapshot.cursor",
                message="snapshot cursor must be a ReplayCursor",
            )
        if not isinstance(self.state_document, SchemaDocument):
            raise ProjectionSnapshotError(
                code="projection.snapshot_state_document_type",
                path="$.snapshot.state",
                message="snapshot state must be a SchemaDocument",
            )
        if not isinstance(self.document, SchemaDocument):
            raise ProjectionSnapshotError(
                code="projection.snapshot_document_type",
                path="$.snapshot.document",
                message="snapshot document must be a SchemaDocument",
            )
        if self.document.schema != PROJECTION_SNAPSHOT_SCHEMA:
            raise ProjectionSnapshotError(
                code="projection.snapshot_schema",
                path="$.schema",
                message=f"snapshot schema must be {PROJECTION_SNAPSHOT_SCHEMA}",
            )
        expected = SchemaDocument.build(
            PROJECTION_SNAPSHOT_SCHEMA,
            _canonical_snapshot_payload(self.projection, self.cursor, self.state_document),
        )
        if self.document != expected:
            raise ProjectionSnapshotError(
                code="projection.snapshot_content_mismatch",
                path="$",
                message="snapshot content does not match its checkpoint values",
            )

    @classmethod
    def create[T](
        cls,
        checkpoint: ProjectionCheckpoint[T],
        *,
        registry: SchemaRegistry,
    ) -> ProjectionSnapshot:
        if not isinstance(checkpoint, ProjectionCheckpoint):
            raise ProjectionSnapshotError(
                code="projection.snapshot_checkpoint_type",
                path="$.checkpoint",
                message="checkpoint must be a ProjectionCheckpoint",
            )
        if not isinstance(registry, SchemaRegistry):
            raise ProjectionSnapshotError(
                code="projection.snapshot_registry_type",
                path="$.registry",
                message="registry must be a SchemaRegistry",
            )
        try:
            state_document = registry.encode(checkpoint.state)
        except SerializationError as error:
            raise ProjectionSnapshotError(
                code="projection.snapshot_state_encode",
                path="$.state",
                message=f"projection state cannot be encoded: {error.message}",
            ) from error
        document = SchemaDocument.build(
            PROJECTION_SNAPSHOT_SCHEMA,
            _canonical_snapshot_payload(
                checkpoint.projection,
                checkpoint.cursor,
                state_document,
            ),
        )
        return cls(checkpoint.projection, checkpoint.cursor, state_document, document)

    @classmethod
    def parse(
        cls,
        content: bytes,
        *,
        expected_fingerprint: ContentFingerprint | None = None,
    ) -> ProjectionSnapshot:
        try:
            document = SchemaDocument.parse(
                content,
                expected_fingerprint=expected_fingerprint,
            )
        except SerializationError as error:
            raise ProjectionSnapshotError(
                code="projection.snapshot_invalid",
                path=error.path,
                message=error.message,
            ) from error
        if document.schema != PROJECTION_SNAPSHOT_SCHEMA:
            raise ProjectionSnapshotError(
                code="projection.snapshot_schema",
                path="$.schema",
                message=f"snapshot schema must be {PROJECTION_SNAPSHOT_SCHEMA}",
            )

        payload = _require_fields(document.payload, _SNAPSHOT_FIELDS, path="$.payload")
        try:
            projection = _projection_name(payload["projection"], path="$.payload.projection")
        except ProjectionError as error:
            raise ProjectionSnapshotError(
                code="projection.snapshot_projection",
                path=error.path,
                message=error.message,
            ) from error
        cursor_value = _require_fields(payload["cursor"], _CURSOR_FIELDS, path="$.payload.cursor")
        fingerprint_value = cursor_value["fingerprint"]
        cursor_fingerprint = (
            None
            if fingerprint_value is None
            else _parse_fingerprint(
                fingerprint_value,
                path="$.payload.cursor.fingerprint",
            )
        )
        try:
            cursor = ReplayCursor(cursor_value["sequence"], cursor_fingerprint)
        except JournalError as error:
            raise ProjectionSnapshotError(
                code="projection.snapshot_cursor",
                path=error.path,
                message=error.message,
            ) from error

        state = _require_fields(payload["state"], _STATE_FIELDS, path="$.payload.state")
        state_fingerprint = _parse_fingerprint(
            state["fingerprint"],
            path="$.payload.state.fingerprint",
        )
        try:
            state_content = canonical_json_bytes(state["document"])
            state_document = SchemaDocument.parse(
                state_content,
                expected_fingerprint=state_fingerprint,
            )
        except SerializationError as error:
            raise ProjectionSnapshotError(
                code="projection.snapshot_state_invalid",
                path="$.payload.state.document",
                message=error.message,
            ) from error
        return cls(projection, cursor, state_document, document)

    @property
    def content(self) -> bytes:
        return self.document.content

    @property
    def fingerprint(self) -> ContentFingerprint:
        return self.document.fingerprint


class ProjectionRunner:
    """Replay events into named projections and recover them from snapshots."""

    def __init__(self, registry: SchemaRegistry) -> None:
        if not isinstance(registry, SchemaRegistry):
            raise ProjectionError(
                code="projection.registry_type",
                path="$.registry",
                message="registry must be a SchemaRegistry",
            )
        self._registry = registry
        self._replayer = JournalReplayer(registry)

    @property
    def registry(self) -> SchemaRegistry:
        return self._registry

    def project[T](
        self,
        entries: Iterable[JournalEntry],
        *,
        projection: Projection[T],
        checkpoint: ProjectionCheckpoint[T] | None = None,
    ) -> ProjectionCheckpoint[T]:
        if not isinstance(projection, Projection):
            raise ProjectionError(
                code="projection.definition_type",
                path="$.projection",
                message="projection must be a Projection",
            )
        if checkpoint is not None and not isinstance(checkpoint, ProjectionCheckpoint):
            raise ProjectionError(
                code="projection.checkpoint_type",
                path="$.checkpoint",
                message="checkpoint must be a ProjectionCheckpoint or None",
            )
        if checkpoint is not None and checkpoint.projection != projection.name:
            raise ProjectionError(
                code="projection.checkpoint_name_mismatch",
                path="$.checkpoint.projection",
                message="checkpoint belongs to a different projection",
            )
        initial_state = projection.initial_state if checkpoint is None else checkpoint.state
        cursor = None if checkpoint is None else checkpoint.cursor
        result = self._replayer.replay(
            entries,
            initial_state=initial_state,
            reducer=projection.reducer,
            cursor=cursor,
        )
        return ProjectionCheckpoint(projection.name, result.state, result.cursor)

    def snapshot[T](self, checkpoint: ProjectionCheckpoint[T]) -> ProjectionSnapshot:
        return ProjectionSnapshot.create(checkpoint, registry=self._registry)

    def restore[T](
        self,
        snapshot: ProjectionSnapshot | bytes,
        *,
        projection: Projection[T],
        expected_fingerprint: ContentFingerprint | None = None,
    ) -> ProjectionCheckpoint[T]:
        if not isinstance(projection, Projection):
            raise ProjectionError(
                code="projection.definition_type",
                path="$.projection",
                message="projection must be a Projection",
            )
        resolved = (
            ProjectionSnapshot.parse(
                snapshot,
                expected_fingerprint=expected_fingerprint,
            )
            if isinstance(snapshot, bytes)
            else snapshot
        )
        if not isinstance(resolved, ProjectionSnapshot):
            raise ProjectionSnapshotError(
                code="projection.snapshot_type",
                path="$.snapshot",
                message="snapshot must be ProjectionSnapshot or bytes",
            )
        if expected_fingerprint is not None and resolved.fingerprint != expected_fingerprint:
            raise ProjectionSnapshotError(
                code="projection.snapshot_fingerprint_mismatch",
                path="$.snapshot.fingerprint",
                message="snapshot does not match the expected fingerprint",
            )
        if resolved.projection != projection.name:
            raise ProjectionSnapshotError(
                code="projection.snapshot_name_mismatch",
                path="$.snapshot.projection",
                message="snapshot belongs to a different projection",
            )
        try:
            state = self._registry.decode(resolved.state_document)
        except SerializationError as error:
            raise ProjectionSnapshotError(
                code="projection.snapshot_state_decode",
                path="$.snapshot.state",
                message=f"snapshot state cannot be decoded: {error.message}",
            ) from error
        if not isinstance(state, type(projection.initial_state)):
            raise ProjectionSnapshotError(
                code="projection.snapshot_state_type",
                path="$.snapshot.state",
                message="snapshot state type does not match the projection genesis state",
            )
        return ProjectionCheckpoint(
            projection=projection.name,
            state=cast(T, state),
            cursor=resolved.cursor,
        )

    def recover[T](
        self,
        entries: Iterable[JournalEntry],
        *,
        projection: Projection[T],
        snapshot: ProjectionSnapshot | bytes,
        expected_fingerprint: ContentFingerprint | None = None,
    ) -> ProjectionCheckpoint[T]:
        checkpoint = self.restore(
            snapshot,
            projection=projection,
            expected_fingerprint=expected_fingerprint,
        )
        return self.project(entries, projection=projection, checkpoint=checkpoint)
