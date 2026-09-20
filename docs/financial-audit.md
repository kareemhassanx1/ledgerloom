# Financial audit and verified backup

`ledgerloom audit` builds deterministic JSON from one verified read-only journal
snapshot. All views share the event count and head fingerprint. It does not
append decisions or declare the financial books correct.

```shell
ledgerloom audit --database ledgerloom.db --as-of 2026-09-16T00:00:00Z
ledgerloom audit --database ledgerloom.db --as-of 2026-09-16T00:00:00Z --period-start 2026-09-01T00:00:00Z --period-end 2026-10-01T00:00:00Z
ledgerloom audit --database ledgerloom.db --as-of 2026-09-16T00:00:00Z --control bank cash USD 100 2500 100
ledgerloom audit --database ledgerloom.db --as-of 2026-09-16T00:00:00Z --compare-reports review-before review-after
```

Each repeated `--control` takes source, account, currency, accepted-row count,
gross inflow and positive gross outflow. Supply independent statement totals;
controls generated from the same imported rows provide no completeness check.
Exit 0 means analysis ran; inspect `statement_controls.passed` for the result.
Omitted controls yield null, not a pass.

Public functions below `ledgerloom`:

| Function | Contract |
| --- | --- |
| `analysis.balances.source_balances` | Exact inflow, outflow and net per source/account/currency; reject duplicated identities. |
| `analysis.control_totals.check_statement_controls` | Compare independent `StatementControl` values, including offsetting omissions hidden by net totals. |
| `analysis.period_balances.period_balances` | Observed opening, movement and closing over a `TimeRange`, inclusive start and exclusive end. |
| `analysis.open_items.open_items` | Unclaimed imports, last review reason, and explicit future/0–7/8–30/31–90/91+ day age buckets. |
| `analysis.settlements.settlement_variances` | Amount differences and source/account/currency exceptions for committed matches. |
| `analysis.source_coverage.source_coverage` | Input counts, rejection diagnostics and observed date spans, including all-rejected imports. |
| `analysis.report_changes.report_changes` | Added/removed/changed decision evidence and policy changes between retained reports. |
| `application.audit_pack.build_audit_pack` | Verify and compose views, retain unknown-schema disclosures and fingerprint the result. |
| `storage.backup.backup_journal` | Restorable online SQLite backup with verified exclusive publication. |

Amounts are strings. Arithmetic uses exact integer units, independent of ambient
Decimal precision, accepting at most 80 integer and 18 decimal places per input.
Sources and currencies remain separate. Inflow/outflow means positive/negative
imported amounts, not debit/credit accounting semantics. Opening balances cover
only earlier imported entries, not a certified bank opening balance.

`--as-of` dates source ages, not journal commitments: reports have no historical
commitment timestamp, so matches describe the current snapshot. Future records
remain visible. Unknown schemas are disclosed and excluded from financial totals.
A fingerprint supports comparison with retained evidence, not authenticity.

## Journal-bound close evidence

```shell
ledgerloom audit --database ledgerloom.db --as-of 2026-09-20T09:00:00Z --close-evidence --review-due-at 2026-09-21T09:00:00Z --evidence-actor reviewer-1 --review-capacity 20 --review-currency USD --control bank cash USD 100 2500 100
```

This opt-in form returns `result.audit` (the unchanged, fingerprinted audit pack)
and `result.close_evidence`. Exit 0 still means the analysis completed; read
`close_evidence.assessment.disposition` before acting on its findings.
The fixed `strict-full-reconciliation-v1` policy accepts only a nonempty imported
ledger with complete independent statement controls, no open entries, no rejected
import rows, no future-dated source entries, no unknown events and no settlement
discrepancies. Even a matched pair with an amount variance holds the assessment.
This conservative policy evaluates the whole current journal, regardless of any
period filters supplied for the separate audit views. It cannot certify facts
absent from the journal or prove that the caller obtained controls independently.

The review plan carries each open entry's source record, account, exact amount,
currency and latest committed decision reason. Both participants of a committed
match are excluded. Conflicts precede ambiguities, unmatched and unreviewed rows;
equal priorities use entry identity for stable ordering. Each entry consumes one
whole review slot. Windows must support its `currency:USD`-style capability, and
no allocation starts before `as_of` or the source occurrence. Future entries at
or beyond the deadline and capacity shortfalls remain explicitly unscheduled.
Omit both capacity and currency options to record an entirely unscheduled desk.
The CLI declares one window from `as_of` to the deadline; the library supports
multiple windows. Capacity is a caller-supplied slot budget, not a reviewer roster.

The public APIs are `assurance.risk.assess_journal_close`,
`assurance.planning.plan_journal_reviews`, and
`assurance.evidence.build_close_evidence` / `verify_close_evidence`.
They verify canonical journal history before measuring or planning. Evidence
seals the exact journal head/count, financial audit, independent control values,
policy, review inputs and computed allocations. Verification recomputes against
the caller's trusted journal, timestamp, deadline, controls, windows and actor;
an altered or entirely rehashed receipt does not validate against those inputs.
Invalid trusted inputs raise validation errors. Receipts are deterministic JSON
data and do not authenticate the actor: the fingerprint is not a signature.
These APIs preserve the earlier generic helpers for compatibility.

Planning and technical acceptance never append a financial decision or claim
human review, authorization or approval. `as_of` is an explicit source-age and
planning timestamp, not a reconstruction of historical journal commitments.

## Backup and reopen

```shell
ledgerloom backup --database ledgerloom.db --output archive/ledgerloom.db
ledgerloom inspect --database archive/ledgerloom.db --expected-count 42 --expected-head sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

SQLite's online backup includes committed WAL data. The copied snapshot passes
storage and application verification before publication. Optional expected
count/head arguments compare it with independently retained anchors. Keep the
returned anchors separately; later source writes do not invalidate the copy.

The parent directory must exist. Existing outputs, source files, sidecars and
hard links are refused, including destinations created during copying. Failed
copies are not published and temporary files are removed. Backup stops after a
30-second deadline. Reopen the separate copy using normal SQLiteJournal OPEN
mode; no in-place restore or repair is performed.

## Offline container verification

The Docker image contains Python and pinned dependencies, not application source
or tests. CI supplies the exact checkout, disables networking, installs/builds
offline and runs the full suite, including backup/reopen and financial auditing.
This verifies remote Docker without changing local Docker, WSL or security.
