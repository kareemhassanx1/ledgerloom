import json
import subprocess
import sys

from ledgerloom.cli import ExitCode, main


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
