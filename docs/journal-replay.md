# Journal integrity and replay

LedgerLoom verifies journal history before allowing it to influence projected
state. The journal's chain fingerprint covers each entry's global sequence,
schema reference, event fingerprint, and predecessor fingerprint. Changing an
event, moving an entry, removing a middle entry, or joining an unrelated history
therefore breaks verification.

## Inspecting integrity

`InMemoryJournal.verify()` provides a binary check and raises
`JournalIntegrityError` on the first failure. `integrity_report()` is the
diagnostic form and remains usable when normal reads are intentionally blocked:

```python
report = journal.integrity_report(
    expected_count=stored_entry_count,
    expected_head=stored_head_fingerprint,
)

if not report.is_valid:
    issue = report.issues[0]
    print(issue.code, issue.sequence, issue.path)
```

An internally valid prefix cannot reveal that its tail was deleted by itself.
Supplying the independently stored count or head makes that truncation or stale
copy observable. The report includes the number of entries seen, the verified
prefix length, and the last trusted fingerprint. Inspection stops at the first
broken link to avoid a cascade of misleading secondary errors.

`inspect_journal_entries()` also verifies a segment. A segment after genesis
must declare both its global starting sequence and the preceding chain
fingerprint.

## Replaying events

A `JournalReplayer` binds replay to a `SchemaRegistry`. It verifies the entire
segment and decodes every schema document before invoking the reducer:

```python
from ledgerloom.journal import JournalReplayer
from ledgerloom.serialization.domain_v1 import domain_v1_registry

replayer = JournalReplayer(domain_v1_registry())

result = replayer.replay(
    journal.read(limit=100),
    initial_state=(),
    reducer=lambda state, event: (*state, event.value),
)
```

The preflight means a damaged final link, unknown schema, or invalid payload
cannot cause a partially applied projection. Reducer exceptions remain ordinary
application errors and are not mislabeled as journal corruption.

The returned `ReplayCursor` contains the last applied global sequence and its
chain fingerprint. Pass it with the next contiguous segment to resume. A cursor
with the correct sequence but the wrong fingerprint is rejected before the
reducer runs, preventing a projection from continuing on another history.

`DecodedJournalEvent` exposes the decoded domain value, schema reference, event
fingerprint, chain fingerprint, and sequence. Reducers should depend on these
public values and remain free of external side effects where possible.

