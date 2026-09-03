# Operations and recovery

LedgerLoom makes journal damage visible; it does not decide how an operator
should repair or discard financial history. Treat the SQLite database, external
recovery anchors, and verified exports as audit data. Test recovery on a copy and
preserve the original evidence when an integrity check fails.

## Open the database with the intended authority

The CLI chooses a storage mode for each workflow:

- `import` may create a missing database and then appends one batch document.
- `reconcile` requires an existing database and appends one report document.
- `inspect` and `export` require an existing database and open it read-only.

A missing journal therefore returns exit code 3 for every command except
`import`. Read-only commands do not initialize tables, update recovery metadata,
or silently repair damage. Application code should make the same distinction
with `SQLiteJournalMode.CREATE`, `OPEN`, and `READ_ONLY`; see
[SQLite journal storage](sqlite-storage.md).

SQLite serializes writers, and each append checks the expected next sequence
inside the write transaction. A competing writer can therefore produce exit
code 6. Reload and re-evaluate the operation against the new committed head;
do not blindly replay a reconciliation decision made from stale state.

## Retain independent recovery anchors

The journal stores its entry count and head fingerprint beside the events. Those
values detect inconsistent rows or a missing tail inside that database, but an
older backup can still be internally consistent. After a trusted write, retain
the successful command's sequence/journal fingerprint or the following inspect
fields in a separate protected system:

- `result.entry_count`;
- `result.head_fingerprint`; and
- the identity and time of the backup or checkpoint they describe.

Validate a restored or transferred database against those values before use:

```shell
ledgerloom inspect \
  --database restored-ledgerloom.db \
  --expected-count 42 \
  --expected-head sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

Exit code 0 means both the internal history and supplied anchors agree. Exit
code 4 means storage or integrity verification failed; the JSON error or
`result.integrity.issues` gives the stable diagnostic.

## Back up a live journal

SQLite journals use WAL mode. Do not copy only the main database file while a
writer may be active: committed state can still reside in `-wal`, and copying
files at different instants can create an inconsistent backup. Use SQLite's
online backup facility, or stop writers and copy the database and any sidecars
as one coordinated operation. Then run read-only `inspect` on the backup with
the independently retained anchors.

LedgerLoom's `export` command is an audit interchange format, not a restorable
SQLite backup. It includes the complete canonical document and both document and
journal fingerprints for every event:

```shell
ledgerloom export --database ledgerloom.db --output archive/journal.jsonl
```

The parent directory must already exist. By default, export refuses an existing
destination even if another process creates it during publication. `--force`
atomically replaces an existing destination only after the full verified export
has been written and synced. The CLI refuses the database, its `-wal`, `-shm`,
and `-journal` sidecars, and existing hard links to those files as destinations.

Exports contain source batches and structured reconciliation evidence. Protect
them according to the same access and retention policy as the source financial
data.

## Respond to a failed integrity check

1. Stop writes to the affected database and preserve the database plus any
   sidecars.
2. Record the canonical JSON diagnostic and the external anchors used for the
   check.
3. Inspect a copy or known backup read-only. Do not edit journal rows, rewrite
   fingerprints, or change count/head metadata to make verification pass.
4. Compare the candidate backup with the last trusted external count and head.
5. Resume writes only from a copy that passes both internal and external checks.

A known LedgerLoom schema with an unsupported version produces
`catalog.schema_version_unsupported`. This is a compatibility failure, not
corruption: preserve the journal and use software that explicitly supports that
schema version before importing or reconciling again. Format-level inspection
and export preserve structurally valid events without decoding their application
payload. Unknown schema names are likewise retained by the catalog.

For command output and exit-code contracts, see
[Command-line workflows](cli.md).
