from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from collections.abc import Iterator, Mapping
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

_SAMPLE_DB_COLUMNS = _SAMPLE_COLUMNS + (
    "session_id",
    "quality_label",
    "channel_quality_json",
    "artifact_flags_json",
)

_SAMPLE_MIGRATION_COLUMNS = {
    "session_id": "TEXT",
    "quality_label": "TEXT",
    "channel_quality_json": "TEXT",
    "artifact_flags_json": "TEXT",
}


def _normalise_session_id(session_id: Any) -> str | None:
    if session_id is None or session_id == "":
        return None
    return str(session_id)


_SESSION_COLUMNS = (
    "id",
    "name",
    "notes",
    "source",
    "started_at",
    "ended_at",
    "status",
    "software_version",
    "created_at",
)

_SESSION_EVENT_COLUMNS = (
    "id",
    "session_id",
    "timestamp",
    "kind",
    "label",
)


def connect_database(path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10, check_same_thread=check_same_thread)
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
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_samples_session_timestamp
            ON samples(session_id, timestamp, id)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_session
            ON sessions(status) WHERE status = 'active'
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_session_events_session_timestamp
            ON session_events(session_id, timestamp, id)
            """
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _session_from_row(row: tuple[Any, ...] | sqlite3.Row) -> dict[str, Any]:
    return dict(zip(_SESSION_COLUMNS, row, strict=True))


def _session_event_from_row(row: tuple[Any, ...] | sqlite3.Row) -> dict[str, Any]:
    return dict(zip(_SESSION_EVENT_COLUMNS, row, strict=True))


def create_session(
    connection: sqlite3.Connection,
    *,
    name: str,
    notes: str,
    source: str,
    started_at: float,
    software_version: str,
    created_at: float,
) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    values = (
        session_id,
        name,
        notes,
        source,
        started_at,
        None,
        "active",
        software_version,
        created_at,
    )
    with connection:
        connection.execute(
            """
            INSERT INTO sessions(
                id, name, notes, source, started_at, ended_at, status,
                software_version, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            values,
        )
    return dict(zip(_SESSION_COLUMNS, values, strict=True))


