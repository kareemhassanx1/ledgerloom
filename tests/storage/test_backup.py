import os

import pytest

from ledgerloom.application.errors import ApplicationError
from ledgerloom.domain import BatchId, LedgerBatch, SourceId, ValidationResult
from ledgerloom.journal import JournalIntegrityError
from ledgerloom.serialization import domain_v1_registry
from ledgerloom.storage import SQLiteJournal, SQLiteJournalMode
from ledgerloom.storage.backup import backup_journal


def document(make_entry, name="a"):
    return domain_v1_registry().encode(
        LedgerBatch(
            BatchId(name),
            SourceId("bank"),
            (ValidationResult.success(make_entry(name, source_id="bank")),),
        )
    )


def test_backup_captures_live_wal_and_restores_to_independent_writable_database(
    tmp_path, make_entry
):
    source, output = tmp_path / "live.db", tmp_path / "restored.db"
    with SQLiteJournal(source) as writer:
        event = writer.append(document(make_entry), expected_sequence=1)
        result = backup_journal(source, output, expected_count=1, expected_head=event.fingerprint)
        assert result["head_fingerprint"] == str(event.fingerprint)
        assert result["entry_count"] == 1 and result["application_verified"]
        with SQLiteJournal(output, mode=SQLiteJournalMode.OPEN) as restored:
            assert len(restored) == 1
            restored.append(document(make_entry, "b"), expected_sequence=2)
        assert len(writer) == 1
    assert not list(tmp_path.glob(".ledgerloom-backup-*"))


def test_backup_external_anchor_failure_does_not_publish_or_change_source(tmp_path, make_entry):
    source, output = tmp_path / "live.db", tmp_path / "copy.db"
    with SQLiteJournal(source) as writer:
        writer.append(document(make_entry))
        with pytest.raises(JournalIntegrityError):
            backup_journal(source, output, expected_count=2)
        assert len(writer) == 1
    assert not output.exists() and not list(tmp_path.glob(".ledgerloom-backup-*"))


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_backup_protects_source_and_every_sidecar(tmp_path, suffix):
    source = tmp_path / "live.db"
    with SQLiteJournal(source), pytest.raises(ApplicationError, match="journal storage file"):
        backup_journal(source, str(source) + suffix)


def test_backup_protects_existing_destination_and_source_hard_links(tmp_path):
    source, output = tmp_path / "live.db", tmp_path / "keep.db"
    with SQLiteJournal(source):
        os.link(source, output)
        with pytest.raises(ApplicationError, match="journal storage file"):
            backup_journal(source, output)
        output.unlink()
        output.write_bytes(b"keep")
        with pytest.raises(ApplicationError, match="already exists"):
            backup_journal(source, output)
        assert output.read_bytes() == b"keep"


def test_backup_racing_output_is_not_overwritten(tmp_path, monkeypatch):
    source, output = tmp_path / "live.db", tmp_path / "race.db"
    original = os.link

    def publish(left, right):
        output.write_bytes(b"other writer")
        original(left, right)

    monkeypatch.setattr("ledgerloom.storage.backup.os.link", publish)
    with SQLiteJournal(source), pytest.raises(ApplicationError, match="appeared"):
        backup_journal(source, output)
    assert output.read_bytes() == b"other writer"
    assert not list(tmp_path.glob(".ledgerloom-backup-*"))


def test_backup_refuses_non_journal_and_missing_inputs(tmp_path):
    source, output = tmp_path / "bad.db", tmp_path / "out.db"
    with pytest.raises(ApplicationError, match="does not exist"):
        backup_journal(source, output)
    source.write_bytes(b"not sqlite")
    with pytest.raises(ApplicationError, match="SQLite backup failed"):
        backup_journal(source, output)
    assert not output.exists()
    with pytest.raises(ApplicationError, match="parent"):
        backup_journal(source, tmp_path / "missing" / "out.db")


def test_backup_aborts_busy_deadline_and_removes_partial_file(tmp_path, monkeypatch):
    source, output = tmp_path / "live.db", tmp_path / "out.db"
    times = iter([0, 31])
    monkeypatch.setattr("ledgerloom.storage.backup.time.monotonic", lambda: next(times))
    with SQLiteJournal(source), pytest.raises(ApplicationError, match="30 seconds"):
        backup_journal(source, output)
    assert not output.exists() and not list(tmp_path.glob(".ledgerloom-backup-*"))


def test_backup_verifies_application_schema_before_publication(tmp_path):
    from ledgerloom.application import CatalogError
    from ledgerloom.serialization import LEDGER_BATCH_V1, SchemaDocument, SchemaRef

    output = tmp_path / "out.db"
    other = tmp_path / "future-known.db"
    with SQLiteJournal(other) as journal:
        journal.append(SchemaDocument.build(SchemaRef.parse(str(LEDGER_BATCH_V1.name), "9.0"), {}))
    with pytest.raises(CatalogError):
        backup_journal(other, output)
    assert not output.exists()
