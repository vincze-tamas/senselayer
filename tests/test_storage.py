from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

from services.storage import (
    abort_session,
    complete_session,
    connect_database,
    create_session,
    fetch_sample_history,
    get_active_session,
    get_session,
    insert_sample,
    list_sessions,
    migrate,
)


LEGACY_SCHEMA = """
CREATE TABLE samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    received_at REAL NOT NULL,
    source TEXT NOT NULL,
    delta REAL NOT NULL,
    theta REAL NOT NULL,
    alpha REAL NOT NULL,
    beta REAL NOT NULL,
    gamma REAL NOT NULL,
    signal_quality REAL NOT NULL
)
"""

EXPECTED_SAMPLE_COLUMNS = {
    "id",
    "timestamp",
    "received_at",
    "source",
    "delta",
    "theta",
    "alpha",
    "beta",
    "gamma",
    "signal_quality",
    "session_id",
    "quality_label",
    "channel_quality_json",
    "artifact_flags_json",
}


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _sample(timestamp: float, received_at: float, source: str) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "received_at": received_at,
        "source": source,
        "delta": 0.1,
        "theta": 0.2,
        "alpha": 0.3,
        "beta": 0.3,
        "gamma": 0.1,
        "signal_quality": 1.0,
    }


def test_connect_database_initializes_empty_database(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "nested" / "history.db")
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000
        assert _column_names(connection, "samples") == EXPECTED_SAMPLE_COLUMNS
        assert _column_names(connection, "sessions") >= {"id", "status", "started_at", "ended_at"}
        assert _column_names(connection, "session_events") >= {"id", "session_id", "timestamp"}
        foreign_keys = connection.execute("PRAGMA foreign_key_list(session_events)").fetchall()
        assert any(row[2] == "sessions" and row[3] == "session_id" for row in foreign_keys)
    finally:
        connection.close()


