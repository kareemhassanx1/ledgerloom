import json
import sqlite3
import subprocess
import sys

import pytest

from ledgerloom.application import JournalCatalog
from ledgerloom.cli import ExitCode, main
from ledgerloom.reconciliation import DecisionStatus
from ledgerloom.serialization import SchemaDocument, SchemaRef
from ledgerloom.storage import SQLiteJournal


def _run(capsys, *arguments: str) -> tuple[int, dict[str, object]]:
    exit_code = main(arguments)
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1
    return exit_code, json.loads(lines[0])


def _write_csv(path, *, record_id: str, occurred_at: str, reference: str) -> None:
    path.write_text(
        "record_id,account_id,amount,currency,occurred_at,reference\n"
        f"{record_id},cash,10.00,USD,{occurred_at},{reference}\n",
        encoding="utf-8",
    )


def test_import_reconcile_inspect_and_export_across_runs(tmp_path, capsys) -> None:
    database = tmp_path / "ledgerloom.db"
    bank = tmp_path / "bank.csv"
    processor = tmp_path / "processor.csv"
    export = tmp_path / "journal.jsonl"
    _write_csv(
        bank,
        record_id="bank-1",
        occurred_at="2026-09-02T12:00:00Z",
        reference="invoice-1",
    )
    _write_csv(
        processor,
        record_id="processor-1",
        occurred_at="2026-09-02T12:01:00Z",
        reference="invoice-1",
    )

    code, first = _run(
        capsys,
        "import",
        str(bank),
        "--database",
        str(database),
        "--source",
        "bank",
        "--batch",
        "import-bank",
    )
    assert code == ExitCode.SUCCESS
    assert first["result"]["status"] == "complete"
    assert first["result"]["sequence"] == 1

    code, second = _run(
        capsys,
        "import",
        str(processor),
        "--database",
        str(database),
        "--source",
        "processor",
        "--batch",
        "import-processor",
    )
    assert code == ExitCode.SUCCESS
    assert second["result"]["sequence"] == 2

    code, reconciled = _run(
        capsys,
        "reconcile",
        "--database",
        str(database),
        "--anchor-batch",
        "import-bank",
        "--batch",
        "reconcile-september",
    )
    assert code == ExitCode.SUCCESS
    assert reconciled["result"] == {
        "ambiguous": 0,
        "anchor_batch_id": "import-bank",
        "batch_id": "reconcile-september",
        "candidate_entries": 1,
        "conflict": 0,
        "coverage_basis_points": 10000,
        "document_fingerprint": reconciled["result"]["document_fingerprint"],
        "evaluated_candidates": 1,
        "journal_fingerprint": reconciled["result"]["journal_fingerprint"],
        "matched": 1,
        "sequence": 3,
        "total": 1,
        "unmatched": 0,
    }

    code, duplicate = _run(
        capsys,
        "reconcile",
        "--database",
        str(database),
        "--anchor-batch",
        "import-bank",
        "--batch",
        "reconcile-september",
    )
    assert code == ExitCode.INVALID_REQUEST
    assert duplicate["error"]["code"] == "cli.batch_exists"

    code, inspected = _run(capsys, "inspect", "--database", str(database))
    assert code == ExitCode.SUCCESS
    assert inspected["result"]["entry_count"] == 3
    assert inspected["result"]["integrity"] == {
        "is_valid": True,
        "issues": [],
        "valid_entries": 3,
    }
    assert [item["schema"] for item in inspected["result"]["events"]] == [
        "ledgerloom.ledger_batch",
        "ledgerloom.ledger_batch",
        "ledgerloom.reconciliation_report",
    ]

    code, exported = _run(
        capsys,
        "export",
        "--database",
        str(database),
        "--output",
        str(export),
    )
    assert code == ExitCode.SUCCESS
    assert exported["result"]["entry_count"] == 3
    export_rows = [json.loads(line) for line in export.read_text().splitlines()]
    assert [item["sequence"] for item in export_rows] == [1, 2, 3]
    assert export_rows[2]["document"]["payload"]["summary"]["matched"] == 1


