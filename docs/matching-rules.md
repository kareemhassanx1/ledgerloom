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

The first built-in rules establish safe candidate scope:

- `DifferentEntryRule` prevents an entry from matching itself.
- `SameAccountRule` requires a common normalized account.
- `DifferentSourceRule` limits matching to records from different upstream
  sources.

Amount, reference, and time-window comparison rules build on the same contract
in the next matching milestone.

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
from ledgerloom.rules import (
    AllOfRule,
    DifferentEntryRule,
    DifferentSourceRule,
    SameAccountRule,
    evaluate_rule,
)

policy = AllOfRule(
    "policy.cross_source_same_account",
    (
        DifferentEntryRule(),
        DifferentSourceRule(),
        SameAccountRule(),
    ),
)

evidence = evaluate_rule(policy, bank_entry, processor_entry)
if evidence.is_match:
    print("candidate is eligible for field comparison")
else:
    print([item.reason_code for item in evidence.failures])
```

Third-party rules can satisfy the runtime-checkable `MatchRule` protocol by
exposing a valid `rule_id` and an `evaluate(left, right)` method. Calling them
through `evaluate_rule` verifies operand types, the evidence result type, and
that the returned evidence belongs to the evaluated rule.
