# Matching rules


LedgerLoom match rules compare two normalized `LedgerEntry` values and return
`RuleEvidence`. A rule never returns a bare boolean. This makes a policy result
usable both by reconciliation code and by audit or reporting tools without
re-running hidden business logic.

## Verdicts and evidence

Every evidence item contains:

- a dotted, lowercase `rule_id` that identifies the configured rule;
- one of `match`, `no_match`, or `indeterminate`;
- a stable `reason_code` suitable for programmatic handling;
- optional named scalar facts; and
- ordered child evidence for composite rules.

Facts are sorted by name during construction. Composite children retain their
declared order. `RuleEvidence.walk()` therefore produces the same pre-order
audit trail for the same policy and inputs, while `RuleEvidence.failures`
selects every explicit rejection from that trail.

`indeterminate` is distinct from rejection. It lets later rules report missing
or unusable comparison data without silently treating uncertainty as either a
match or a mismatch.

## Built-in safeguards

Built-in scope rules establish safe candidate scope:

- `DifferentEntryRule` prevents an entry from matching itself.
- `SameAccountRule` requires a common normalized account.
- `DifferentSourceRule` limits matching to records from different upstream
  sources.

Field rules build on the same contract:

- `AmountRule` requires a common currency and accepts an exact decimal
  difference up to its configured non-negative tolerance. The tolerance
  boundary is inclusive and defaults to zero.
- `ReferenceRule` compares canonical reference text exactly. A missing reference
  on either side is `indeterminate`, not a rejection.
- `TimeWindowRule` accepts entries whose absolute occurrence-time difference is
  no greater than its configured non-negative `timedelta`. Both boundaries are
  inclusive.

## Candidate indexes

`CandidateIndex` snapshots entries into account, exact and tolerant amount
(including currency), present-reference, and occurrence-time indexes. Every
lookup returns entries ordered by `(occurred_at, entry_id)`, so input order and
hash iteration cannot change later policy behavior.

`within_time_window()` uses the same inclusive boundary as `TimeWindowRule`.
`candidates_for()` intersects account, same-currency amount-tolerance, and
time-window lookups, optionally requires an exact reference, and removes the
anchor entry. Missing references are not placed in the reference index because
they represent absent evidence rather than a shared value.

## Composition

`AllOfRule`, `AnyOfRule`, and `NotRule` use explicit three-valued logic:

| Composition | Match | No match | Indeterminate |
| --- | --- | --- | --- |
| `AllOfRule` | every child matches | any child rejects | otherwise, a child is indeterminate |
| `AnyOfRule` | any child matches | every child rejects | otherwise, a child is indeterminate |
| `NotRule` | child rejects | child matches | child is indeterminate |

All children of `AllOfRule` and `AnyOfRule` are evaluated even after the result
can be known. This deliberately favors a complete explanation over
short-circuit performance. Candidate indexes narrow the comparison set before
rules run, so policies do not need to hide skipped evidence to remain efficient.

Composite child identifiers must be unique, and a composite cannot directly
contain its own identifier. These constraints make each child addressable in a
stored explanation and reject accidental direct recursion at configuration
time.

## Example

```python
from datetime import timedelta

from ledgerloom.rules import (
    AllOfRule,
    AmountRule,
    DifferentEntryRule,
    DifferentSourceRule,
    ReferenceRule,
    SameAccountRule,
    TimeWindowRule,
    evaluate_rule,
)

policy = AllOfRule(
    "policy.cross_source_same_account",
    (
        DifferentEntryRule(),
        DifferentSourceRule(),
        SameAccountRule(),
        AmountRule(),
        ReferenceRule(),
        TimeWindowRule(timedelta(days=2)),
    ),
)

evidence = evaluate_rule(policy, bank_entry, processor_entry)
if evidence.is_match:
    print("candidate satisfies the configured policy")
else:
    print([item.reason_code for item in evidence.failures])
```

Third-party rules can satisfy the runtime-checkable `MatchRule` protocol by
exposing a valid `rule_id` and an `evaluate(left, right)` method. Calling them
through `evaluate_rule` verifies operand types, the evidence result type, and
that the returned evidence belongs to the evaluated rule.

See [Scoring and match decisions](scoring-decisions.md) for the integer weighting,
ambiguity, and cross-anchor conflict semantics built on this evidence.