def test_partial_import_is_persisted_with_a_distinct_exit_code(tmp_path, capsys) -> None:
    source = tmp_path / "mixed.jsonl"
    database = tmp_path / "ledgerloom.db"
    source.write_text(
        '{"record_id":"ok","account_id":"cash","amount":"5","currency":"USD",'
        '"occurred_at":"2026-09-02T12:00:00Z"}\n'
        '{"record_id":"zero","account_id":"cash","amount":"0","currency":"USD",'
        '"occurred_at":"2026-09-02T12:01:00Z"}\n',
        encoding="utf-8",
    )

    code, payload = _run(
        capsys,
        "import",
        str(source),
        "--database",
        str(database),
        "--source",
        "bank",
        "--batch",
        "mixed",
    )

    assert code == ExitCode.PARTIAL_IMPORT
    assert payload["ok"] is True
    assert payload["result"]["status"] == "partial"
    assert payload["result"]["accepted"] == 1
    assert payload["result"]["rejected"] == 1
    assert payload["result"]["diagnostic_counts"] == [
        {"code": "ledger_entry.zero_amount", "count": 1}
    ]
    code, inspected = _run(capsys, "inspect", "--database", str(database))
    assert code == ExitCode.SUCCESS
    assert inspected["result"]["entry_count"] == 1


def test_errors_use_the_stable_json_shape_and_exit_codes(tmp_path, capsys) -> None:
    database = tmp_path / "ledgerloom.db"
    with SQLiteJournal(database):
        pass

    code, missing = _run(
        capsys,
        "reconcile",
        "--database",
        str(database),
        "--anchor-batch",
        "absent",
        "--batch",
        "attempt",
    )
    assert code == ExitCode.NOT_FOUND
    assert missing == {
        "command": "reconcile",
        "error": {
            "code": "cli.anchor_batch_not_found",
            "message": "anchor batch was not found",
            "path": "$.anchor_batch",
        },
        "ok": False,
    }

    code, invalid = _run(capsys, "inspect")
    assert code == ExitCode.INVALID_REQUEST
    assert invalid["command"] == "inspect"
    assert invalid["error"]["code"] == "cli.usage"
    assert invalid["ok"] is False


def test_python_module_entry_point_runs_in_a_fresh_process(tmp_path) -> None:
    database = tmp_path / "ledgerloom.db"
    with SQLiteJournal(database):
        pass

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ledgerloom",
            "inspect",
            "--database",
            str(database),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == ExitCode.SUCCESS
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "command": "inspect",
        "ok": True,
        "result": {
            "entry_count": 0,
            "events": [],
            "head_fingerprint": None,
            "integrity": {"is_valid": True, "issues": [], "valid_entries": 0},
        },
    }


@pytest.mark.parametrize(
    ("arguments", "output_name"),
    (
        (("inspect",), None),
        (("export",), "export.jsonl"),
        (("reconcile", "--anchor-batch", "missing", "--batch", "attempt"), None),
    ),
)
def test_existing_journal_commands_do_not_create_a_missing_database(
    tmp_path,
    capsys,
    arguments,
    output_name,
) -> None:
    database = tmp_path / "missing.db"
    command = [arguments[0], "--database", str(database), *arguments[1:]]
    output = None
    if output_name is not None:
        output = tmp_path / output_name
        command.extend(("--output", str(output)))

    code, payload = _run(capsys, *command)

    assert code == ExitCode.NOT_FOUND
    assert payload["error"] == {
        "code": "journal.storage_not_found",
        "message": "journal database was not found",
        "path": "$.database",
    }
    assert not database.exists()
    assert output is None or not output.exists()


