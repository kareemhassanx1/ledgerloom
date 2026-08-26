from dataclasses import FrozenInstanceError

import pytest

from ledgerloom.domain.identifiers import AccountId, SourceId, SourceRecordId
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import SourceRecord
from ledgerloom.domain.time import UtcTimestamp
from ledgerloom.journal import (
    PROJECTION_SNAPSHOT_SCHEMA,
    DecodedJournalEvent,
    InMemoryJournal,
    JournalIntegrityError,
    Projection,
    ProjectionCheckpoint,
    ProjectionError,
    ProjectionRunner,
    ProjectionSnapshot,
    ProjectionSnapshotError,
    ReplayCursor,
)
from ledgerloom.serialization.canonical import canonical_json_bytes, loads_canonical_json
from ledgerloom.serialization.domain_v1 import domain_v1_registry
from ledgerloom.serialization.errors import SerializationError
from ledgerloom.serialization.schema import FunctionSchemaCodec, SchemaDocument, SchemaRef


def record(record_id: str) -> SourceRecord:
    return SourceRecord(
        SourceId("bank-primary"),
        SourceRecordId(record_id),
        AccountId("assets:checking"),
        Money.parse("12.50", "USD"),
        UtcTimestamp.parse("2026-08-26T16:45:00+03:00"),
        f"invoice-{record_id}",
    )


def journal_with_records(*record_ids: str) -> InMemoryJournal:
    registry = domain_v1_registry()
    journal = InMemoryJournal(max_read_limit=100)
    for record_id in record_ids:
        journal.append(registry.encode(record(record_id)))
    return journal


def decode_id_tuple(payload):
    if not isinstance(payload, list) or any(not isinstance(value, str) for value in payload):
        raise SerializationError(
            code="test.ids_payload",
            path="$.payload",
            message="ids must be a list of strings",
        )
    return tuple(payload)


def projection_registry():
    registry = domain_v1_registry()
    registry.register(
        FunctionSchemaCodec(
            ref=SchemaRef.parse("tests.record_ids", "1.0"),
            python_type=tuple,
            encoder=list,
            decoder=decode_id_tuple,
        )
    )
    return registry


def collect_record_ids(
    state: tuple[str, ...],
    event: DecodedJournalEvent,
) -> tuple[str, ...]:
    assert isinstance(event.value, SourceRecord)
    return (*state, str(event.value.record_id))


def record_ids_projection() -> Projection[tuple[str, ...]]:
    return Projection("ledgerloom.record_ids", (), collect_record_ids)


def test_snapshot_recovery_matches_uninterrupted_replay():
    journal = journal_with_records("txn-1", "txn-2", "txn-3", "txn-4")
    entries = journal.read(limit=4)
    runner = ProjectionRunner(projection_registry())
    projection = record_ids_projection()

    uninterrupted = runner.project(entries, projection=projection)
    checkpoint = runner.project(entries[:2], projection=projection)
    snapshot = runner.snapshot(checkpoint)
    restored = ProjectionSnapshot.parse(
        snapshot.content,
        expected_fingerprint=snapshot.fingerprint,
    )
    recovered = runner.recover(entries[2:], projection=projection, snapshot=restored)

    assert recovered == uninterrupted
    assert recovered.state == ("txn-1", "txn-2", "txn-3", "txn-4")
    assert recovered.cursor.sequence == 4
    assert recovered.cursor.fingerprint == journal.head_fingerprint


def test_equal_checkpoints_produce_byte_identical_snapshots():
    journal = journal_with_records("txn-1", "txn-2")
    runner = ProjectionRunner(projection_registry())
    checkpoint = runner.project(journal.read(), projection=record_ids_projection())

    first = runner.snapshot(checkpoint)
    second = runner.snapshot(checkpoint)

    assert first.content == second.content
    assert first.fingerprint == second.fingerprint
    assert first.document.schema == PROJECTION_SNAPSHOT_SCHEMA
    assert first.state_document.schema == SchemaRef.parse("tests.record_ids", "1.0")


def test_genesis_checkpoint_round_trips_without_a_chain_fingerprint():
    runner = ProjectionRunner(projection_registry())
    projection = record_ids_projection()
    checkpoint = runner.project((), projection=projection)

    restored = runner.restore(runner.snapshot(checkpoint).content, projection=projection)

    assert restored == ProjectionCheckpoint(
        "ledgerloom.record_ids",
        (),
        ReplayCursor.genesis(),
    )


