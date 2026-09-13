"""Transactional SQLite persistence for append-only journals."""

from __future__ import annotations

import math
import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from enum import StrEnum
from pathlib import Path
from threading import RLock
from types import TracebackType
from typing import cast

from ledgerloom.journal.entries import MAX_JOURNAL_SEQUENCE, JournalEntry
from ledgerloom.journal.errors import (
    JournalConflictError,
    JournalError,
    JournalIntegrityError,
    JournalStorageError,
)
from ledgerloom.journal.integrity import JournalIntegrityReport, inspect_journal_entries
from ledgerloom.journal.memory import DEFAULT_MAX_READ_LIMIT, MAX_CONFIGURED_READ_LIMIT
from ledgerloom.serialization.errors import SerializationError
from ledgerloom.serialization.fingerprints import ContentFingerprint
from ledgerloom.serialization.schema import SchemaDocument
from ledgerloom.storage.errors import StorageError
from ledgerloom.storage.snapshots import StoredJournalSnapshot

SQLITE_STORAGE_SCHEMA_VERSION = 1
_FORMAT_VERSION = 1
_BUSY_TIMEOUT_SECONDS = 5.0
_MAX_TIMEOUT_SECONDS = 60.0


class SQLiteJournalMode(StrEnum):
    """Control whether a journal may be created or mutated while opening."""

    CREATE = "create"
    OPEN = "open"
    READ_ONLY = "read_only"


def _validate_path(value: object) -> str:
    if not isinstance(value, str | Path):
        raise StorageError(
            code="storage.path_type",
            path="$.path",
            message="path must be text or a Path",
        )
    rendered = str(value)
    if not rendered or "\x00" in rendered:
        raise StorageError(
            code="storage.path_format",
            path="$.path",
            message="path must be non-empty text without null characters",
        )
    return rendered


def _validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StorageError(
            code="storage.timeout_type",
            path="$.timeout_seconds",
            message="timeout_seconds must be a number",
        )
    resolved = float(value)
    if not math.isfinite(resolved) or not 0 <= resolved <= _MAX_TIMEOUT_SECONDS:
        raise StorageError(
            code="storage.timeout_range",
            path="$.timeout_seconds",
            message=f"timeout_seconds must be between 0 and {_MAX_TIMEOUT_SECONDS}",
        )
    return resolved