def test_migrate_preserves_legacy_rows_and_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(database_path)
    legacy.execute(LEGACY_SCHEMA)
    legacy.execute(
        """
        INSERT INTO samples(timestamp, received_at, source, delta, theta, alpha, beta, gamma, signal_quality)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (10.0, 11.0, "legacy", 0.1, 0.2, 0.3, 0.3, 0.1, 0.8),
    )
    legacy.commit()
    legacy.close()

    connection = connect_database(database_path)
    try:
        migrate(connection)
        migrate(connection)
        assert _column_names(connection, "samples") == EXPECTED_SAMPLE_COLUMNS
        row = connection.execute(
            "SELECT timestamp, received_at, source, signal_quality, session_id, quality_label "
            "FROM samples"
        ).fetchone()
        assert tuple(row) == (10.0, 11.0, "legacy", 0.8, None, None)
        assert connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
    finally:
        connection.close()


def test_sample_storage_round_trip_and_pruning(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "history.db")
    try:
        insert_sample(connection, _sample(1.0, 2.0, "first"), max_history_rows=1)
        insert_sample(connection, _sample(3.0, 4.0, "second"), max_history_rows=1)
        items = fetch_sample_history(connection, limit=10)
    finally:
        connection.close()

    assert [item["source"] for item in items] == ["second"]
    assert items[0]["alpha"] == 0.3


def test_samples_attach_to_active_session_before_during_and_after_stop(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "history.db")
    try:
        before = _sample(0.5, 0.6, "before")
        insert_sample(connection, before, max_history_rows=10)

        session = _create_session(connection)
        insert_sample(connection, _sample(1.0, 2.0, "during"), max_history_rows=10)

        completed = complete_session(connection, str(session["id"]), ended_at=150.0)
        assert completed is not None

        insert_sample(connection, _sample(3.0, 4.0, "after"), max_history_rows=10)

        items = fetch_sample_history(connection, limit=10)
        stored = connection.execute(
            "SELECT source, session_id FROM samples ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    assert [item["source"] for item in items] == ["before", "during", "after"]
    assert [item["session_id"] for item in items] == [None, session["id"], None]
    assert stored == [("before", None), ("during", session["id"]), ("after", None)]


def test_insert_sample_reserves_the_active_session_boundary(tmp_path: Path) -> None:
    database_path = tmp_path / "history.db"
    connection = connect_database(database_path)
    session = _create_session(connection)
    connection.close()

    begin_seen = threading.Event()
    release = threading.Event()

    class InterceptingConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /):  # type: ignore[override]
            if sql == "BEGIN IMMEDIATE":
                result = super().execute(sql, parameters)
                begin_seen.set()
                assert release.wait(timeout=5)
                return result
            return super().execute(sql, parameters)

    ingest_connection = sqlite3.connect(
        database_path, timeout=10, factory=InterceptingConnection, check_same_thread=False
    )
    stop_connection = sqlite3.connect(database_path, timeout=0.1, check_same_thread=False)
    result: dict[str, Any] = {}
    stop_error: list[BaseException] = []

    def ingest() -> None:
        result["sample"] = insert_sample(
            ingest_connection,
            _sample(9.0, 10.0, "boundary"),
            max_history_rows=10,
        )

    def stop_session() -> None:
        try:
            stop_connection.execute(
                "UPDATE sessions SET ended_at = ?, status = ? WHERE id = ? AND status = 'active'",
                (200.0, "completed", session["id"]),
            )
            stop_connection.commit()
        except BaseException as error:
            stop_error.append(error)

    worker = threading.Thread(target=ingest)
    worker.start()
    assert begin_seen.wait(timeout=5)

    stopper = threading.Thread(target=stop_session)
    stopper.start()
    stopper.join(timeout=5)
    release.set()
    worker.join(timeout=5)

    try:
        assert worker.is_alive() is False
        assert stopper.is_alive() is False
        assert result["sample"]["session_id"] == session["id"]
        assert stop_error and isinstance(stop_error[0], sqlite3.OperationalError)
        stored = ingest_connection.execute(
            "SELECT source, session_id FROM samples ORDER BY id"
        ).fetchall()
        assert stored == [("boundary", session["id"])]
    finally:
        ingest_connection.close()
        stop_connection.close()


def test_pruning_keeps_session_rows_and_drops_unassigned_rows(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "history.db")
    try:
        insert_sample(connection, _sample(1.0, 2.0, "before-1"), max_history_rows=2)
        insert_sample(connection, _sample(2.0, 3.0, "before-2"), max_history_rows=2)

        session = _create_session(connection)
        insert_sample(connection, _sample(3.0, 4.0, "attached"), max_history_rows=2)
        complete_session(connection, str(session["id"]), ended_at=150.0)
        insert_sample(connection, _sample(4.0, 5.0, "after-1"), max_history_rows=2)
        insert_sample(connection, _sample(5.0, 6.0, "after-2"), max_history_rows=2)

        items = fetch_sample_history(connection, limit=10)
        stored = connection.execute(
            "SELECT source, session_id FROM samples ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    assert [item["source"] for item in items] == ["attached", "after-1", "after-2"]
    assert [item["session_id"] for item in items] == [session["id"], None, None]
    assert stored == [
        ("attached", session["id"]),
        ("after-1", None),
        ("after-2", None),
    ]


def _create_session(
    connection: sqlite3.Connection,
    *,
    name: str = "baseline",
    created_at: float = 101.0,
) -> dict[str, object]:
    return create_session(
        connection,
        name=name,
        notes="eyes open",
        source="muse2-edge",
        started_at=100.0,
        software_version="2.0.0",
        created_at=created_at,
    )


def test_create_session_round_trip_and_listing(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "history.db")
    try:
        created = _create_session(connection)
        session_id = str(created["id"])

        assert str(uuid.UUID(session_id)) == session_id
        assert created == {
            "id": session_id,
            "name": "baseline",
            "notes": "eyes open",
            "source": "muse2-edge",
            "started_at": 100.0,
            "ended_at": None,
            "status": "active",
            "software_version": "2.0.0",
            "created_at": 101.0,
        }
        assert get_active_session(connection) == created
        assert get_session(connection, session_id) == created
        assert list_sessions(connection, limit=10) == [created]
        assert any(
            row[1] == "idx_one_active_session" and row[2] == 1 and row[4] == 1
            for row in connection.execute("PRAGMA index_list(sessions)")
        )
    finally:
        connection.close()


def test_create_session_rejects_second_active_session(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "history.db")
    try:
        _create_session(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _create_session(connection, name="conflict")
        assert get_active_session(connection)["name"] == "baseline"
    finally:
        connection.close()


def test_complete_session_is_idempotent(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "history.db")
    try:
        created = _create_session(connection)
        completed = complete_session(connection, str(created["id"]), ended_at=150.0)
        repeated = complete_session(connection, str(created["id"]), ended_at=999.0)

        assert completed is not None
        assert completed["status"] == "completed"
        assert completed["ended_at"] == 150.0
        assert repeated == completed
        assert get_active_session(connection) is None
    finally:
        connection.close()


def test_abort_session_closes_active_session_and_allows_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "history.db"
    connection = connect_database(database_path)
    created = _create_session(connection)
    connection.close()

    reopened = connect_database(database_path)
    try:
        assert get_active_session(reopened) == created
        aborted = abort_session(reopened, str(created["id"]), ended_at=160.0)
        replacement = _create_session(reopened, name="replacement", created_at=102.0)

        assert aborted is not None
        assert aborted["status"] == "aborted"
        assert aborted["ended_at"] == 160.0
        assert get_active_session(reopened) == replacement
        assert [item["name"] for item in list_sessions(reopened, limit=1)] == ["replacement"]
    finally:
        reopened.close()


def test_session_transitions_return_none_for_unknown_id(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "history.db")
    try:
        assert get_session(connection, "missing") is None
        assert complete_session(connection, "missing", ended_at=150.0) is None
        assert abort_session(connection, "missing", ended_at=150.0) is None
    finally:
        connection.close()
