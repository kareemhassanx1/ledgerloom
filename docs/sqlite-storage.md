# SQLite journal storage

`SQLiteJournal` is the durable counterpart to `InMemoryJournal`. It stores each
canonical schema document with its sequence, predecessor fingerprint, and chain
fingerprint. A separate metadata row records the committed entry count and head,
which makes a missing tail or stale copy observable during verification.

## Opening and appending

```python
from pathlib import Path

from ledgerloom.storage import SQLiteJournal

with SQLiteJournal(Path("ledgerloom.sqlite3")) as journal:
    entries = journal.append_many(
        documents,
        expected_sequence=journal.next_sequence,
    )
```

`append_many()` requires at least one `SchemaDocument`. It validates the complete
input before opening a write transaction, takes an immediate SQLite write lock,
checks `expected_sequence`, inserts every entry, and advances the metadata head.
Any validation, constraint, I/O, or commit failure rolls back the entire batch.
Use `append()` for the same behavior with one document.

## Restart and recovery checks

Closing and reopening the same path reconstructs the immutable journal entries
from their canonical bytes. Opening, reading, appending, and explicit `verify()`
all reject an invalid document fingerprint, a sequence gap, a broken chain link,
or metadata that disagrees with the stored rows. No automatic repair is attempted.

`integrity_report()` also accepts an external expected count or head fingerprint.
This lets a caller compare a reopened database with a separately retained
checkpoint before replaying projections.

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
