# Reconciliation batches and reports

LedgerLoom can run candidate selection, scoring, ambiguity decisions, and
cross-anchor conflict resolution as one deterministic batch. The output remains
immutable and contains the evidence needed to explain every final status.

## Batch workflow

`BatchReconciler` combines four existing policies:

1. `CandidateSelectionPolicy` narrows an immutable `CandidateIndex` by account,
   currency, amount tolerance, time window, and optional reference equality.
2. `MatchScorer` evaluates every configured weighted rule for each candidate.
3. `DecisionPolicy` applies the required-rule, score-threshold, and score-margin
   conditions for each anchor.
4. `ConflictPolicy` prevents independently matched anchors from silently
   claiming the same candidate.

The workflow accepts a typed `BatchId`, one or more unique anchor entries, and
one candidate-index snapshot. Anchor input order does not affect output: the
batch orders decisions by anchor occurrence instant and then entry identifier.
Candidate ordering is already deterministic within the index and scorer. Every
anchor identifier is removed from candidate results before scoring, even when
the candidate index contains the anchor set, so an entry cannot occupy both
roles in one batch.

The result is a `ReconciliationBatch`. It rejects duplicate anchors and any
remaining duplicate selected candidate, so constructing a successful batch can
never bypass conflict resolution. It also rejects a claimed candidate whose
identifier belongs to any batch anchor as a defensive invariant.
`decision_for()` performs typed anchor lookup, and `matched_pairs` exposes
conflict-free identifier pairs in batch order.

Every reconciler-created batch carries a `ReconciliationPolicySnapshot`. It
records rule identifiers, integer weights and required flags; score and margin
thresholds; exact time and amount tolerances; reference selection; and the
conflict strategy. The snapshot contains configuration only, not ledger values,
and lets reports explain which policy produced their decisions.

A conflict must have at least two claims on its disputed candidate: another
conflict or a uniquely winning matched decision. Orphan conflict objects are
rejected rather than published without a real competing party.

## Claims across committed runs

The conflict policy above resolves collisions inside one batch.
`ReconciliationClaims` applies the same one-to-one constraint to reports already
committed in a journal. `from_reports()` derives an immutable index from every
matched pair and records both the anchor and selected candidate as participants.

Before a later run, `require_available_anchors()` rejects an anchor that already
participates in a committed match. After the ordinary batch workflow produces a
report, `resolve()` changes a selection of an already claimed candidate into a
conflict with reason `conflict.candidate_already_claimed`. The decision retains
the disputed candidate as a contender and names the earlier anchor or anchors as
competitors. Its selected candidate is cleared and the report summary is
recomputed from the changed decisions.

The CLI rebuilds this claim index from the journal catalog for every
reconciliation command. Claims therefore survive process exits and database
reopens; they are not an implicit in-memory lock.

## Structured report

`build_reconciliation_report()` snapshots a verified batch without performing
selection, scoring, or decision logic again. The report contains no full ledger
entry objects. It retains identifiers, scores, status and reason codes, plus the
structured `RuleEvidence` that already explains each comparison.

`ReconciliationSummary` contains exact counts for:

- total anchors;
- matched, unmatched, ambiguous, and conflict outcomes;
- evaluated candidates; and
- matched coverage in integer basis points.

The four status counts must sum to the total. Coverage must equal the
floor-rounded matched share on a 0 through 10,000 scale. Report construction
rechecks these aggregates against the decision reports, rejects duplicate
anchors, and rejects reuse of a selected candidate.

Each reconciler-created decision also retains privacy-safe selection counts for
the indexed population, account, amount, time and reference filters, batch-anchor
exclusions, and final evaluated set. These counts explain an empty ranking
without copying rejected ledger values into the report.

`decision_reason_counts` groups final decision reasons into lexically ordered
`ReasonCount` values. `decisions_with_status()` and `decisions_with_reason()`
return matching items without changing batch order. These structured queries
let operational code distinguish candidate scarcity, ambiguity, and conflicts
without parsing rendered text.