class SqliteDatabase:
    """Own one SQLite connection with explicit transactions and schema metadata."""

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._path = _validate_path(path)
        self._timeout_seconds = _validate_timeout(timeout_seconds)
        self._lock = RLock()
        self._connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._path,
                timeout=self._timeout_seconds,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            timeout_milliseconds = round(self._timeout_seconds * 1000)
            connection.execute(f"PRAGMA busy_timeout = {timeout_milliseconds}")
            connection.execute("PRAGMA synchronous = FULL")
            if self._path != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            self._connection = connection
            self._initialize()
        except (sqlite3.Error, OSError) as error:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            raise StorageError(
                code="storage.open_failed",
                path="$.path",
                message="SQLite database could not be opened or initialized",
            ) from error

    @property
    def path(self) -> str:
        return self._path

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def is_closed(self) -> bool:
        return self._connection is None

    @property
    def schema_version(self) -> int:
        row = self._query_one(
            "SELECT value FROM ledgerloom_metadata WHERE key = ?",
            ("storage_schema_version",),
        )
        if row is None or row["value"] != str(SQLITE_STORAGE_SCHEMA_VERSION):
            raise StorageError(
                code="storage.schema_version",
                path="$.metadata.storage_schema_version",
                message="database storage schema version is missing or unsupported",
            )
        return SQLITE_STORAGE_SCHEMA_VERSION

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run one immediate transaction, rolling back every failed body."""

        with self._lock:
            connection = self._require_open()
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as error:
                raise StorageError(
                    code="storage.transaction_begin",
                    path="$.transaction",
                    message="SQLite transaction could not begin",
                ) from error
            try:
                yield connection
                connection.commit()
            except sqlite3.Error as error:
                connection.rollback()
                raise StorageError(
                    code="storage.transaction_failed",
                    path="$.transaction",
                    message="SQLite transaction failed and was rolled back",
                ) from error
            except BaseException:
                connection.rollback()
                raise

    def close(self) -> None:
        """Close the owned connection; repeated closes are harmless."""

        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _initialize(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ledgerloom_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                ) WITHOUT ROWID
                """
            )
            connection.execute(
                """
                INSERT INTO ledgerloom_metadata(key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                ("storage_schema_version", str(SQLITE_STORAGE_SCHEMA_VERSION)),
            )
        _ = self.schema_version

    def _require_open(self) -> sqlite3.Connection:
        connection = self._connection
        if connection is None:
            raise StorageError(
                code="storage.closed",
                path="$.database",
                message="SQLite database is closed",
            )
        return connection

    def _query_one(
        self,
        sql: str,
        parameters: tuple[object, ...] = (),
    ) -> sqlite3.Row | None:
        with self._lock:
            connection = self._require_open()
            try:
                return cast(
                    sqlite3.Row | None,
                    connection.execute(sql, parameters).fetchone(),
                )
            except sqlite3.Error as error:
                raise StorageError(
                    code="storage.read_failed",
                    path="$.database",
                    message="SQLite read failed",
                ) from error

    def __enter__(self) -> SqliteDatabase:
        self._require_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _integer(value: object, *, code: str, path: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise JournalError(code=code, path=path, message=f"{label} must be an integer")
    return value


def _fingerprint(value: object, *, path: str, nullable: bool = False) -> ContentFingerprint | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise JournalIntegrityError(
            code="journal.sqlite_fingerprint_type",
            path=path,
            message="stored fingerprint must be text",
        )
    try:
        return ContentFingerprint.parse(value)
    except SerializationError as error:
        raise JournalIntegrityError(
            code="journal.sqlite_fingerprint_invalid",
            path=path,
            message="stored fingerprint is invalid",
        ) from error


class SQLiteJournal:
    """Persist a verified journal in one SQLite database.

    Each append transaction takes SQLite's write lock before reading the head,
    so an ``expected_sequence`` check and all inserts commit as one atomic unit.
    Separate instances may safely coordinate through the same database file.
    """

    def __init__(
        self,
        database: str | os.PathLike[str],
        *,
        max_read_limit: int = DEFAULT_MAX_READ_LIMIT,
        mode: SQLiteJournalMode = SQLiteJournalMode.CREATE,
        verify_on_open: bool = True,
    ) -> None:
        try:
            database_text = os.fspath(database)
        except TypeError as error:
            raise JournalError(
                code="journal.database_path_type",
                path="$.database",
                message="database path must be text or path-like",
            ) from error
        if not isinstance(database_text, str) or not database_text or "\x00" in database_text:
            raise JournalError(
                code="journal.database_path_format",
                path="$.database",
                message="database path must be non-empty text without null characters",
            )
        if not isinstance(mode, SQLiteJournalMode):
            raise JournalError(
                code="journal.database_mode_type",
                path="$.mode",
                message="mode must be a SQLiteJournalMode",
            )
        if not isinstance(verify_on_open, bool):
            raise JournalError(
                code="journal.verify_on_open_type",
                path="$.verify_on_open",
                message="verify_on_open must be a boolean",
            )
        if not verify_on_open and mode is not SQLiteJournalMode.READ_ONLY:
            raise JournalError(
                code="journal.unverified_writable_open",
                path="$.verify_on_open",
                message="only a read-only journal may skip verification on open",
            )
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
                message=f"max read limit must be between 1 and {MAX_CONFIGURED_READ_LIMIT}",
            )

        is_memory = database_text == ":memory:"
        if is_memory and mode is not SQLiteJournalMode.CREATE:
            raise JournalStorageError(
                code="journal.storage_not_found",
                path="$.database",
                message="an existing on-disk journal is required",
            )
        database_path = None if is_memory else Path(database_text).resolve()
        exists = is_memory or (database_path is not None and database_path.is_file())
        if mode is not SQLiteJournalMode.CREATE and not exists:
            raise JournalStorageError(
                code="journal.storage_not_found",
                path="$.database",
                message="journal database was not found",
            )

        self._database = database_text
        self._max_read_limit = resolved_limit
        self._mode = mode
        self._lock = RLock()
        self._closed = False
        try:
            connection_target = database_text
            use_uri = False
            if mode is not SQLiteJournalMode.CREATE:
                assert database_path is not None
                sqlite_mode = "ro" if mode is SQLiteJournalMode.READ_ONLY else "rw"
                connection_target = f"{database_path.as_uri()}?mode={sqlite_mode}"
                use_uri = True
            self._connection = sqlite3.connect(
                connection_target,
                timeout=_BUSY_TIMEOUT_SECONDS,
                isolation_level=None,
                check_same_thread=False,
                uri=use_uri,
            )
            self._configure(writable=mode is not SQLiteJournalMode.READ_ONLY)
            if mode is SQLiteJournalMode.CREATE and (is_memory or not exists):
                self._initialize_schema()
            if verify_on_open:
                self.verify()
        except (sqlite3.Error, OSError) as error:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            self._closed = True
            raise JournalStorageError(
                code="journal.storage_open_failed",
                path="$.database",
                message="SQLite journal could not be opened or configured",
            ) from error
        except BaseException:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            self._closed = True
            raise

    @property
    def database(self) -> Path | str:
        return self._database if self._database == ":memory:" else Path(self._database)

    @property
    def max_read_limit(self) -> int:
        return self._max_read_limit

    @property
    def mode(self) -> SQLiteJournalMode:
        return self._mode

    @property
    def next_sequence(self) -> int:
        entries, _ = self._verified_snapshot()
        return len(entries) + 1

    @property
    def head_fingerprint(self) -> ContentFingerprint | None:
        entries, _ = self._verified_snapshot()
        return entries[-1].fingerprint if entries else None

    def append(
        self,
        document: SchemaDocument,
        *,
        expected_sequence: int | None = None,
    ) -> JournalEntry:
        """Atomically append one document."""

        return self.append_many((document,), expected_sequence=expected_sequence)[0]

    def append_many(
        self,
        documents: Iterable[SchemaDocument],
        *,
        expected_sequence: int | None = None,
    ) -> tuple[JournalEntry, ...]:
        """Atomically append a non-empty batch or leave the journal unchanged."""

        if isinstance(documents, str | bytes | bytearray) or not isinstance(documents, Iterable):
            raise JournalError(
                code="journal.documents_type",
                path="$.documents",
                message="documents must be an iterable of SchemaDocument values",
            )
        resolved_documents = tuple(documents)
        if not resolved_documents:
            raise JournalError(
                code="journal.documents_empty",
                path="$.documents",
                message="documents must contain at least one item",
            )
        for index, document in enumerate(resolved_documents):
            if not isinstance(document, SchemaDocument):
                raise JournalError(
                    code="journal.document_type",
                    path=f"$.documents[{index}]",
                    message="document must be a SchemaDocument",
                )
        resolved_expected = self._validate_expected_sequence(expected_sequence)

        try:
            with self._lock, self._transaction(write=True):
                existing, metadata = self._load_snapshot()
                self._require_valid(existing, metadata)
                next_sequence = len(existing) + 1
                if resolved_expected is not None and resolved_expected != next_sequence:
                    raise JournalConflictError(
                        code="journal.sequence_conflict",
                        path="$.expected_sequence",
                        message=(
                            f"expected sequence {resolved_expected}, "
                            f"current sequence is {next_sequence}"
                        ),
                    )
                if len(resolved_documents) > MAX_JOURNAL_SEQUENCE - len(existing):
                    raise JournalError(
                        code="journal.sequence_exhausted",
                        path="$.documents",
                        message="journal has exhausted the supported sequence range",
                    )

                previous = existing[-1].fingerprint if existing else None
                appended: list[JournalEntry] = []
                for document in resolved_documents:
                    entry = JournalEntry.create(next_sequence, document, previous)
                    self._connection.execute(
                        """
                        INSERT INTO ledgerloom_journal_entries (
                            sequence, document, previous_fingerprint, fingerprint
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            entry.sequence,
                            entry.document.content,
                            (
                                None
                                if entry.previous_fingerprint is None
                                else str(entry.previous_fingerprint)
                            ),
                            str(entry.fingerprint),
                        ),
                    )
                    appended.append(entry)
                    previous = entry.fingerprint
                    next_sequence += 1
                self._connection.execute(
                    """
                    UPDATE ledgerloom_journal_metadata
                    SET entry_count = ?, head_fingerprint = ?
                    WHERE singleton = 1
                    """,
                    (len(existing) + len(appended), str(appended[-1].fingerprint)),
                )
                return tuple(appended)
        except sqlite3.Error as error:
            raise self._storage_error(error) from error

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[JournalEntry, ...]:
        """Read a verified page after an exclusive sequence cursor."""

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
        resolved_limit = (
            self._max_read_limit
            if limit is None
            else _integer(
                limit,
                code="journal.read_limit_type",
                path="$.limit",
                label="read limit",
            )
        )
        if not 1 <= resolved_limit <= self._max_read_limit:
            raise JournalError(
                code="journal.read_limit_range",
                path="$.limit",
                message=f"read limit must be between 1 and {self._max_read_limit}",
            )
        entries, _ = self._verified_snapshot()
        start = min(resolved_after, len(entries))
        return entries[start : start + resolved_limit]

    def verify(self) -> None:
        """Verify stored rows, metadata, event bytes, and every hash-chain link."""

        self._verified_snapshot()

    def integrity_report(
        self,
        *,
        expected_count: int | None = None,
        expected_head: ContentFingerprint | None = None,
    ) -> JournalIntegrityReport:
        """Inspect the durable journal and optional external recovery expectations."""

        return self.inspect_snapshot(
            expected_count=expected_count,
            expected_head=expected_head,
        ).integrity

    def inspect_snapshot(
        self,
        *,
        expected_count: int | None = None,
        expected_head: ContentFingerprint | None = None,
    ) -> StoredJournalSnapshot:
        """Read entries and integrity metadata from one consistent transaction."""

        try:
            with self._lock, self._transaction(write=False):
                entries, metadata = self._load_snapshot()
                report = inspect_journal_entries(
                    entries,
                    expected_count=metadata[0],
                    expected_head=metadata[1],
                )
                if report.is_valid and (expected_count is not None or expected_head is not None):
                    report = inspect_journal_entries(
                        entries,
                        expected_count=expected_count,
                        expected_head=expected_head,
                    )
                return StoredJournalSnapshot(entries[: report.valid_entries], report)
        except sqlite3.Error as error:
            raise self._storage_error(error) from error

    def close(self) -> None:
        """Close this adapter's connection. The database remains reusable."""

        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> SQLiteJournal:
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __len__(self) -> int:
        entries, _ = self._verified_snapshot()
        return len(entries)

    def _configure(self, *, writable: bool) -> None:
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        if writable:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")

    def _initialize_schema(self) -> None:
        try:
            with self._lock, self._transaction(write=True):
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ledgerloom_journal_metadata (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        format_version INTEGER NOT NULL,
                        entry_count INTEGER NOT NULL CHECK (entry_count >= 0),
                        head_fingerprint TEXT
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ledgerloom_journal_entries (
                        sequence INTEGER PRIMARY KEY,
                        document BLOB NOT NULL,
                        previous_fingerprint TEXT,
                        fingerprint TEXT NOT NULL UNIQUE
                    )
                    """
                )
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO ledgerloom_journal_metadata (
                        singleton, format_version, entry_count, head_fingerprint
                    ) VALUES (1, ?, 0, NULL)
                    """,
                    (_FORMAT_VERSION,),
                )
        except sqlite3.Error as error:
            raise self._storage_error(error) from error

    def _verified_snapshot(
        self,
    ) -> tuple[tuple[JournalEntry, ...], tuple[int, ContentFingerprint | None]]:
        try:
            with self._lock, self._transaction(write=False):
                entries, metadata = self._load_snapshot()
                self._require_valid(entries, metadata)
                return entries, metadata
        except sqlite3.Error as error:
            raise self._storage_error(error) from error

    def _load_snapshot(
        self,
    ) -> tuple[tuple[JournalEntry, ...], tuple[int, ContentFingerprint | None]]:
        metadata_row = self._connection.execute(
            """
            SELECT format_version, entry_count, head_fingerprint
            FROM ledgerloom_journal_metadata
            WHERE singleton = 1
            """
        ).fetchone()
        if metadata_row is None:
            raise JournalIntegrityError(
                code="journal.sqlite_metadata_missing",
                path="$.metadata",
                message="journal metadata row is missing",
            )
        format_version, entry_count, head_text = metadata_row
        if format_version != _FORMAT_VERSION:
            raise JournalStorageError(
                code="journal.sqlite_format_unsupported",
                path="$.metadata.format_version",
                message=f"SQLite journal format {format_version!r} is not supported",
            )
        if isinstance(entry_count, bool) or not isinstance(entry_count, int) or entry_count < 0:
            raise JournalIntegrityError(
                code="journal.sqlite_count_invalid",
                path="$.metadata.entry_count",
                message="stored entry count must be a non-negative integer",
            )
        head = _fingerprint(
            head_text,
            path="$.metadata.head_fingerprint",
            nullable=entry_count == 0,
        )
        if (entry_count == 0) != (head is None):
            raise JournalIntegrityError(
                code="journal.sqlite_head_inconsistent",
                path="$.metadata.head_fingerprint",
                message="stored head fingerprint does not agree with entry count",
            )

        rows = self._connection.execute(
            """
            SELECT sequence, document, previous_fingerprint, fingerprint
            FROM ledgerloom_journal_entries
            ORDER BY sequence
            """
        ).fetchall()
        entries: list[JournalEntry] = []
        for row_index, (sequence, content, previous_text, fingerprint_text) in enumerate(rows):
            path = f"$[{row_index}]"
            if not isinstance(content, bytes):
                raise JournalIntegrityError(
                    code="journal.sqlite_document_type",
                    path=f"{path}.document",
                    message="stored document must be bytes",
                )
            try:
                document = SchemaDocument.parse(content)
                previous = _fingerprint(
                    previous_text,
                    path=f"{path}.previous_fingerprint",
                    nullable=True,
                )
                fingerprint = _fingerprint(
                    fingerprint_text,
                    path=f"{path}.fingerprint",
                )
                assert fingerprint is not None
                entries.append(JournalEntry(sequence, document, previous, fingerprint))
            except SerializationError as error:
                raise JournalIntegrityError(
                    code="journal.sqlite_document_invalid",
                    path=f"{path}.document",
                    message="stored document is not a valid canonical schema document",
                ) from error
        return tuple(entries), (entry_count, head)

    @staticmethod
    def _require_valid(
        entries: tuple[JournalEntry, ...],
        metadata: tuple[int, ContentFingerprint | None],
    ) -> None:
        inspect_journal_entries(
            entries,
            expected_count=metadata[0],
            expected_head=metadata[1],
        ).require_valid()

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[None]:
        self._ensure_open()
        if write and self._mode is SQLiteJournalMode.READ_ONLY:
            raise JournalStorageError(
                code="journal.storage_read_only",
                path="$.database",
                message="read-only journals do not accept writes",
            )
        self._connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
        except BaseException:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        else:
            try:
                self._connection.commit()
            except BaseException:
                if self._connection.in_transaction:
                    with suppress(sqlite3.Error):
                        self._connection.rollback()
                raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise JournalStorageError(
                code="journal.storage_closed",
                path="$.database",
                message="SQLite journal is closed",
            )

    @staticmethod
    def _storage_error(error: sqlite3.Error) -> JournalStorageError:
        locked = "locked" in str(error).lower() or "busy" in str(error).lower()
        return JournalStorageError(
            code="journal.storage_busy" if locked else "journal.storage_error",
            path="$.database",
            message=("SQLite journal is busy" if locked else "SQLite journal operation failed"),
        )

    def _validate_expected_sequence(self, value: int | None) -> int | None:
        if value is None:
            return None
        resolved = _integer(
            value,
            code="journal.expected_sequence_type",
            path="$.expected_sequence",
            label="expected sequence",
        )
        if not 1 <= resolved <= MAX_JOURNAL_SEQUENCE:
            raise JournalError(
                code="journal.expected_sequence_range",
                path="$.expected_sequence",
                message=f"expected sequence must be between 1 and {MAX_JOURNAL_SEQUENCE}",
            )
        return resolved
