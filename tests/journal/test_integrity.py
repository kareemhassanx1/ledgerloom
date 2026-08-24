import pytest

from ledgerloom.domain.identifiers import AccountId, SourceId, SourceRecordId
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import SourceRecord
from ledgerloom.domain.time import UtcTimestamp
from ledgerloom.journal import (
    InMemoryJournal,
    JournalError,
    JournalIntegrityError,
    JournalIntegrityIssue,
    inspect_journal_entries,
)
from ledgerloom.serialization.domain_v1 import domain_v1_registry
from ledgerloom.serialization.fingerprints import ContentFingerprint


def event(record_id: str):
    return domain_v1_registry().encode(
        SourceRecord(
            SourceId("bank-primary"),
            SourceRecordId(record_id),
            AccountId("assets:checking"),
            Money.parse("12.50", "USD"),
            UtcTimestamp.parse("2026-08-25T02:40:00+03:00"),
            f"invoice-{record_id}",
        )
    )


def populated_journal(count: int = 3) -> InMemoryJournal:
    journal = InMemoryJournal()
    for number in range(1, count + 1):
        journal.append(event(f"txn-{number}"))
    return journal


def test_integrity_report_describes_complete_verified_chain():
    journal = populated_journal()
    entries = journal.read(limit=3)

    report = inspect_journal_entries(
        entries,
        expected_count=3,
        expected_head=entries[-1].fingerprint,
    )

    assert report.is_valid
    assert report.starting_sequence == 1
    assert report.entries_seen == 3
    assert report.valid_entries == 3
    assert report.next_sequence == 4
    assert report.head_fingerprint == entries[-1].fingerprint
    assert report.issues == ()
    report.require_valid()


def test_segment_report_verifies_declared_predecessor_and_global_sequence():
    entries = populated_journal(4).read(limit=4)

    report = inspect_journal_entries(
        entries[2:],
        starting_sequence=3,
        previous_fingerprint=entries[1].fingerprint,
        expected_count=2,
        expected_head=entries[3].fingerprint,
    )

    assert report.is_valid
    assert report.valid_entries == 2
    assert report.next_sequence == 5


def test_report_exposes_first_broken_fingerprint_and_valid_prefix():
    entries = list(populated_journal().read(limit=3))
    object.__setattr__(
        entries[1],
        "fingerprint",
        ContentFingerprint.from_bytes(b"tampered"),
    )

    report = inspect_journal_entries(entries)

    assert not report.is_valid
    assert report.valid_entries == 1
    assert report.head_fingerprint == entries[0].fingerprint
    assert report.issues == (
        JournalIntegrityIssue(
            code="journal.fingerprint_mismatch",
            path="$[1].fingerprint",
            message="entry metadata does not match its fingerprint",
            sequence=2,
        ),
    )


def test_report_stops_after_first_break_to_avoid_cascade_diagnostics():
    entries = list(populated_journal(4).read(limit=4))
    object.__setattr__(entries[1], "sequence", 9)
    object.__setattr__(entries[2], "sequence", 12)

    report = inspect_journal_entries(entries)

    assert len(report.issues) == 1
    assert report.issues[0].code == "journal.sequence_gap"
    assert report.issues[0].sequence == 2


def test_report_detects_chain_link_to_unrelated_history():
    entries = list(populated_journal().read(limit=3))
    object.__setattr__(
        entries[1],
        "previous_fingerprint",
        ContentFingerprint.from_bytes(b"unrelated"),
    )

    report = inspect_journal_entries(entries)

    assert report.issues[0].code == "journal.chain_mismatch"
    assert report.issues[0].path == "$[1].previous_fingerprint"


def test_expected_count_makes_truncation_observable():
    entries = populated_journal(3).read(limit=3)

    report = inspect_journal_entries(entries[:2], expected_count=3)

    assert report.valid_entries == 2
    assert report.issues[0].code == "journal.truncated"
    assert report.issues[0].sequence == 3


def test_expected_count_detects_unexpected_tail():
    entries = populated_journal(3).read(limit=3)

    report = inspect_journal_entries(entries, expected_count=2)

    assert report.valid_entries == 3
    assert report.issues[0].code == "journal.extra_entries"
    assert report.issues[0].sequence == 3


def test_expected_head_detects_stale_but_internally_valid_copy():
    entries = populated_journal(3).read(limit=3)

    report = inspect_journal_entries(
        entries[:2],
        expected_head=entries[2].fingerprint,
    )

    assert report.valid_entries == 2
    assert report.issues[0].code == "journal.head_mismatch"


def test_require_valid_translates_first_issue_to_integrity_error():
    entries = populated_journal(2).read(limit=2)
    report = inspect_journal_entries(entries[:1], expected_count=2)

    with pytest.raises(JournalIntegrityError) as captured:
        report.require_valid()

    assert captured.value.code == "journal.truncated"
    assert captured.value.path == "$"


def test_memory_report_remains_available_when_verified_reads_refuse_corruption():
    journal = populated_journal(2)
    first = journal.read(limit=2)[0]
    object.__setattr__(first, "fingerprint", ContentFingerprint.from_bytes(b"tampered"))

    report = journal.integrity_report()

    assert not report.is_valid
    assert report.issues[0].code == "journal.fingerprint_mismatch"
    with pytest.raises(JournalIntegrityError):
        journal.read()


def test_memory_report_supports_external_count_and_head_expectations():
    journal = populated_journal(2)
    actual_head = journal.head_fingerprint

    assert journal.integrity_report(expected_count=2, expected_head=actual_head).is_valid
    assert journal.integrity_report(expected_count=3).issues[0].code == "journal.truncated"


@pytest.mark.parametrize(
    "arguments,code",
    [
        ({"starting_sequence": True}, "journal.starting_sequence_type"),
        ({"starting_sequence": 0}, "journal.starting_sequence_type_range"),
        ({"expected_count": True}, "journal.expected_count_type"),
        ({"expected_count": -1}, "journal.expected_count_type_range"),
        ({"expected_head": "digest"}, "journal.expected_head_type"),
    ],
)
def test_inspection_validates_options(arguments, code):
    with pytest.raises(JournalError) as captured:
        inspect_journal_entries((), **arguments)

    assert captured.value.code == code


def test_segment_after_genesis_requires_predecessor():
    with pytest.raises(JournalError) as captured:
        inspect_journal_entries((), starting_sequence=2)

    assert captured.value.code == "journal.segment_previous_missing"


def test_genesis_segment_rejects_predecessor():
    with pytest.raises(JournalError) as captured:
        inspect_journal_entries(
            (),
            previous_fingerprint=ContentFingerprint.from_bytes(b"unexpected"),
        )

    assert captured.value.code == "journal.segment_genesis_previous"


def test_inspection_rejects_non_entry_without_raising_constructor_error():
    report = inspect_journal_entries(("not-an-entry",))  # type: ignore[arg-type]

    assert report.issues[0].code == "journal.entry_type"
    assert report.issues[0].path == "$[0]"
    assert report.valid_entries == 0

