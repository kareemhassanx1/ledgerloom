# LedgerLoom

LedgerLoom is a deterministic, local-first reconciliation engine for Python.
It turns records from unrelated financial systems into a consistent ledger,
matches related entries with explainable rules, and preserves an auditable
trail of every decision.

The project is designed for teams that need reconciliation behavior they can
run locally and reproduce later. Network access is never required by the core
engine, decimal amounts remain exact, and every state transition is represented
as immutable domain data.

## Capabilities

- Strict value objects for identifiers, money, timestamps, source records, and ledger entries.
- Ordered ledger batches that preserve accepted entries and structured rejection diagnostics.
- Canonical, versioned serialization and SHA-256 content fingerprints.
- Append-only journals with integrity reports, resumable replay, and snapshots.
- Configurable exact, tolerance, date-window, and composite match rules.
- Explainable reconciliation decisions and conflict resolution.
- Streaming CSV and JSON Lines ingestion with structured validation errors.
- SQLite journal persistence with explicit create, writable-open, and read-only modes.
- A command-line interface for repeatable import, reconciliation, inspection, and export runs.
- Reports that distinguish matched, unmatched, ambiguous, and invalid records.

## Quick start

After installing the package, import two batches into one journal and reconcile
one against the other:

```shell
ledgerloom import bank.csv --database ledgerloom.db --source bank --batch bank-2026-09
ledgerloom import processor.jsonl --database ledgerloom.db --source processor --batch processor-2026-09
ledgerloom reconcile --database ledgerloom.db --anchor-batch bank-2026-09 --batch reconciliation-2026-09
ledgerloom inspect --database ledgerloom.db
ledgerloom export --database ledgerloom.db --output audit.jsonl
```

Every command emits one canonical JSON result. Inspection and export open an
existing journal read-only, so they neither create a missing database nor repair
one that fails verification. See [Command-line workflows](docs/cli.md) for the
complete behavior and [Operations and recovery](docs/operations.md) before
automating backups or recovery checks.

## Development setup

LedgerLoom requires Python 3.12 or newer.

```shell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .[dev]
python -m pytest
```

The package uses a `src` layout. Public modules live below `src/ledgerloom`,
and the test tree mirrors that structure. See [Contributing](CONTRIBUTING.md) for
the complete lint, type-check, coverage, and build gates.

## Design principles

1. **Deterministic by default.** Equal inputs and configuration must produce
   byte-for-byte equivalent results.
2. **Exact domain values.** Monetary values use decimal arithmetic and explicit
   currencies; timestamps are timezone-aware.
3. **Explain every decision.** Matching and rejection results carry stable
   reason codes and contextual evidence.
4. **Keep boundaries replaceable.** Storage, clock, identifier generation, and
   import formats are defined behind narrow interfaces.
5. **Make corruption visible.** Journal integrity failures are reported instead
   of being silently repaired.

## Status

LedgerLoom is under active development, with the end-to-end local workflow now
implemented. Imported batches and reconciliation reports use canonical,
versioned documents in an append-only SQLite journal. Reopening verifies event
bytes, hash-chain links, and independently committed count/head metadata before
state is exposed. Read-only inspection captures rows and metadata in one SQLite
transaction and can compare them with externally retained count/head anchors.

Reconciliation is deterministic and explainable: candidate indexes, weighted
rules, ambiguity thresholds, conflict policies, and structured reports all use
stable ordering. Committed reports also reserve both sides of a matched pair, so
later CLI runs cannot silently reuse an already matched entry. A typed journal
catalog decodes supported application schemas, retains unrelated schema entries,
and fails closed on unsupported versions of known schemas.

Verified exports are written beside their destination and published atomically.
They do not overwrite an existing file unless explicitly requested, and the CLI
protects the database plus its SQLite sidecar files from being selected as an
export target. Remaining planned hardening work is tracked in
[the roadmap](docs/roadmap.md).

## License

LedgerLoom is available under the MIT License.
