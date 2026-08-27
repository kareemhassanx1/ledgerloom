# Architecture

LedgerLoom is organized as a collection of inward-pointing layers. The domain
layer contains immutable values and invariants and imports nothing from storage,
file formats, or command-line concerns. Application services coordinate domain
operations. Adapter packages translate external data and persist results.

## Package boundaries

`ledgerloom.domain`
: Identifiers, monetary values, timestamps, source records, ledger entries, and
  reconciliation outcomes. Domain objects reject invalid state when created.

Source records retain the upstream source and source-scoped record identifier.
Normalized ledger entries copy that provenance so batches can reject duplicate
input without inspecting mutable adapter state. A ledger batch contains one
ordered `ValidationResult` per submitted item: successful items carry an entry
and optional warnings, while rejected items carry one or more error diagnostics.
Flattened batch diagnostics include stable one-based input positions.

Boundary diagnostics contain a stable code, field path, message, and severity.
They never retain the rejected value held by a `DomainValidationError`, which
makes them safe to persist or log without accidentally copying source data.

`ledgerloom.journal`
: Append-only event storage, canonical encoding, integrity chains, replay, and
  snapshots. The initial implementation is in memory; durable implementations
  preserve the same behavior.

Journal entries assign contiguous one-based sequences and retain immutable
schema documents. Each entry fingerprint covers its sequence, event schema and
content fingerprint, and the preceding entry fingerprint. The in-memory adapter
verifies the complete chain before returning events or accepting an append. An
optimistic expected-sequence guard coordinates competing writers, while a
configurable hard limit caps cursor-based reads. A durable adapter must preserve
those observable semantics and surface integrity failures instead of skipping
damaged entries.

Integrity inspection reports the verified prefix and first broken sequence
without requiring a successful read. Independently stored count and head values
make a truncated but internally consistent prefix detectable. Replay accepts
genesis or fingerprint-anchored segments, verifies and decodes the complete
segment before invoking a reducer, and returns a cursor for the next segment.
This keeps corruption and unknown schema versions from partially changing a
projection.

Named projections combine a genesis state with a reducer and produce immutable
checkpoints whose replay cursor binds the state to an exact journal history.
Snapshots encode checkpoint state through the schema registry, embed the complete
canonical state document and its fingerprint, and wrap it in the versioned
`ledgerloom.projection_snapshot@1.0` contract. Recovery validates the snapshot,
projection identity, state schema, and next journal link before applying new
events. Equal checkpoint state and schema versions therefore produce identical
snapshot bytes and fingerprints.

`ledgerloom.rules`
: Match contracts, structured evidence, composition, and deterministic ranking.
  Rules return explanations rather than a bare boolean.

Rule evidence records a three-valued verdict, stable rule and reason codes,
canonical scalar facts, and ordered child explanations. Built-in scope rules
prevent self-matches and compare account or source identity. Composite rules
apply explicit `all`, `any`, and `not` semantics without short-circuiting, so an
audit receives evidence from every configured child in declaration order.
Custom implementations pass through a guarded protocol boundary that verifies
operand types, result types, and evidence ownership.

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
Matching evidence sorts facts by name and preserves policy child order, which
makes traversal and failure extraction repeatable without relying on mapping or
set iteration order.

## Serialized contracts

Persistence boundaries use canonical UTF-8 JSON: object keys are sorted,
insignificant whitespace is removed, integers stay within the interoperable
JSON range, and binary floating-point values are rejected. Exact amounts and
timestamps are represented by canonical text owned by their domain values.

Every document has a dotted schema name, a comparable `major.minor` version,
and one payload. A SHA-256 fingerprint covers the complete canonical envelope,
including its schema metadata. Registries select the latest registered codec
for a Python type by default while retaining explicit access to older versions.

The first contract set covers source records, ledger entries, and ordered
ledger batches. Batch payloads preserve accepted entries, rejected positions,
warnings, and errors so a serialize/deserialize round trip cannot hide partial
failure. Decoding rebuilds the domain objects and therefore rechecks all domain
invariants rather than trusting the serialized data.
