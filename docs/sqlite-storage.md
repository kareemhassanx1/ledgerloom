# SQLite journal storage

`SQLiteJournal` is the durable counterpart to `InMemoryJournal`. It stores each
canonical schema document with its sequence, predecessor fingerprint, and chain
fingerprint. A separate metadata row records the committed entry count and head,
which makes a missing tail or stale copy observable during verification.

## Opening and appending

```python
from pathlib import Path

from ledgerloom.storage import SQLiteJournal, SQLiteJournalMode

with SQLiteJournal(
    Path("ledgerloom.sqlite3"),
    mode=SQLiteJournalMode.CREATE,
) as journal:
    entries = journal.append_many(
        documents,
        expected_sequence=journal.next_sequence,
    )
```

Opening mode is an explicit part of the boundary contract:

- `CREATE` is the default. It creates a missing journal or opens an existing
  journal for verified writes.
- `OPEN` requires an existing on-disk journal and permits verified writes.
- `READ_ONLY` requires an existing on-disk journal, opens it through SQLite's
  read-only URI mode, and rejects append attempts.

The latter two modes return `journal.storage_not_found` for a missing path; they
never create a placeholder database. Existing databases are never initialized
or repaired during open. Eager verification is enabled by default in every
mode. Setting `verify_on_open=False` is accepted only with `READ_ONLY`, where it
supports diagnostic inspection of a damaged chain without granting a write
path.

`append_many()` requires at least one `SchemaDocument`. It validates the complete
input before opening a write transaction, takes an immediate SQLite write lock,
checks `expected_sequence`, inserts every entry, and advances the metadata head.
Any validation, constraint, I/O, or commit failure rolls back the entire batch.
If SQLite raises while committing, the adapter explicitly rolls back any
transaction that remains active before propagating the error; the connection is
therefore not left stranded inside the failed transaction. Use `append()` for
the same behavior with one document.

## Restart and recovery checks

Closing and reopening the same path reconstructs the immutable journal entries
from their canonical bytes. Opening, reading, appending, and explicit `verify()`
all reject an invalid document fingerprint, a sequence gap, a broken chain link,
or metadata that disagrees with the stored rows. No automatic repair is attempted.

`inspect_snapshot()` reads entries and the stored count/head metadata inside one
read transaction. It returns a `StoredJournalSnapshot` containing the verified
prefix and its `JournalIntegrityReport`, so callers do not need to perform a
second database read that might observe a different state. Call
`snapshot.require_valid()` when the operation requires the complete journal.

Both `inspect_snapshot()` and `integrity_report()` accept an external expected
count or head fingerprint. These anchors are checked only after the journal is
valid against its own metadata. Keeping them outside the database lets a caller
detect a truncated or stale but internally consistent copy before replaying
projections.

```python
from ledgerloom.serialization import ContentFingerprint

with SQLiteJournal(
    Path("ledgerloom.sqlite3"),
    mode=SQLiteJournalMode.READ_ONLY,
    verify_on_open=False,
) as journal:
    snapshot = journal.inspect_snapshot(
        expected_count=stored_entry_count,
        expected_head=ContentFingerprint.parse(stored_head),
    )
    print(snapshot.integrity.is_valid, snapshot.integrity.issues)
```

## Concurrent writers

Multiple `SQLiteJournal` instances may use the same database file. SQLite
serializes write transactions. Once a writer owns the lock it reads the latest
committed head; if another writer already advanced that head, a stale
`expected_sequence` raises `JournalConflictError` without inserting any rows.
Callers can then reload state and decide whether retrying is appropriate.

Reads are capped by `max_read_limit` and use the same exclusive cursor semantics
as the in-memory journal. The adapter enables WAL mode for file-backed operation
and uses full synchronous commits so successfully returned appends survive a
normal close and reopen.
