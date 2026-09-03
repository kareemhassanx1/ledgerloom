import json
import os
from pathlib import Path

import pytest

from ledgerloom.application import ExportError, export_journal_jsonl
from ledgerloom.journal import JournalEntry
from ledgerloom.serialization import domain_v1_registry


def _entries(*records) -> tuple[JournalEntry, ...]:
    entries = []
    previous = None
    registry = domain_v1_registry()
    for sequence, record in enumerate(records, start=1):
        entry = JournalEntry.create(sequence, registry.encode(record), previous)
        entries.append(entry)
        previous = entry.fingerprint
    return tuple(entries)


def test_export_streams_canonical_rows_and_returns_content_fingerprint(
    tmp_path,
    make_entry,
):
    output = tmp_path / "journal.jsonl"
    entries = _entries(make_entry("one"), make_entry("two"))

    summary = export_journal_jsonl(entries, output)

    content = output.read_bytes()
    rows = tuple(json.loads(line) for line in content.splitlines())
    assert [row["sequence"] for row in rows] == [1, 2]
    assert summary.output == output.resolve()
    assert summary.entry_count == 2
    assert summary.bytes_written == len(content)
    assert summary.fingerprint.matches_bytes(content)


def test_export_without_force_never_clobbers_a_concurrent_writer(
    tmp_path,
    make_entry,
    monkeypatch,
):
    output = tmp_path / "journal.jsonl"
    original_link = os.link

    def race(source, destination):
        output.write_bytes(b"competitor\n")
        return original_link(source, destination)

    monkeypatch.setattr("ledgerloom.application.exporting.os.link", race)
    with pytest.raises(ExportError) as captured:
        export_journal_jsonl(_entries(make_entry()), output)

    assert captured.value.code == "export.output_exists"
    assert output.read_bytes() == b"competitor\n"
    assert not tuple(tmp_path.glob(".journal.jsonl.*.tmp"))


def test_force_replaces_an_existing_export(tmp_path, make_entry):
    output = tmp_path / "journal.jsonl"
    output.write_bytes(b"old")

    summary = export_journal_jsonl(_entries(make_entry()), output, force=True)

    assert summary.fingerprint.matches_bytes(output.read_bytes())
    assert output.read_bytes() != b"old"


@pytest.mark.parametrize("suffix", ("", "-wal", "-shm", "-journal"))
def test_export_rejects_journal_and_companion_paths(tmp_path, make_entry, suffix):
    database = (tmp_path / "journal.db").resolve()
    protected = tuple(Path(f"{database}{item}") for item in ("", "-wal", "-shm", "-journal"))
    output = Path(f"{database}{suffix}")

    with pytest.raises(ExportError) as captured:
        export_journal_jsonl(
            _entries(make_entry()),
            output,
            force=True,
            protected_paths=protected,
        )

    assert captured.value.code == "export.output_protected"
    assert not output.exists()


def test_export_rejects_an_existing_hardlink_to_a_protected_file(tmp_path, make_entry):
    protected = tmp_path / "journal.db"
    protected.write_bytes(b"database")
    output = tmp_path / "alias.jsonl"
    os.link(protected, output)

    with pytest.raises(ExportError) as captured:
        export_journal_jsonl(
            _entries(make_entry()),
            output,
            force=True,
            protected_paths=(protected,),
        )

    assert captured.value.code == "export.output_protected"
    assert protected.read_bytes() == b"database"


def test_export_validates_public_arguments(tmp_path):
    with pytest.raises(ExportError) as captured:
        export_journal_jsonl("entries", tmp_path / "out")
    assert captured.value.code == "export.entries_type"
