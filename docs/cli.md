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

## Inspect and export

`inspect` verifies the persisted hash chain and reports every event without
decoding sensitive payload fields. `export` first verifies the journal, then
writes deterministic JSON Lines to a new file using an atomic replacement. It
refuses to overwrite a file unless `--force` is supplied.

```shell
ledgerloom inspect --database ledgerloom.db
ledgerloom export --database ledgerloom.db --output audit.jsonl
```

Each export row contains the sequence, complete versioned document, document
fingerprint, and journal fingerprint. The command result includes a fingerprint
of the complete exported byte stream.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Operation completed successfully. |
| 1 | Unexpected internal failure. |
| 2 | Invalid arguments, values, or file request. |
| 3 | Requested journal object was not found. |
| 4 | Journal storage or integrity failure. |
| 5 | Import committed with one or more rejected rows. |
