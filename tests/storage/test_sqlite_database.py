import sqlite3

import pytest

from ledgerloom.storage import SQLITE_STORAGE_SCHEMA_VERSION, SqliteDatabase, StorageError


def test_database_initializes_versioned_metadata_and_reopens(tmp_path):
    path = tmp_path / "ledgerloom.sqlite3"

    with SqliteDatabase(path) as database:
        assert database.schema_version == SQLITE_STORAGE_SCHEMA_VERSION
        assert database.path == str(path)
        assert not database.is_closed

    assert database.is_closed
    with SqliteDatabase(path) as reopened:
        assert reopened.schema_version == SQLITE_STORAGE_SCHEMA_VERSION


def test_transaction_commits_successful_writes(tmp_path):
    database = SqliteDatabase(tmp_path / "commit.sqlite3")

    with database.transaction() as connection:
        connection.execute("CREATE TABLE values_table(value TEXT NOT NULL)")
        connection.execute("INSERT INTO values_table(value) VALUES (?)", ("saved",))

    with sqlite3.connect(database.path) as observer:
        assert observer.execute("SELECT value FROM values_table").fetchone() == ("saved",)
    database.close()


def test_transaction_rolls_back_application_failure(tmp_path):
    database = SqliteDatabase(tmp_path / "rollback.sqlite3")
    with database.transaction() as connection:
        connection.execute("CREATE TABLE values_table(value TEXT NOT NULL)")

    with pytest.raises(RuntimeError), database.transaction() as connection:
        connection.execute("INSERT INTO values_table(value) VALUES ('not-saved')")
        raise RuntimeError("injected failure")

    with sqlite3.connect(database.path) as observer:
        assert observer.execute("SELECT value FROM values_table").fetchall() == []
    database.close()


def test_closed_database_rejects_operations(tmp_path):
    database = SqliteDatabase(tmp_path / "closed.sqlite3")
    database.close()
    database.close()

    with pytest.raises(StorageError) as captured:
        _ = database.schema_version

    assert captured.value.code == "storage.closed"


@pytest.mark.parametrize("path", [None, "", "bad\x00path"])
def test_database_rejects_invalid_paths(path):
    with pytest.raises(StorageError):
        SqliteDatabase(path)  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout", [-1, 61, True, float("inf")])
def test_database_rejects_invalid_timeouts(timeout):
    with pytest.raises(StorageError):
        SqliteDatabase(":memory:", timeout_seconds=timeout)


def test_directory_path_reports_stable_open_failure(tmp_path):
    with pytest.raises(StorageError) as captured:
        SqliteDatabase(tmp_path)

    assert captured.value.code == "storage.open_failed"
