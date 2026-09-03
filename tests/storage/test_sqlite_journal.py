import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from ledgerloom.domain.identifiers import AccountId, SourceId, SourceRecordId
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import SourceRecord
from ledgerloom.domain.time import UtcTimestamp
from ledgerloom.journal import (
    JournalConflictError,
    JournalError,
    JournalIntegrityError,
    JournalStorageError,
)
from ledgerloom.serialization.domain_v1 import domain_v1_registry
from ledgerloom.storage import SQLiteJournal


def event(record_id: str):
    return domain_v1_registry().encode(
        SourceRecord(
            SourceId("bank-primary"),
            SourceRecordId(record_id),
            AccountId("assets:checking"),
            Money.parse("12.50", "USD"),
            UtcTimestamp.parse("2026-09-01T09:55:00Z"),
            f"invoice-{record_id}",
        )
    )


def test_batch_append_survives_close_reopen_and_resumes_chain(tmp_path):
    path = tmp_path / "journal.sqlite3"

    with SQLiteJournal(path, max_read_limit=2) as journal:
        appended = journal.append_many(
            (event("txn-1"), event("txn-2"), event("txn-3")),
            expected_sequence=1,
        )
        assert journal.read() == appended[:2]
        assert journal.read(after_sequence=2) == appended[2:]
        persisted_head = journal.head_fingerprint

    with SQLiteJournal(path) as reopened:
        assert len(reopened) == 3
        assert reopened.head_fingerprint == persisted_head
        fourth = reopened.append(event("txn-4"), expected_sequence=4)
        assert fourth.previous_fingerprint == persisted_head
        assert reopened.integrity_report(expected_count=4).is_valid


def test_failed_batch_insert_rolls_back_every_row_and_metadata(tmp_path):
    path = tmp_path / "atomic.sqlite3"
    journal = SQLiteJournal(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_second_entry
            BEFORE INSERT ON ledgerloom_journal_entries
            WHEN NEW.sequence = 2
            BEGIN
                SELECT RAISE(ABORT, 'injected batch failure');
            END
            """
        )

    with pytest.raises(JournalStorageError) as captured:
        journal.append_many((event("txn-1"), event("txn-2")), expected_sequence=1)

    assert captured.value.code == "journal.storage_error"
    assert len(journal) == 0
    assert journal.next_sequence == 1
    assert journal.head_fingerprint is None
    journal.close()


def test_failed_commit_rolls_back_and_leaves_connection_reusable(tmp_path):
    path = tmp_path / "commit-failure.sqlite3"
    journal = SQLiteJournal(path)

    def deny_commit(action, argument, *_):
        if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    journal._connection.set_authorizer(deny_commit)
    with pytest.raises(JournalStorageError) as captured:
        journal.append(event("rejected"), expected_sequence=1)

    assert captured.value.code == "journal.storage_error"
    assert journal._connection.in_transaction is False

    journal._connection.set_authorizer(None)
    appended = journal.append(event("accepted"), expected_sequence=1)
    assert journal.read() == (appended,)
    journal.close()


def test_stale_batch_expectation_does_not_mutate_database(tmp_path):
    with SQLiteJournal(tmp_path / "conflict.sqlite3") as journal:
        first = journal.append(event("txn-1"))

        with pytest.raises(JournalConflictError) as captured:
            journal.append_many((event("txn-2"), event("txn-3")), expected_sequence=1)

        assert captured.value.code == "journal.sequence_conflict"
        assert journal.read() == (first,)


def test_competing_instances_allow_only_one_expected_head_writer(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    left = SQLiteJournal(path)
    right = SQLiteJournal(path)
    barrier = Barrier(2)

    def append_at_genesis(journal, record_id):
        barrier.wait()
        try:
            return "appended", journal.append(event(record_id), expected_sequence=1)
        except JournalConflictError as error:
            return "conflict", error.code

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                future.result()
                for future in (
                    executor.submit(append_at_genesis, left, "left"),
                    executor.submit(append_at_genesis, right, "right"),
                )
            )

        assert sorted(result[0] for result in results) == ["appended", "conflict"]
        assert len(left) == 1
        assert left.read() == right.read()
    finally:
        left.close()
        right.close()


def test_reopen_refuses_tampered_chain_fingerprint(tmp_path):
    path = tmp_path / "tampered.sqlite3"
    with SQLiteJournal(path) as journal:
        journal.append(event("txn-1"))

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE ledgerloom_journal_entries SET fingerprint = ? WHERE sequence = 1",
            ("sha256:" + ("0" * 64),),
        )

    with pytest.raises(JournalIntegrityError) as captured:
        SQLiteJournal(path)

    assert captured.value.code == "journal.fingerprint_mismatch"


def test_batch_validation_happens_before_storage_mutation(tmp_path):
    with SQLiteJournal(tmp_path / "validation.sqlite3") as journal:
        with pytest.raises(JournalError) as captured:
            journal.append_many((event("txn-1"), b"invalid"))  # type: ignore[arg-type]

        assert captured.value.code == "journal.document_type"
        assert len(journal) == 0


def test_closed_journal_rejects_operations(tmp_path):
    journal = SQLiteJournal(tmp_path / "closed.sqlite3")
    journal.close()

    with pytest.raises(JournalStorageError) as captured:
        journal.read()

    assert captured.value.code == "journal.storage_closed"


def test_open_failure_uses_stable_storage_error(tmp_path):
    with pytest.raises(JournalStorageError) as captured:
        SQLiteJournal(tmp_path / "missing" / "journal.sqlite3")

    assert captured.value.code == "journal.storage_open_failed"
