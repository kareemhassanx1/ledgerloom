from dataclasses import FrozenInstanceError

import pytest

from ledgerloom.domain.identifiers import AccountId, SourceId, SourceRecordId
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import SourceRecord
from ledgerloom.domain.time import UtcTimestamp
from ledgerloom.journal import (
    DecodedJournalEvent,
    InMemoryJournal,
    JournalError,
    JournalIntegrityError,
    JournalReplayer,
    JournalReplayError,
    ReplayCursor,
    ReplayResult,
    replay_entries,
)
from ledgerloom.serialization.domain_v1 import domain_v1_registry
from ledgerloom.serialization.fingerprints import ContentFingerprint
from ledgerloom.serialization.schema import SchemaDocument, SchemaRef, SchemaRegistry


def record(record_id: str, amount: str = "12.50") -> SourceRecord:
    return SourceRecord(
        SourceId("bank-primary"),
        SourceRecordId(record_id),
        AccountId("assets:checking"),
        Money.parse(amount, "USD"),
        UtcTimestamp.parse("2026-08-25T02:40:00+03:00"),
        f"invoice-{record_id}",
    )


def journal_with_records(*items: SourceRecord) -> InMemoryJournal:
    registry = domain_v1_registry()
    journal = InMemoryJournal(max_read_limit=100)
    for item in items:
        journal.append(registry.encode(item))
    return journal


def collect_ids(state: tuple[str, ...], event: DecodedJournalEvent) -> tuple[str, ...]:
    assert isinstance(event.value, SourceRecord)
    return (*state, str(event.value.record_id))


def sum_amounts(state: Money, event: DecodedJournalEvent) -> Money:
    assert isinstance(event.value, SourceRecord)
    return state + event.value.amount


def test_replay_verifies_decodes_and_reduces_in_sequence_order():
    journal = journal_with_records(record("txn-1"), record("txn-2"), record("txn-3"))

    result = replay_entries(
        journal.read(limit=3),
        registry=domain_v1_registry(),
        initial_state=(),
        reducer=collect_ids,
    )

    assert result.state == ("txn-1", "txn-2", "txn-3")
    assert result.started_from == ReplayCursor.genesis()
    assert result.applied_count == 3
    assert result.cursor.sequence == 3
    assert result.cursor.fingerprint == journal.head_fingerprint


def test_decoded_event_exposes_both_content_and_chain_fingerprints():
    journal = journal_with_records(record("txn-1"))
    observed: list[DecodedJournalEvent] = []

    def capture(state, event):
        observed.append(event)
        return state

    replay_entries(
        journal.read(),
        registry=domain_v1_registry(),
        initial_state=None,
        reducer=capture,
    )

    entry = journal.read()[0]
    assert observed == [
        DecodedJournalEvent(
            sequence=1,
            schema=entry.document.schema,
            value=record("txn-1"),
            event_fingerprint=entry.document.fingerprint,
            chain_fingerprint=entry.fingerprint,
        )
    ]


def test_replay_is_deterministic_for_equal_streams_and_initial_state():
    items = (record("a", "1.25"), record("b", "2.50"), record("c", "-0.75"))
    journal = journal_with_records(*items)
    replayer = JournalReplayer(domain_v1_registry())

    first = replayer.replay(
        journal.read(limit=3),
        initial_state=Money.zero("USD"),
        reducer=sum_amounts,
    )
    second = replayer.replay(
        journal.read(limit=3),
        initial_state=Money.zero("USD"),
        reducer=sum_amounts,
    )

    assert first == second
    assert first.state == Money.parse("3.00", "USD")


def test_replay_can_resume_from_a_fingerprint_anchored_cursor():
    journal = journal_with_records(*(record(f"txn-{number}") for number in range(1, 5)))
    entries = journal.read(limit=4)
    replayer = JournalReplayer(domain_v1_registry())

    first = replayer.replay(entries[:2], initial_state=(), reducer=collect_ids)
    second = replayer.replay(
        entries[2:],
        initial_state=first.state,
        reducer=collect_ids,
        cursor=first.cursor,
    )

    assert first.state == ("txn-1", "txn-2")
    assert second.state == ("txn-1", "txn-2", "txn-3", "txn-4")
    assert second.started_from == first.cursor
    assert second.applied_count == 2
    assert second.cursor == ReplayCursor.from_entry(entries[-1])


def test_empty_replay_preserves_state_and_cursor_without_calling_reducer():
    calls = 0

    def reducer(state, event):
        nonlocal calls
        calls += 1
        return state

    cursor = ReplayCursor(2, ContentFingerprint.from_bytes(b"head"))
    result = replay_entries(
        (),
        registry=domain_v1_registry(),
        initial_state={"unchanged": True},
        reducer=reducer,
        cursor=cursor,
    )

    assert result.state == {"unchanged": True}
    assert result.started_from == cursor
    assert result.cursor == cursor
    assert result.applied_count == 0
    assert calls == 0


def test_wrong_resume_fingerprint_fails_before_reducer_runs():
    journal = journal_with_records(record("txn-1"), record("txn-2"))
    entries = journal.read(limit=2)
    calls = 0

    def reducer(state, event):
        nonlocal calls
        calls += 1
        return state

    wrong = ReplayCursor(1, ContentFingerprint.from_bytes(b"wrong-head"))

    with pytest.raises(JournalIntegrityError) as captured:
        replay_entries(
            entries[1:],
            registry=domain_v1_registry(),
            initial_state=None,
            reducer=reducer,
            cursor=wrong,
        )

    assert captured.value.code == "journal.chain_mismatch"
    assert calls == 0


