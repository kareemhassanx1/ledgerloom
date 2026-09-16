import json

import pytest

from ledgerloom.cli import main
from ledgerloom.storage import SQLiteJournal


def run(capsys, *args):
    code = main(args)
    captured = capsys.readouterr()
    assert not captured.err
    return code, json.loads(captured.out)


def test_installed_workflow_import_audit_controls_and_backup(tmp_path, capsys):
    source, db, backup = tmp_path / "input.csv", tmp_path / "ledger.db", tmp_path / "backup.db"
    source.write_text(
        "record_id,account_id,amount,currency,occurred_at,reference\n"
        "1,cash,12.50,USD,2026-09-01T00:00:00Z,invoice-1\n",
        encoding="utf-8",
    )
    assert (
        run(
            capsys,
            "import",
            str(source),
            "--database",
            str(db),
            "--source",
            "bank",
            "--batch",
            "one",
        )[0]
        == 0
    )
    args = (
        "audit",
        "--database",
        str(db),
        "--as-of",
        "2026-09-16T00:00:00Z",
        "--period-start",
        "2026-09-01T00:00:00Z",
        "--period-end",
        "2026-10-01T00:00:00Z",
        "--control",
        "bank",
        "cash",
        "USD",
        "1",
        "12.50",
        "0",
    )
    code, output = run(capsys, *args)
    assert code == 0 and output["command"] == "audit"
    result = output["result"]
    assert result["statement_controls"]["passed"]
    assert result["period"]["balances"][0]["closing"] == "12.5"
    assert result["open_items"][0]["age_days"] == 15
    code, saved = run(
        capsys,
        "backup",
        "--database",
        str(db),
        "--output",
        str(backup),
        "--expected-count",
        "1",
        "--expected-head",
        result["head_fingerprint"],
    )
    assert code == 0 and saved["result"]["head_fingerprint"] == result["head_fingerprint"]
    assert run(capsys, *args)[1] == output


@pytest.mark.parametrize(
    "extra",
    [
        ("--period-start", "2026-09-01T00:00:00Z"),
        ("--control", "bank", "cash", "USD", "bad", "1", "0"),
        ("--control", "bank", "cash", "USD", "1", "bad", "0"),
        ("--control", "bank", "cash", "USD", "1", "NaN", "0"),
        ("--compare-reports", "missing", "another"),
    ],
)
def test_audit_invalid_input_is_a_structured_error(tmp_path, capsys, extra):
    db = tmp_path / "empty.db"
    with SQLiteJournal(db):
        pass
    code, payload = run(
        capsys, "audit", "--database", str(db), "--as-of", "2026-09-16T00:00:00Z", *extra
    )
    assert code == 2 and payload["ok"] is False and payload["command"] == "audit"


def test_audit_missing_database_never_creates_it(tmp_path, capsys):
    missing = tmp_path / "missing.db"
    assert (
        run(capsys, "audit", "--database", str(missing), "--as-of", "2026-09-16T00:00:00Z")[0] == 3
    )
    assert not missing.exists()
