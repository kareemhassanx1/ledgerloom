# Architecture

LedgerLoom is organized as a collection of inward-pointing layers. The domain
layer contains immutable values and invariants and imports nothing from storage,
file formats, or command-line concerns. Application services coordinate domain
operations. Adapter packages translate external data and persist results.

## Package boundaries

`ledgerloom.domain`
: Identifiers, monetary values, timestamps, source records, ledger entries, and
  reconciliation outcomes. Domain objects reject invalid state when created.

`ledgerloom.journal`
: Append-only event storage, canonical encoding, integrity chains, replay, and
  snapshots. The initial implementation is in memory; durable implementations
  preserve the same behavior.

`ledgerloom.rules`
: Match predicates, weighted evidence, composition, and deterministic ranking.
  Rules return explanations rather than a bare boolean.

`ledgerloom.reconciliation`
: Candidate selection, decision policies, conflict handling, and result reports.

`ledgerloom.ingest`
: Parsers and validation pipelines for external record formats. Import failures
  are data, not control-flow exceptions, so one bad row does not hide later rows.

`ledgerloom.storage`
: Replaceable persistence adapters. SQLite is the first durable implementation.

`ledgerloom.cli`
: A thin command layer over the public application services.

## Dependency rule

Dependencies point toward the domain. The domain package does not import from
any outer package. Cross-layer communication uses immutable values or protocols,
which keeps business behavior independently testable.

## Determinism

Ordering is explicit at every boundary. Canonical encoders sort mapping keys,
timestamps are normalized to UTC, and ranking uses stable tie breakers. Injected
clocks and identifier factories keep tests independent of wall-clock time.

