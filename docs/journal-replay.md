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

## Checkpointing projections

`ProjectionRunner` adds named, resumable projections on top of verified replay.
A `ProjectionCheckpoint` contains the projected state and the `ReplayCursor` that
identifies the exact journal prefix used to produce it:

```python
from ledgerloom.journal import Projection, ProjectionRunner

projection = Projection(
    "ledgerloom.record_ids",
    (),
    lambda state, event: (*state, str(event.value.record_id)),
)
runner = ProjectionRunner(registry)
checkpoint = runner.project(first_page, projection=projection)
snapshot = runner.snapshot(checkpoint)
```

Snapshot state must have a codec in the same schema registry. The snapshot is a
canonical `ledgerloom.projection_snapshot@1.0` document containing the projection
name, replay cursor, and complete versioned state document. Persist both
`snapshot.content` and `snapshot.fingerprint`; the independent fingerprint lets
recovery detect replacement or truncation of the outer snapshot, while the
embedded state fingerprint protects the state document itself.

Recovery restores the checkpoint and verifies the next segment against its
fingerprint anchor before invoking the reducer:

```python
recovered = runner.recover(
    next_page,
    projection=projection,
    snapshot=stored_snapshot_bytes,
    expected_fingerprint=stored_snapshot_fingerprint,
)
```

The next page must start at `checkpoint.cursor.sequence + 1`. A page copied from
another history, a damaged snapshot, an unknown state schema, or a snapshot for
another projection fails before any new event is applied. Reducers and state
codecs determine semantic determinism, so they should avoid clocks, random values,
unordered external input, and side effects.