def get_active_session(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute(
        f"SELECT {', '.join(_SESSION_COLUMNS)} FROM sessions WHERE status = 'active'"
    ).fetchone()
    return _session_from_row(row) if row is not None else None


def list_sessions(connection: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"SELECT {', '.join(_SESSION_COLUMNS)} "
        "FROM sessions ORDER BY created_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_session_from_row(row) for row in rows]


def get_session(connection: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        f"SELECT {', '.join(_SESSION_COLUMNS)} FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    return _session_from_row(row) if row is not None else None


def _finish_session(
    connection: sqlite3.Connection,
    session_id: str,
    *,
    ended_at: float,
    status: str,
) -> dict[str, Any] | None:
    with connection:
        connection.execute(
            "UPDATE sessions SET ended_at = ?, status = ? WHERE id = ? AND status = 'active'",
            (ended_at, status, session_id),
        )
    return get_session(connection, session_id)


def complete_session(
    connection: sqlite3.Connection, session_id: str, *, ended_at: float
) -> dict[str, Any] | None:
    return _finish_session(connection, session_id, ended_at=ended_at, status="completed")


def abort_session(
    connection: sqlite3.Connection, session_id: str, *, ended_at: float
) -> dict[str, Any] | None:
    return _finish_session(connection, session_id, ended_at=ended_at, status="aborted")


def create_session_event(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    timestamp: float | None = None,
    kind: str,
    label: str = "",
) -> dict[str, Any]:
    started_transaction = False
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
        started_transaction = True
    try:
        session = get_session(connection, session_id)
        if session is None:
            raise LookupError("session not found")
        if session["status"] == "aborted":
            raise ValueError("markers are not allowed on aborted sessions")
        if session["status"] not in {"active", "completed"}:
            raise ValueError("markers are only allowed on active or completed sessions")
        if timestamp is None:
            timestamp = time.time()
        if not math.isfinite(float(timestamp)):
            raise ValueError("invalid event timestamp")
        kind = kind.strip()
        label = label.strip()
        if not kind or len(kind) > 64:
            raise ValueError("invalid event kind")
        if len(label) > 120:
            raise ValueError("invalid event label")
        if timestamp < session["started_at"]:
            raise ValueError("event timestamp precedes session start")
        ended_at = session["ended_at"]
        if ended_at is not None and timestamp > ended_at:
            raise ValueError("event timestamp exceeds completed session interval")
        cursor = connection.execute(
            """
            INSERT INTO session_events(session_id, timestamp, kind, label)
            VALUES(?,?,?,?)
            """,
            (session_id, timestamp, kind, label),
        )
        row = connection.execute(
            "SELECT id, session_id, timestamp, kind, label FROM session_events WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        if row is None:
            raise LookupError("inserted event not found")
        if started_transaction:
            connection.commit()
        return _session_event_from_row(row)
    except BaseException:
        if started_transaction and connection.in_transaction:
            connection.rollback()
        raise


def list_session_events(
    connection: sqlite3.Connection, *, session_id: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, session_id, timestamp, kind, label
        FROM session_events
        WHERE session_id = ?
        ORDER BY timestamp ASC, id ASC
        """,
        (session_id,),
    ).fetchall()
    return [_session_event_from_row(row) for row in rows]


def iter_session_event_export_rows(
    connection: sqlite3.Connection, *, session_id: str, batch_size: int = 1000
) -> Iterator[tuple[Any, ...]]:
    cursor = connection.execute(
        """
        SELECT session_id, id, timestamp, kind, label
        FROM session_events
        WHERE session_id = ?
        ORDER BY timestamp ASC, id ASC
        """,
        (session_id,),
    )
    while rows := cursor.fetchmany(batch_size):
        yield from rows


def insert_sample(
    connection: sqlite3.Connection,
    sample: Mapping[str, Any],
    *,
    max_history_rows: int,
) -> dict[str, Any]:
    stored_sample = {
        **sample,
        "session_id": _normalise_session_id(sample.get("session_id")),
        "quality_label": sample.get("quality_label", "unknown"),
        "channel_quality_json": json.dumps(
            sample.get("channel_quality", {}), sort_keys=True, separators=(",", ":")
        ),
        "artifact_flags_json": json.dumps(
            sample.get("artifact_flags", []), sort_keys=True, separators=(",", ":")
        ),
    }
    values = tuple(stored_sample[column] for column in _SAMPLE_DB_COLUMNS)
    started_transaction = False
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
        started_transaction = True
    try:
        active_session = stored_sample["session_id"] or get_active_session(connection)
        if isinstance(active_session, dict):
            stored_sample["session_id"] = active_session["id"]
        connection.execute(
            """
            INSERT INTO samples(
                timestamp, received_at, source, delta, theta, alpha, beta, gamma,
                signal_quality, session_id, quality_label, channel_quality_json, artifact_flags_json
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            tuple(stored_sample[column] for column in _SAMPLE_DB_COLUMNS),
        )
        connection.execute(
            "DELETE FROM samples WHERE id IN "
            "(SELECT id FROM samples WHERE session_id IS NULL ORDER BY id DESC LIMIT -1 OFFSET ?)",
            (max_history_rows,),
        )
        if started_transaction:
            connection.commit()
    except BaseException:
        if started_transaction and connection.in_transaction:
            connection.rollback()
        raise
    return stored_sample


def _sample_from_row(row: tuple[Any, ...] | sqlite3.Row) -> dict[str, Any]:
    stored: dict[str, Any] = dict(zip(_SAMPLE_DB_COLUMNS, row, strict=True))
    channel_quality_json = stored.pop("channel_quality_json")
    artifact_flags_json = stored.pop("artifact_flags_json")
    stored["quality_label"] = stored["quality_label"] or "unknown"
    stored["session_id"] = _normalise_session_id(stored["session_id"])
    stored["channel_quality"] = (
        json.loads(channel_quality_json) if channel_quality_json is not None else {}
    )
    stored["artifact_flags"] = (
        json.loads(artifact_flags_json) if artifact_flags_json is not None else []
    )
    return stored


def fetch_sample_history(connection: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT timestamp, received_at, source, delta, theta, alpha, beta, gamma,
               signal_quality, session_id, quality_label, channel_quality_json, artifact_flags_json
        FROM samples ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_sample_from_row(row) for row in reversed(rows)]


def fetch_session_samples(
    connection: sqlite3.Connection, *, session_id: str, limit: int
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT timestamp, received_at, source, delta, theta, alpha, beta, gamma,
               signal_quality, session_id, quality_label, channel_quality_json, artifact_flags_json
        FROM samples
        WHERE session_id = ?
        ORDER BY timestamp ASC, id ASC
        LIMIT ?
        """,
        (session_id, limit),
    ).fetchall()
    return [_sample_from_row(row) for row in rows]


def iter_session_sample_export_rows(
    connection: sqlite3.Connection, *, session_id: str, batch_size: int = 1000
) -> Iterator[tuple[Any, ...]]:
    cursor = connection.execute(
        """
        SELECT session_id, timestamp, received_at, source, delta, theta, alpha, beta, gamma,
               signal_quality, COALESCE(quality_label, 'unknown'),
               COALESCE(channel_quality_json, '{}'), COALESCE(artifact_flags_json, '[]')
        FROM samples
        WHERE session_id = ?
        ORDER BY timestamp ASC, id ASC
        """,
        (session_id,),
    )
    while rows := cursor.fetchmany(batch_size):
        yield from rows
