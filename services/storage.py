from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_SAMPLE_COLUMNS = (
    "timestamp",
    "received_at",
    "source",
    "delta",
    "theta",
    "alpha",
    "beta",
    "gamma",
    "signal_quality",
)

_SAMPLE_MIGRATION_COLUMNS = {
    "session_id": "TEXT",
    "quality_label": "TEXT",
    "channel_quality_json": "TEXT",
    "artifact_flags_json": "TEXT",
}


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        migrate(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def migrate(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise sqlite3.OperationalError("cannot migrate inside an active transaction")

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL,
                status TEXT NOT NULL,
                software_version TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS samples (
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
        )
        for name, declaration in _SAMPLE_MIGRATION_COLUMNS.items():
            if name not in _column_names(connection, "samples"):
                connection.execute(f"ALTER TABLE samples ADD COLUMN {name} {declaration}")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                kind TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_received_at ON samples(received_at)"
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def insert_sample(
    connection: sqlite3.Connection,
    sample: Mapping[str, Any],
    *,
    max_history_rows: int,
) -> None:
    values = tuple(sample[column] for column in _SAMPLE_COLUMNS)
    with connection:
        connection.execute(
            """
            INSERT INTO samples(timestamp, received_at, source, delta, theta, alpha, beta, gamma, signal_quality)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            values,
        )
        connection.execute(
            "DELETE FROM samples WHERE id IN "
            "(SELECT id FROM samples ORDER BY id DESC LIMIT -1 OFFSET ?)",
            (max_history_rows,),
        )


def fetch_sample_history(connection: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT timestamp, received_at, source, delta, theta, alpha, beta, gamma, signal_quality
        FROM samples ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(zip(_SAMPLE_COLUMNS, row, strict=True)) for row in reversed(rows)]