def test_recovery_rejects_tail_from_another_history_before_reducing():
    original = journal_with_records("txn-1", "txn-2")
    unrelated = journal_with_records("different-1", "txn-2")
    runner = ProjectionRunner(projection_registry())
    calls = 0

    def counting_reducer(state, event):
        nonlocal calls
        calls += 1
        return collect_record_ids(state, event)

    projection = Projection("ledgerloom.record_ids", (), counting_reducer)
    checkpoint = runner.project(original.read(limit=2)[:1], projection=projection)
    snapshot = runner.snapshot(checkpoint)
    calls = 0

    with pytest.raises(JournalIntegrityError) as captured:
        runner.recover(
            unrelated.read(limit=2)[1:],
            projection=projection,
            snapshot=snapshot,
        )

    assert captured.value.code == "journal.chain_mismatch"
    assert calls == 0


def test_expected_fingerprint_rejects_modified_snapshot():
    runner = ProjectionRunner(projection_registry())
    projection = record_ids_projection()
    checkpoint = runner.project(
        journal_with_records("txn-1").read(),
        projection=projection,
    )
    snapshot = runner.snapshot(checkpoint)
    changed = snapshot.content.replace(b"ledgerloom.record_ids", b"ledgerloom.other_ids")

    with pytest.raises(ProjectionSnapshotError) as captured:
        runner.restore(
            changed,
            projection=projection,
            expected_fingerprint=snapshot.fingerprint,
        )

    assert captured.value.code == "projection.snapshot_invalid"


def test_embedded_state_fingerprint_detects_tampering_even_in_rehashed_snapshot():
    runner = ProjectionRunner(projection_registry())
    projection = record_ids_projection()
    checkpoint = runner.project(
        journal_with_records("txn-1").read(),
        projection=projection,
    )
    snapshot = runner.snapshot(checkpoint)
    envelope = loads_canonical_json(snapshot.content)
    assert isinstance(envelope, dict)
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    state = payload["state"]
    assert isinstance(state, dict)
    state_document = state["document"]
    assert isinstance(state_document, dict)
    state_document["payload"] = ["tampered"]
    rehashed = canonical_json_bytes(envelope)

    with pytest.raises(ProjectionSnapshotError) as captured:
        ProjectionSnapshot.parse(rehashed)

    assert captured.value.code == "projection.snapshot_state_invalid"


def test_snapshot_for_another_projection_is_rejected_before_state_decode():
    runner = ProjectionRunner(projection_registry())
    checkpoint = runner.project(
        journal_with_records("txn-1").read(),
        projection=record_ids_projection(),
    )
    snapshot = runner.snapshot(checkpoint)
    other = Projection("ledgerloom.other_ids", (), collect_record_ids)

    with pytest.raises(ProjectionSnapshotError) as captured:
        runner.restore(snapshot, projection=other)

    assert captured.value.code == "projection.snapshot_name_mismatch"


def test_unknown_snapshot_state_schema_has_a_recovery_diagnostic():
    runner = ProjectionRunner(projection_registry())
    projection = record_ids_projection()
    checkpoint = runner.project(
        journal_with_records("txn-1").read(),
        projection=projection,
    )
    snapshot = runner.snapshot(checkpoint)
    envelope = loads_canonical_json(snapshot.content)
    assert isinstance(envelope, dict)
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    state = payload["state"]
    assert isinstance(state, dict)
    unknown_document = SchemaDocument.build(
        SchemaRef.parse("tests.unknown_state", "1.0"),
        ["txn-1"],
    )
    state["document"] = loads_canonical_json(unknown_document.content)
    state["fingerprint"] = str(unknown_document.fingerprint)
    parsed = ProjectionSnapshot.parse(canonical_json_bytes(envelope))

    with pytest.raises(ProjectionSnapshotError) as captured:
        runner.restore(parsed, projection=projection)

    assert captured.value.code == "projection.snapshot_state_decode"


def test_projection_and_checkpoint_are_immutable():
    projection = record_ids_projection()
    checkpoint = ProjectionCheckpoint(
        projection.name,
        (),
        ReplayCursor.genesis(),
    )

    with pytest.raises(FrozenInstanceError):
        projection.name = "ledgerloom.changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        checkpoint.cursor = ReplayCursor.genesis()  # type: ignore[misc]


@pytest.mark.parametrize(
    "definition,code",
    [
        (("not-dotted", (), collect_record_ids), "projection.name_format"),
        (("ledgerloom.ids", (), "reducer"), "projection.reducer_type"),
    ],
)
def test_projection_validates_its_stable_definition(definition, code):
    with pytest.raises(ProjectionError) as captured:
        Projection(*definition)

    assert captured.value.code == code


def test_checkpoint_cannot_resume_a_different_projection():
    runner = ProjectionRunner(projection_registry())
    checkpoint = ProjectionCheckpoint(
        "ledgerloom.record_ids",
        (),
        ReplayCursor.genesis(),
    )

    with pytest.raises(ProjectionError) as captured:
        runner.project(
            (),
            projection=Projection("ledgerloom.other_ids", (), collect_record_ids),
            checkpoint=checkpoint,
        )

    assert captured.value.code == "projection.checkpoint_name_mismatch"
