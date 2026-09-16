"""Online SQLite backups verified before exclusive atomic publication."""

import os
import sqlite3
import tempfile
import time
from contextlib import closing, suppress
from pathlib import Path

from ledgerloom.application.catalog import JournalCatalog
from ledgerloom.application.errors import ApplicationError
from ledgerloom.serialization.fingerprints import ContentFingerprint
from ledgerloom.storage.sqlite import SQLiteJournal, SQLiteJournalMode


def backup_journal(
    database: str | Path,
    output: str | Path,
    *,
    expected_count: int | None = None,
    expected_head: ContentFingerprint | None = None,
) -> dict[str, object]:
    """Capture WAL commits using SQLite backup and retain independent recovery anchors.

    Never overwrite a destination. Verify the copied snapshot, not a separately
    read live source. No source writes, schema initialization, or repair occur.
    A backup is restorable as an ordinary database; keep its anchors separately.
    """
    source, destination = Path(database).resolve(), Path(output).resolve()
    protected = (source, *(Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")))
    if any(
        destination == path
        or (destination.exists() and path.exists() and destination.samefile(path))
        for path in protected
    ):
        raise ApplicationError("backup.protected", "$.output", "output is a journal storage file")
    if destination.exists():
        raise ApplicationError("backup.exists", "$.output", "output already exists")
    if not source.is_file():
        raise ApplicationError("backup.source", "$.database", "source database does not exist")
    if not destination.parent.is_dir():
        raise ApplicationError("backup.parent", "$.output", "output parent does not exist")
    descriptor, name = tempfile.mkstemp(
        prefix=".ledgerloom-backup-", suffix=".db", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(name)
    deadline = time.monotonic() + 30

    def progress(status: int, remaining: int, total: int) -> None:
        if time.monotonic() > deadline:
            raise ApplicationError("backup.timeout", "$.database", "backup exceeded 30 seconds")

    try:
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as origin,
            closing(sqlite3.connect(temporary)) as target,
        ):
            origin.backup(target, pages=256, progress=progress, sleep=0.01)
            target.execute("PRAGMA journal_mode=DELETE")
        with SQLiteJournal(temporary, mode=SQLiteJournalMode.READ_ONLY) as journal:
            entries = journal.inspect_snapshot(
                expected_count=expected_count,
                expected_head=expected_head,
            ).require_valid()
            JournalCatalog.from_entries(entries)
        with temporary.open("r+b") as file:
            os.fsync(file.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as error:
            raise ApplicationError(
                "backup.exists", "$.output", "output appeared during backup"
            ) from error
        return {
            "output": str(destination),
            "entry_count": len(entries),
            "head_fingerprint": str(entries[-1].fingerprint) if entries else None,
            "bytes": destination.stat().st_size,
            "application_verified": True,
        }
    except sqlite3.Error as error:
        raise ApplicationError("backup.sqlite", "$.database", "SQLite backup failed") from error
    finally:
        for path in (temporary, *(Path(name + suffix) for suffix in ("-wal", "-shm", "-journal"))):
            with suppress(FileNotFoundError):
                path.unlink()
