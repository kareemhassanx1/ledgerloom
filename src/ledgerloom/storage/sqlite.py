"""Managed SQLite connections and atomic transaction boundaries."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from ledgerloom.storage.errors import StorageError

SQLITE_STORAGE_SCHEMA_VERSION = 1
_MAX_TIMEOUT_SECONDS = 60.0


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
                return connection.execute(sql, parameters).fetchone()
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