def test_inspect_reports_metadata_corruption_without_repairing_it(tmp_path, capsys) -> None:
    database = tmp_path / "corrupt.db"
    source = tmp_path / "source.csv"
    _write_csv(
        source,
        record_id="bank-1",
        occurred_at="2026-09-02T12:00:00Z",
        reference="invoice-1",
    )
    code, _ = _run(
        capsys,
        "import",
        str(source),
        "--database",
        str(database),
        "--source",
        "bank",
        "--batch",
        "import-bank",
    )
    assert code == ExitCode.SUCCESS
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE ledgerloom_journal_metadata SET entry_count = 2 WHERE singleton = 1"
        )

    code, payload = _run(capsys, "inspect", "--database", str(database))

    assert code == ExitCode.STORAGE_FAILURE
    assert payload["ok"] is True
    assert payload["result"]["events"] == []
    assert payload["result"]["integrity"] == {
        "is_valid": False,
        "issues": [
            {
                "code": "journal.truncated",
                "message": "expected 2 entries, found 1",
                "path": "$",
                "sequence": 2,
            }
        ],
        "valid_entries": 1,
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT entry_count FROM ledgerloom_journal_metadata WHERE singleton = 1"
        ).fetchone() == (2,)


def test_import_fails_closed_for_a_future_known_schema(tmp_path, capsys) -> None:
    database = tmp_path / "future.db"
    source = tmp_path / "source.csv"
    _write_csv(
        source,
        record_id="bank-1",
        occurred_at="2026-09-02T12:00:00Z",
        reference="invoice-1",
    )
    future = SchemaDocument.build(
        SchemaRef.parse("ledgerloom.ledger_batch", "2.0"),
        {"batch_id": "future"},
    )
    with SQLiteJournal(database) as journal:
        journal.append(future)

    code, payload = _run(
        capsys,
        "import",
        str(source),
        "--database",
        str(database),
        "--source",
        "bank",
        "--batch",
        "future",
    )

    assert code == ExitCode.STORAGE_FAILURE
    assert payload["error"]["code"] == "catalog.schema_version_unsupported"
    with SQLiteJournal(database) as journal:
        assert len(journal) == 1


def test_reconciliation_preserves_claims_across_separate_runs(tmp_path, capsys) -> None:
    database = tmp_path / "claims.db"
    specifications = (
        ("bank", "batch-a", "a", "2026-09-02T12:00:00Z"),
        ("processor", "batch-b", "b", "2026-09-02T12:04:00Z"),
        ("settlement", "batch-c", "c", "2026-09-02T12:08:00Z"),
    )
    for source_id, batch_id, record_id, occurred_at in specifications:
        source = tmp_path / f"{source_id}.csv"
        _write_csv(
            source,
            record_id=record_id,
            occurred_at=occurred_at,
            reference="invoice-shared",
        )
        code, _ = _run(
            capsys,
            "import",
            str(source),
            "--database",
            str(database),
            "--source",
            source_id,
            "--batch",
            batch_id,
        )
        assert code == ExitCode.SUCCESS

    code, first = _run(
        capsys,
        "reconcile",
        "--database",
        str(database),
        "--anchor-batch",
        "batch-a",
        "--batch",
        "report-a",
    )
    assert code == ExitCode.SUCCESS
    assert first["result"]["matched"] == 1

    code, second = _run(
        capsys,
        "reconcile",
        "--database",
        str(database),
        "--anchor-batch",
        "batch-c",
        "--batch",
        "report-c",
    )
    assert code == ExitCode.SUCCESS
    assert second["result"]["matched"] == 0
    assert second["result"]["conflict"] == 1

    with SQLiteJournal(database) as journal:
        catalog = JournalCatalog.from_entries(journal.read())
    decision = catalog.reports[-1].decisions[0]
    assert decision.status is DecisionStatus.CONFLICT
    assert decision.reason_code == "conflict.candidate_already_claimed"
    assert decision.competing_anchor_ids == (
        catalog.reports[-2].decisions[0].anchor_id,
    )

    code, reused = _run(
        capsys,
        "reconcile",
        "--database",
        str(database),
        "--anchor-batch",
        "batch-a",
        "--batch",
        "report-a-again",
    )
    assert code == ExitCode.INVALID_REQUEST
    assert reused["error"]["code"] == "claim.anchor_already_matched"
