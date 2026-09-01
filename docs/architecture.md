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
configurable hard limit caps cursor-based reads. The SQLite adapter preserves
those semantics across process restarts. It verifies stored documents, the full
chain, and independently committed count and head metadata before every read or
write instead of skipping damaged entries.

SQLite multi-document appends run inside one immediate transaction. The write
lock is acquired before the current head is read, so the expected-sequence check,
entry inserts, and metadata update observe one state and commit or roll back
together. Separate adapter instances therefore serialize writers through SQLite;
a writer carrying a stale expected sequence receives the same conflict error as
the in-memory journal.

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

Candidate indexes snapshot an entry set and build account, exact and tolerant
amount, present-reference, and occurrence-time lookups. Lookups and intersections
always return entries ordered by occurrence instant and then entry identifier,
independently of ingestion order. Centered time windows include both boundaries
and clamp at the supported datetime limits. `candidates_for()` intersects
same-account, same-currency amount-tolerance, and time-window indexes, optionally
requires an exact reference, and excludes the anchor entry.

Weighted scoring uses bounded positive integers and retains every rule's
evidence in configuration order. Score cards bind evidence to both comparison
entries, while required rules can disqualify a candidate independently of its
points. Rankings use descending score followed by the canonical occurrence and
entry-identifier order.

Decision policies apply a minimum score and score margin. Exact ties remain
ambiguous instead of being resolved by presentation tie breakers. Cross-anchor
conflict policies either reject every reuse of one candidate or retain a
uniquely higher scoring selection; equal leaders remain conflicts. Score-based
conflict resolution rejects mixed maximum-score scales rather than comparing
unlike values.

Batch reconciliation connects the candidate index, scorer, decision policy, and
conflict policy without hiding their intermediate results. A batch owns a typed
identifier and a canonical, conflict-free decision sequence. Structured reports
remove full entry objects while retaining identifiers, scores, reason codes, and
rule evidence. Summary counts and basis-point coverage are recomputed and
verified against report decisions, so an inconsistent aggregate cannot be
published through the public model. The optional text renderer omits evidence
facts such as references and amounts.

Input-aware reconciliation reports join those decisions back to the ordered
`LedgerBatch` validation results. They cover accepted and rejected positions in
one immutable view, classify conflict decisions as operationally ambiguous while
retaining the detailed conflict status, and explain invalid inputs with safe
diagnostics that omit rejected values. Exact anchor-set and anchor-content checks
prevent an input batch from being paired with unrelated reconciliation output.

`ledgerloom.ingest`
: Parsers and validation pipelines for external record formats. Import failures
  are data, not control-flow exceptions, so one bad row does not hide later rows.

CSV and JSON Lines adapters share a configurable `SourceRecordMapper`, return
lazy `ImportRowResult` sequences, and retain logical record plus physical line
locations without retaining raw values. CSV header and row policies make BOM,
extra-column, blank-row, width, and field-size behavior explicit. JSON Lines
rejects malformed values, non-object roots, duplicate keys, non-standard numeric
constants, and oversized lines independently. A composable source-key filter
rejects later duplicates, while a terminal summary aggregates outcome and
diagnostic counts without materializing the imported records.

`ledgerloom.storage`
: Replaceable persistence adapters. `SqliteDatabase` owns versioned connections
  and explicit transaction boundaries. `SQLiteJournal` stores canonical event
  bytes, hash-chain links, and recovery metadata in a WAL-backed database. A
  reopened adapter verifies the committed history before exposing it.

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
set iteration order. Candidate index buckets use the same canonical
occurrence-time and entry-identifier ordering, including when multiple entries
share an instant. Score ranking adds descending integer score before that entry
order, but decision policies explicitly detect tied evidence before selection.

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
