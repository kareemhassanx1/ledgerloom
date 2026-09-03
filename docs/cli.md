# Command-line workflows

LedgerLoom exposes the `ledgerloom` command and the equivalent
`python -m ledgerloom` entry point. Every operational invocation writes exactly
one canonical JSON object followed by a newline to standard output. Successful
objects have `command`, `ok: true`, and `result`; failures have `command`,
`ok: false`, and an `error` with stable `code`, `path`, and `message` fields.

## Import

Import CSV or JSON Lines data into a durable journal. The default mapped fields
are `record_id`, `account_id`, `amount`, `currency`, `occurred_at`, and optional
`reference`. File format is inferred from `.csv`, `.jsonl`, or `.ndjson`, or can
be selected explicitly.

```shell
ledgerloom import bank.csv --database ledgerloom.db --source bank --batch bank-2026-09
ledgerloom import processor.jsonl --database ledgerloom.db --source processor --batch processor-2026-09
```

Entry identifiers are deterministically derived from the source-scoped record
identity. Row failures are retained in the serialized batch without rejected
values. A partially accepted batch is still committed atomically and returns
exit code 5.

The CLI accepts at most 100,000 data records per import by default. Set a lower
or higher positive limit explicitly with `--max-records COUNT`. Exceeding that
limit rejects the request before a batch document is appended, preventing an
unbounded in-memory materialization from an unexpectedly large input:

```shell
ledgerloom import bank.csv --database ledgerloom.db --source bank --batch bank-2026-09 --max-records 5000
```

`import` is the only command that may create a missing database. If the path
already exists, its complete journal history is verified before duplicate source
records or batch identifiers are checked and before the new batch is appended.
Known application documents are discovered through the typed journal catalog;
unrelated schema names remain in the journal, while an unsupported version of a
known LedgerLoom schema stops the operation.

## Reconcile

Reconcile accepted entries from one imported batch against accepted entries in
all other imported batches in the journal. The default policy requires different
sources, the same account, and an amount within `0.01`; it searches a five-minute
window and uses exact references as additional ranking evidence.

```shell
ledgerloom reconcile --database ledgerloom.db --anchor-batch bank-2026-09 --batch reconciliation-2026-09
```

The amount tolerance, time window, score and margin thresholds, exact-reference
selection, and conflict strategy are explicit command options. The resulting
versioned reconciliation report is appended to the same journal.

`reconcile` requires an existing writable journal; a missing database is not
created. Before each run it rebuilds the set of claims from committed
reconciliation reports. Both participants in every prior matched pair are
reserved: a claimed entry cannot be reused as a later anchor, and selecting a
previously claimed candidate produces an explicit
`conflict.candidate_already_claimed` decision that identifies the prior anchor.
This preserves one-to-one matching across process restarts, not only within one
in-memory reconciliation batch.

## Inspect and export

`inspect` opens an existing database through SQLite's read-only mode. It captures
the stored rows and recovery metadata in one read transaction, then verifies that
single snapshot without decoding sensitive payload fields. A chain or anchor
mismatch reports the verified prefix and first integrity issue; malformed
storage that cannot be represented as journal entries returns a failure
envelope. Inspection does not create a missing database, initialize missing
tables, or repair damaged metadata.

An operator can provide independently retained recovery anchors. The count must
be a non-negative integer, and the head must use the complete
`sha256:<64 lowercase hexadecimal digits>` representation printed by a previous
successful inspection:

```shell
ledgerloom inspect --database ledgerloom.db
ledgerloom inspect --database ledgerloom.db --expected-count 3 --expected-head sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
ledgerloom export --database ledgerloom.db --output audit.jsonl
```

An anchor mismatch returns an integrity issue and exit code 4 even when the
database's internal chain is otherwise valid. Persist the count and head outside
the database if the goal is to detect restoration of an older, internally
consistent copy.

`export` also opens an existing database read-only and requires the complete
snapshot to verify before writing anything. It writes deterministic JSON Lines
to a temporary file in the destination directory, flushes and fsyncs it, and
publishes it atomically. Without `--force`, an exclusive publish step fails if
another process creates the destination during export; with `--force`, the
completed temporary file atomically replaces the destination.

The database and its `-wal`, `-shm`, and `-journal` sidecars are protected export
targets. Existing hard links to those files are protected as well. This prevents
an export from replacing journal storage even when `--force` is supplied. The
destination's parent directory must already exist.

Each export row contains the sequence, complete versioned document, document
fingerprint, and journal fingerprint. The command result includes the byte count,
entry count, and SHA-256 fingerprint of the complete exported byte stream.

## Failure and mutation contract

All commands verify the journal before consuming application objects. A failed
import or reconciliation append leaves the journal at its previous committed
head; a SQLite commit error is rolled back before the connection is reused or
closed. An optimistic sequence conflict has its own exit code so automation can
reload state rather than treating it as invalid input. Read-only commands never
append, initialize, or repair storage.

Unknown schema names are retained by the application catalog. `inspect` and
`export` remain format-level operations and preserve every structurally valid
event without decoding its application payload. In contrast, `import` and
`reconcile` fail closed on unsupported versions of the known ledger-batch or
reconciliation-report schemas because treating them as unrelated events could
hide state needed for duplicate or claim checks.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Operation completed successfully. |
| 1 | Unexpected internal failure. |
| 2 | Invalid arguments, values, or file request. |
| 3 | Requested journal object was not found. |
| 4 | Journal storage or integrity failure. |
| 5 | Import committed with one or more rejected rows. |
| 6 | Optimistic journal sequence conflict; reload before retrying. |
