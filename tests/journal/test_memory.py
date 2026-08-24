from dataclasses import FrozenInstanceError

import pytest

from ledgerloom.domain.identifiers import AccountId, SourceId, SourceRecordId
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import SourceRecord
from ledgerloom.domain.time import UtcTimestamp
from ledgerloom.journal import (
    MAX_CONFIGURED_READ_LIMIT,
    InMemoryJournal,
    JournalConflictError,
    JournalEntry,
    JournalError,
    JournalIntegrityError,
    verify_journal_entries,
)
from ledgerloom.serialization.domain_v1 import domain_v1_registry
from ledgerloom.serialization.fingerprints import ContentFingerprint


def event(record_id: str = "txn-1"):
    record = SourceRecord(
        SourceId("bank-primary"),
        SourceRecordId(record_id),
        AccountId("assets:checking"),
        Money.parse("12.50", "USD"),
        UtcTimestamp.parse("2026-08-24T08:50:00Z"),
        f"invoice-{record_id}",
    )
    return domain_v1_registry().encode(record)


def test_append_assigns_contiguous_sequences_and_builds_hash_chain():
    journal = InMemoryJournal()

    first = journal.append(event("txn-1"), expected_sequence=1)
    second = journal.append(event("txn-2"), expected_sequence=2)

    assert first.sequence == 1
    assert first.previous_fingerprint is None
    assert second.sequence == 2
    assert second.previous_fingerprint == first.fingerprint
    assert second.fingerprint != first.fingerprint
    assert journal.next_sequence == 3
    assert journal.head_fingerprint == second.fingerprint
    assert len(journal) == 2
    journal.verify()


def test_equal_event_streams_produce_equal_entry_fingerprints():
    left = InMemoryJournal()
    right = InMemoryJournal()

    left_entries = tuple(left.append(event(record_id)) for record_id in ("a", "b", "c"))
    right_entries = tuple(right.append(event(record_id)) for record_id in ("a", "b", "c"))

    assert [entry.fingerprint for entry in left_entries] == [
        entry.fingerprint for entry in right_entries
    ]


def test_read_uses_exclusive_cursor_and_returns_bounded_immutable_snapshot():
    journal = InMemoryJournal(max_read_limit=2)
    entries = tuple(journal.append(event(f"txn-{index}")) for index in range(1, 5))

    first_page = journal.read()
    second_page = journal.read(after_sequence=first_page[-1].sequence)

    assert first_page == entries[:2]
    assert second_page == entries[2:]
    assert isinstance(first_page, tuple)
    with pytest.raises(FrozenInstanceError):
        first_page[0].sequence = 99  # type: ignore[misc]


def test_read_after_head_returns_empty_snapshot():
    journal = InMemoryJournal()
    journal.append(event())

    assert journal.read(after_sequence=1) == ()
    assert journal.read(after_sequence=100) == ()


@pytest.mark.parametrize(
    "arguments,code",
    [
        ({"after_sequence": -1}, "journal.after_sequence_range"),
        ({"after_sequence": True}, "journal.after_sequence_type"),
        ({"limit": 0}, "journal.read_limit_range"),
        ({"limit": 3}, "journal.read_limit_range"),
        ({"limit": False}, "journal.read_limit_type"),
    ],
)
def test_read_rejects_invalid_cursors_and_limits(arguments, code):
    journal = InMemoryJournal(max_read_limit=2)

    with pytest.raises(JournalError) as captured:
        journal.read(**arguments)

    assert captured.value.code == code


@pytest.mark.parametrize(
    "value,code",
    [
        (0, "journal.max_read_limit_range"),
        (MAX_CONFIGURED_READ_LIMIT + 1, "journal.max_read_limit_range"),
        (True, "journal.max_read_limit_type"),
    ],
)
def test_constructor_rejects_invalid_max_read_limit(value, code):
    with pytest.raises(JournalError) as captured:
        InMemoryJournal(max_read_limit=value)

    assert captured.value.code == code


def test_stale_expected_sequence_rejects_append_without_mutating_journal():
    journal = InMemoryJournal()
    first = journal.append(event("txn-1"))

    with pytest.raises(JournalConflictError) as captured:
        journal.append(event("txn-2"), expected_sequence=1)

    assert captured.value.code == "journal.sequence_conflict"
    assert journal.read() == (first,)
    assert journal.next_sequence == 2


@pytest.mark.parametrize("value", [0, -1, True, "2"])
def test_append_rejects_invalid_expected_sequence(value):
    journal = InMemoryJournal()

    with pytest.raises(JournalError):
        journal.append(event(), expected_sequence=value)  # type: ignore[arg-type]

    assert len(journal) == 0


def test_append_rejects_non_document_without_mutation():
    journal = InMemoryJournal()

    with pytest.raises(JournalError) as captured:
        journal.append(b"event")  # type: ignore[arg-type]

    assert captured.value.code == "journal.document_type"
    assert len(journal) == 0


def test_chain_verifier_rejects_missing_or_reordered_entries():
    journal = InMemoryJournal()
    first = journal.append(event("txn-1"))
    second = journal.append(event("txn-2"))
    third = journal.append(event("txn-3"))

    with pytest.raises(JournalIntegrityError) as missing:
        verify_journal_entries((first, third))
    with pytest.raises(JournalIntegrityError) as reordered:
        verify_journal_entries((second, first, third))

    assert missing.value.code == "journal.sequence_gap"
    assert reordered.value.code == "journal.sequence_gap"


def test_chain_verifier_rejects_entry_linked_to_another_history():
    journal = InMemoryJournal()
    first = journal.append(event("txn-1"))
    unrelated = ContentFingerprint.from_bytes(b"unrelated-history")
    second = JournalEntry.create(2, event("txn-2"), unrelated)

    with pytest.raises(JournalIntegrityError) as captured:
        verify_journal_entries((first, second))

    assert captured.value.code == "journal.chain_mismatch"


def test_reads_and_appends_refuse_to_continue_from_corrupted_memory():
    journal = InMemoryJournal()
    first = journal.append(event("txn-1"))
    object.__setattr__(first, "fingerprint", ContentFingerprint.from_bytes(b"tampered"))

    with pytest.raises(JournalIntegrityError) as read_error:
        journal.read()
    with pytest.raises(JournalIntegrityError) as append_error:
        journal.append(event("txn-2"))

    assert read_error.value.code == "journal.fingerprint_mismatch"
    assert append_error.value.code == "journal.fingerprint_mismatch"
    assert len(journal) == 1


def test_entry_constructor_rejects_missing_previous_fingerprint():
    document = event()
    fingerprint = ContentFingerprint.from_bytes(b"placeholder")

    with pytest.raises(JournalIntegrityError) as captured:
        JournalEntry(2, document, None, fingerprint)

    assert captured.value.code == "journal.previous_missing"
