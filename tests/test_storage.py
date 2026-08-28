from __future__ import annotations

import sqlite3
from pathlib import Path

from services.storage import connect_database, fetch_sample_history, insert_sample, migrate


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
