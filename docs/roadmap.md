# Roadmap

The roadmap follows dependency order so that each milestone leaves a useful,
tested library surface.

## Domain foundation

- [x] Validated identifiers and account references.
- [x] Exact monetary values and arithmetic.
- [x] Timezone-aware occurrence timestamps.
- [x] Immutable source records, entries, batches, and validation results.

## Verifiable history

- [x] Canonical JSON encoding and stable fingerprints.
- [x] Append-only event journals with hash-chain integrity and bounded reads.
- [x] Fingerprint-anchored replay and structured corruption diagnostics.
- Replayable projections, checkpoints, and snapshots.
- [x] Recovery diagnostics for truncated or corrupted journals.

## Reconciliation

- Composable match rules and structured evidence.
- Candidate indexes for account, amount, reference, and time windows.
- Deterministic scoring, ambiguity detection, and conflict policies.
- Explainable batch results and summary reports.

## Boundaries

- Streaming CSV and JSON Lines importers.
- Schema mapping and row-level validation diagnostics.
- SQLite storage with transactional batch writes.
- Command-line workflows for import, reconcile, inspect, and export.

## Hardening

- End-to-end fixtures covering partial failure and restart behavior.
- Property-style invariant tests and performance benchmarks.
- API reference, tutorials, and operational guidance.
