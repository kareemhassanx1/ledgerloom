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
- [x] Replayable projections, checkpoints, and snapshots.
- [x] Recovery diagnostics for truncated or corrupted journals.

## Reconciliation

- [x] Composable match rules and structured evidence.
- [x] Candidate indexes for account, amount, reference, and time windows.
- [x] Deterministic scoring, ambiguity detection, and conflict policies.
- [x] Explainable batch results and summary reports.

## Boundaries

- [x] Streaming CSV and JSON Lines importers.
- [x] Schema mapping and row-level validation diagnostics.
- [x] SQLite storage with transactional writes and explicit open modes.
- [x] Command-line workflows for import, reconcile, inspect, and export.
- [x] Atomic verified exports with protected storage destinations.
- [x] External count/head recovery anchors for consistent inspection snapshots.

## Hardening

- [x] End-to-end fixtures covering partial import and restart behavior.
- [x] Fail-closed application schema discovery and cross-run match claims.
- [x] Linux CI gates for linting, source type checks, coverage, and wheel builds.
- [x] Backup, inspection, export, and recovery guidance.
- [ ] Property-style invariant tests.
- [ ] Repeatable performance benchmarks for large journals and imports.
- [ ] Complete public API reference and task-focused tutorials.