def test_wrong_resume_sequence_fails_before_reducer_runs():
    journal = journal_with_records(record("txn-1"), record("txn-2"))
    entries = journal.read(limit=2)

    with pytest.raises(JournalIntegrityError) as captured:
        replay_entries(
            entries[1:],
            registry=domain_v1_registry(),
            initial_state=(),
            reducer=collect_ids,
            cursor=ReplayCursor.genesis(),
        )

    assert captured.value.code == "journal.sequence_gap"


def test_corruption_near_tail_prevents_all_reducer_calls():
    journal = journal_with_records(record("txn-1"), record("txn-2"), record("txn-3"))
    entries = list(journal.read(limit=3))
    object.__setattr__(
        entries[-1],
        "fingerprint",
        ContentFingerprint.from_bytes(b"tampered"),
    )
    calls = 0

    def reducer(state, event):
        nonlocal calls
        calls += 1
        return state

    with pytest.raises(JournalIntegrityError) as captured:
        replay_entries(
            entries,
            registry=domain_v1_registry(),
            initial_state=None,
            reducer=reducer,
        )

    assert captured.value.code == "journal.fingerprint_mismatch"
    assert calls == 0


def test_unknown_schema_near_tail_prevents_all_reducer_calls():
    known_registry = domain_v1_registry()
    journal = InMemoryJournal()
    journal.append(known_registry.encode(record("txn-1")))
    journal.append(SchemaDocument.build(SchemaRef.parse("private.unknown", "1.0"), {}))
    calls = 0

    def reducer(state, event):
        nonlocal calls
        calls += 1
        return state

    with pytest.raises(JournalReplayError) as captured:
        replay_entries(
            journal.read(),
            registry=known_registry,
            initial_state=None,
            reducer=reducer,
        )

    assert captured.value.code == "journal.replay_unknown_schema"
    assert captured.value.path == "$[1].document.schema"
    assert calls == 0


def test_invalid_known_payload_prevents_all_reducer_calls():
    registry = domain_v1_registry()
    journal = InMemoryJournal()
    valid = registry.encode(record("txn-1"))
    invalid = SchemaDocument.build(valid.schema, {"source_id": "bank-primary"})
    journal.append(valid)
    journal.append(invalid)
    calls = 0

    def reducer(state, event):
        nonlocal calls
        calls += 1
        return state

    with pytest.raises(JournalReplayError) as captured:
        replay_entries(
            journal.read(),
            registry=registry,
            initial_state=None,
            reducer=reducer,
        )

    assert captured.value.code == "journal.replay_decode"
    assert captured.value.path == "$[1].document.payload"
    assert calls == 0


def test_reducer_exception_is_not_misreported_as_journal_corruption():
    journal = journal_with_records(record("txn-1"))

    def reducer(state, event):
        raise RuntimeError("projection bug")

    with pytest.raises(RuntimeError, match="projection bug"):
        replay_entries(
            journal.read(),
            registry=domain_v1_registry(),
            initial_state=None,
            reducer=reducer,
        )


@pytest.mark.parametrize(
    "sequence,fingerprint,code",
    [
        (True, None, "journal.cursor_sequence_type"),
        (-1, None, "journal.cursor_sequence_range"),
        (0, ContentFingerprint.from_bytes(b"unexpected"), "journal.cursor_genesis_fingerprint"),
        (1, None, "journal.cursor_fingerprint_missing"),
        (1, "fingerprint", "journal.cursor_fingerprint_type"),
    ],
)
def test_cursor_validates_sequence_and_fingerprint_relationship(sequence, fingerprint, code):
    with pytest.raises(JournalError) as captured:
        ReplayCursor(sequence, fingerprint)  # type: ignore[arg-type]

    assert captured.value.code == code


def test_cursor_is_immutable():
    cursor = ReplayCursor.genesis()

    with pytest.raises(FrozenInstanceError):
        cursor.sequence = 1  # type: ignore[misc]


def test_cursor_from_entry_requires_journal_entry():
    with pytest.raises(JournalError) as captured:
        ReplayCursor.from_entry("entry")  # type: ignore[arg-type]

    assert captured.value.code == "journal.cursor_entry_type"


def test_cursor_from_entry_rejects_tampered_entry():
    entry = journal_with_records(record("txn-1")).read()[0]
    object.__setattr__(entry, "fingerprint", ContentFingerprint.from_bytes(b"tampered"))

    with pytest.raises(JournalIntegrityError) as captured:
        ReplayCursor.from_entry(entry)

    assert captured.value.code == "journal.fingerprint_mismatch"


@pytest.mark.parametrize(
    "registry,reducer,cursor,code",
    [
        ("registry", collect_ids, None, "journal.replay_registry_type"),
        (SchemaRegistry(), "reducer", None, "journal.replay_reducer_type"),
        (SchemaRegistry(), collect_ids, "cursor", "journal.replay_cursor_type"),
    ],
)
def test_replay_validates_collaborators(registry, reducer, cursor, code):
    with pytest.raises(JournalError) as captured:
        replay_entries(
            (),
            registry=registry,
            initial_state=(),
            reducer=reducer,
            cursor=cursor,
        )

    assert captured.value.code == code


def test_replayer_requires_registry():
    with pytest.raises(JournalError) as captured:
        JournalReplayer("registry")  # type: ignore[arg-type]

    assert captured.value.code == "journal.replay_registry_type"


def test_replay_result_rejects_count_that_disagrees_with_cursor_advance():
    cursor = ReplayCursor(1, ContentFingerprint.from_bytes(b"entry-1"))

    with pytest.raises(JournalError) as captured:
        ReplayResult(None, ReplayCursor.genesis(), cursor, 0)

    assert captured.value.code == "journal.replay_count_mismatch"