Each `DecisionReport` records the final status, stable reason, selected candidate
when matched, ordered contenders, competing anchor identifiers, and every ranked
`CandidateReport`. A rejected conflict names its peers; when score preference
keeps a unique winner, that matched winner also names the anchors it displaced.
Candidate
reports preserve integer score, maximum score, exact basis-point confidence,
required-rule identifiers and their failed or indeterminate subsets,
required-rule status, score reason, and rule evidence. The `reason_codes`
properties flatten nested explanations in deterministic order without silently
dropping a failed or indeterminate rule.

## Text inspection

`ReconciliationReport.render_text()` provides a concise deterministic view for
logs and manual inspection. It prints only identifiers, status and reason codes,
scores, eligibility, contenders, and summary counts. It does not print monetary
amounts, references, or the scalar facts inside rule evidence. Applications can
use the structured report directly or apply a different presentation policy.
When present, the batch's policy snapshot is rendered before individual
decisions so an inspected log includes its decision context.

## Input-aware reports

`build_input_reconciliation_report()` joins a source `LedgerBatch` to the
`ReconciliationBatch` produced for exactly its accepted entries. The resulting
`InputReconciliationReport` retains every original one-based input position,
including rejected inputs that could never become reconciliation anchors.

Its operational summary distinguishes the four complete input outcomes:

- `matched` for a unique accepted match;
- `unmatched` when no candidate qualifies;
- `ambiguous` when multiple candidates remain or anchors conflict over one
  candidate; and
- `invalid` when input validation produced no ledger entry.

Conflict remains available as the nested decision status and keeps its reason,
contender, and competing-anchor details; grouping it under `ambiguous` only
provides the four-way operational view. Invalid outcomes retain safe structured
diagnostics but never a rejected source value. Accepted warnings are also kept
beside the matching decision. An all-invalid input batch can therefore produce
a complete report without fabricating an empty reconciliation batch.

The builder rejects missing, extra, or changed anchors instead of joining two
unrelated batches by position. `render_text()` emits identifiers, positions,
statuses, diagnostic codes, and field names, while omitting rejected values and
rule-evidence facts.

## Canonical report documents

`build_reconciliation_report_document()` encodes the complete structured report
under the versioned `ledgerloom.reconciliation_report@1.0` schema. The resulting
`SchemaDocument` contains canonical UTF-8 JSON and its SHA-256 content
fingerprint. Unlike the privacy-conscious text rendering, this machine-readable
document includes evidence facts and should be handled as reconciliation data.

`parse_reconciliation_report_document()` verifies canonical encoding, the schema
identifier, an optional expected fingerprint, and every report invariant before
returning typed immutable models. Changed content, an unexpected schema, or
internally inconsistent counts fail instead of producing a partially trusted
report.

The CLI appends this document to the same journal as imported batches. On a
later run, the typed application catalog decodes committed report documents in
journal order so duplicate batch identifiers and cross-run claims can be checked
before a new report is appended. An unsupported future version of the known
report schema fails closed rather than being ignored.

## Example

```python
from datetime import timedelta
from decimal import Decimal

from ledgerloom.domain.identifiers import BatchId
from ledgerloom.reconciliation import (
    BatchReconciler,
    CandidateIndex,
    CandidateSelectionPolicy,
    ConflictPolicy,
    DecisionPolicy,
    MatchScorer,
    ScoringRule,
    build_reconciliation_report,
)
from ledgerloom.rules import AmountRule, ReferenceRule, SameAccountRule

reconciler = BatchReconciler(
    scorer=MatchScorer(
        (
            ScoringRule(SameAccountRule(), 20, required=True),
            ScoringRule(AmountRule(Decimal("0.01")), 50),
            ScoringRule(ReferenceRule(), 30),
        )
    ),
    decision_policy=DecisionPolicy(minimum_score=70, minimum_margin=10),
    selection_policy=CandidateSelectionPolicy(
        max_time_difference=timedelta(days=1),
        max_amount_difference=Decimal("0.01"),
    ),
    conflict_policy=ConflictPolicy(),
)

batch = reconciler.reconcile(
    BatchId("reconcile-2026-08-30"),
    anchors,
    CandidateIndex(candidate_entries),
)
report = build_reconciliation_report(batch)

print(report.summary.matched)
print(report.render_text())
```

For operational examples that persist and inspect these reports, see
[Command-line workflows](cli.md). The lower-level batch APIs remain usable
without SQLite when an application wants to own persistence itself.
