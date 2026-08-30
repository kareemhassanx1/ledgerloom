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

A conflict must have at least two claims on its disputed candidate: another
conflict or a uniquely winning matched decision. Orphan conflict objects are
rejected rather than published without a real competing party.

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
amounts, references, or the scalar facts inside rule evidence. A future CLI can
use the structured report directly or apply a different presentation policy.

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

Batch reports currently remain in memory. Later storage and export adapters can
serialize their stable public fields without changing reconciliation behavior.
