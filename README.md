# LedgerLoom

LedgerLoom is a deterministic, local-first reconciliation engine for Python.
It turns records from unrelated financial systems into a consistent ledger,
matches related entries with explainable rules, and preserves an auditable
trail of every decision.

The project is designed for teams that need reconciliation behavior they can
run locally and reproduce later. Network access is never required by the core
engine, decimal amounts remain exact, and every state transition is represented
as immutable domain data.

## Planned capabilities

- Strict value objects for identifiers, money, timestamps, source records, and ledger entries.
- Ordered ledger batches that preserve accepted entries and structured rejection diagnostics.
- Canonical, versioned serialization and SHA-256 content fingerprints.
- Append-only journals with integrity reports, resumable replay, and snapshots.
- Configurable exact, tolerance, date-window, and composite match rules.
- Explainable reconciliation decisions and conflict resolution.
- Streaming CSV and JSON Lines ingestion with structured validation errors.
- SQLite journal persistence and a command-line interface for repeatable batch runs.
- Reports that distinguish matched, unmatched, ambiguous, and invalid records.

## Development setup

LedgerLoom requires Python 3.12 or newer.

```shell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .[dev]
python -m pytest
```

The package uses a `src` layout. Public modules live below `src/ledgerloom`,
and the test tree mirrors that structure.

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

LedgerLoom is under active development. The current foundation provides immutable
financial values, provenance-carrying records and batches, versioned canonical
documents, and an in-memory append-only journal with sequence guards, bounded
reads, truncation diagnostics, fingerprint-anchored replay, and named projections
with deterministic resumable snapshots. It also provides structured three-valued
matching evidence, guarded rule contracts, entry-scope safeguards, and complete
`all`/`any`/`not` policy composition. Immutable candidate indexes narrow entries
by account, exact or tolerant amount, reference, and inclusive time windows,
while amount, reference, and time-window rules explain field-level matches.
Integer-only weighted scoring retains every explanation, explicit margins keep
tied or close candidates ambiguous, and conflict policies prevent two anchors
from silently claiming the same entry. Deterministic batch workflows now connect
those stages and produce verified summaries, structured per-candidate evidence,
and privacy-conscious text reports. Input-aware reports join those decisions to
ordered validation results, covering matched, unmatched, ambiguous, and invalid
records without retaining rejected values. Streaming CSV and JSON Lines adapters
map configurable schemas into source records,
continue after row-level failures, enforce resource limits, detect duplicate
source identities, and produce deterministic operational summaries. The next
release now includes durable SQLite journals with atomic multi-event appends,
verified reopen behavior, recovery metadata, and optimistic concurrency guards.
The next release adds the CLI.

## License

LedgerLoom is available under the MIT License.
