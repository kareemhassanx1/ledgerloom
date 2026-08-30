# Scoring and match decisions

Candidate indexes narrow the search space and matching rules explain individual
comparisons. The scoring layer combines that evidence into a deterministic
integer score, while decision and conflict policies decide whether a candidate
can be selected safely.

## Weighted rules

`ScoringRule` associates a `MatchRule` with a positive integer weight. A matched
rule earns its complete weight; a `no_match` or `indeterminate` result earns no
points. Integer arithmetic avoids platform-specific floating-point behavior.

A scoring rule can also be `required`. Every required rule must return `match`
before the candidate can qualify, independently of the points earned from other
rules. Required rejection and required indeterminacy have distinct reason codes
so callers can tell contradictory evidence from missing data.

`MatchScorer` evaluates every configured rule and retains its ordered
`RuleEvidence`. The resulting `CandidateScore` is bound to both the anchor and
candidate entries, preventing evidence from one comparison from being reused in
another decision. It exposes:

- the earned and maximum integer score;
- confidence as floor-rounded basis points from 0 through 10,000;
- all per-rule evidence in configuration order;
- matched rule identifiers; and
- the required-rule status and stable reason code.

Candidate rankings are independent of input order. They sort by descending
score, then occurrence instant, then entry identifier. The last two fields are
deterministic presentation tie breakers only; they never turn an equal score
into a match decision.

## Thresholds and ambiguity

`DecisionPolicy` filters out candidates that fail a required rule or fall below
`minimum_score`. If no candidate qualifies, the result is `unmatched` and still
retains the complete ranking.

The best qualified score must lead every other contender by at least
`minimum_margin`. A smaller lead produces `ambiguous`. An exact tie is always
ambiguous, even when the configured minimum margin is zero; chronological or ID
ordering never silently chooses between equal evidence.

Candidate scores in one decision must belong to the requested anchor and use
the same maximum score. This prevents comparisons across different rule-weight
scales.

## Cross-anchor conflicts

After per-anchor decisions, `ConflictPolicy` detects when multiple matched
anchors selected the same candidate. Two strategies are available:

- `reject_all` converts every competing selection to `conflict` and retains the
  disputed candidate for explanation;
- `prefer_higher_score` keeps a uniquely higher scoring selection and marks the
  others as conflicts.

If the highest scores are equal, `prefer_higher_score` rejects every competing
selection instead of using anchor order as a hidden business rule. Score
preference also requires a common maximum score, because raw values from
different scoring scales are not comparable. `reject_all` does not compare
scores and therefore does not need this restriction.

Conflict output is ordered by anchor occurrence instant and identifier,
regardless of the order in which decisions were supplied.

## Example

```python
from decimal import Decimal

from ledgerloom.reconciliation import (
    ConflictPolicy,
    DecisionPolicy,
    MatchScorer,
    ScoringRule,
)
from ledgerloom.rules import AmountRule, ReferenceRule, SameAccountRule

scorer = MatchScorer(
    (
        ScoringRule(SameAccountRule(), 20, required=True),
        ScoringRule(AmountRule(Decimal("0.01")), 50),
        ScoringRule(ReferenceRule(), 30),
    )
)
policy = DecisionPolicy(minimum_score=70, minimum_margin=10)

scores = scorer.rank(anchor, candidate_index.candidates_for(
    anchor,
    max_time_difference=time_window,
    max_amount_difference=Decimal("0.01"),
))
decision = policy.decide(anchor, scores)

resolved_decisions = ConflictPolicy().resolve(all_anchor_decisions)
```

[Reconciliation batches and reports](reconciliation-batches.md) consume these
immutable decisions without re-running or reinterpreting matching rules.
