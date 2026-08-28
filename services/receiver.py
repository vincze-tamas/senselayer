from __future__ import annotations

import csv
import io
import json
import math
import os
import sqlite3
import time
from collections.abc import AsyncIterator, Generator, Iterable, Iterator
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import iterate_in_threadpool

from services.storage import (
    complete_session,
    connect_database,
    create_session,
    create_session_event,
    fetch_sample_history,
    fetch_session_samples,
    get_session,
    insert_sample,
    iter_session_event_export_rows,
    iter_session_sample_export_rows,
    list_session_events,
    list_sessions,
)

BANDS = ("delta", "theta", "alpha", "beta", "gamma")
CHANNELS = frozenset(("TP9", "AF7", "AF8", "TP10"))
DATA_DIR = Path(os.environ.get("SENSELAYER_DATA_DIR", "data"))
LATEST_PATH = DATA_DIR / "latest_sample.json"
HEALTH_PATH = DATA_DIR / "health.json"
DB_PATH = DATA_DIR / "history.db"
MAX_HISTORY_ROWS = 250_000

app = FastAPI(title="SenseLayer Muse Receiver", version="2.0.0")

QualityScore = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
ArtifactFlag = Annotated[str, Field(min_length=1, max_length=64)]
SessionListLimit = Annotated[int, Query(ge=1, le=1000)]
SessionSampleLimit = Annotated[int, Query(ge=1, le=5000)]

SAMPLE_CSV_COLUMNS = (
    "session_id", "timestamp", "received_at", "source", "delta", "theta", "alpha", "beta",
    "gamma", "signal_quality", "quality_label", "channel_quality_json", "artifact_flags_json",
)
EVENT_CSV_COLUMNS = ("session_id", "event_id", "timestamp", "kind", "label")


class SessionStart(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    notes: str = Field(default="", max_length=4000)
    source: str = Field(default="muse2-edge", min_length=1, max_length=100)


class SessionResponse(BaseModel):
    id: UUID
    name: str = Field(min_length=1, max_length=120)
    notes: str = Field(max_length=4000)
    source: str = Field(min_length=1, max_length=100)
    started_at: float = Field(allow_inf_nan=False)
    ended_at: float | None = Field(default=None, allow_inf_nan=False)
    status: Literal["active", "completed", "aborted"]
    software_version: str = Field(min_length=1, max_length=100)
    created_at: float = Field(allow_inf_nan=False)


class SessionListResponse(BaseModel):
    count: int = Field(ge=0)
    items: list[SessionResponse]


class SessionEventCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=120)
    timestamp: float | None = Field(default=None, allow_inf_nan=False)

    @field_validator("kind")
    @classmethod
    def strip_kind(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("kind must not be empty")
        return value


class SessionEventResponse(BaseModel):
    id: int
    session_id: UUID
    timestamp: float = Field(allow_inf_nan=False)
    kind: str = Field(min_length=1, max_length=64)
    label: str = Field(max_length=120)


class SessionEventListResponse(BaseModel):
    count: int = Field(ge=0)
    items: list[SessionEventResponse]


class Sample(BaseModel):
    timestamp: float = Field(..., description="Unix epoch seconds")
    source: str = Field(default="muse2-edge", min_length=1, max_length=100)
    delta: float
    theta: float
    alpha: float
    beta: float
    gamma: float
    signal_quality: QualityScore = 1.0
    quality_label: Literal["good", "marginal", "bad", "unknown"] = "unknown"
    channel_quality: dict[str, QualityScore] = Field(default_factory=dict, max_length=4)
    artifact_flags: list[ArtifactFlag] = Field(default_factory=list, max_length=16)

    @field_validator("channel_quality")
    @classmethod
    def reject_unknown_channels(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = set(value) - CHANNELS
        if unknown:
            raise ValueError(f"unknown channel_quality keys: {', '.join(sorted(unknown))}")
        return value


def _connect(*, check_same_thread: bool = True) -> sqlite3.Connection:
    return connect_database(DB_PATH, check_same_thread=check_same_thread)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _validated_payload(sample: Sample) -> dict[str, Any]:
    payload = sample.model_dump()
    if not math.isfinite(float(payload["timestamp"])) or float(payload["timestamp"]) <= 0:
        raise HTTPException(status_code=400, detail="invalid timestamp")
    values = []
    for band in BANDS:
        value = float(payload[band])
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise HTTPException(status_code=400, detail=f"invalid {band}")
        values.append(value)
    total = sum(values)
    if not 0.98 <= total <= 1.02:
        raise HTTPException(status_code=400, detail=f"band powers must sum to 1 (got {total:.6f})")
    quality = float(payload["signal_quality"])
    if not math.isfinite(quality) or not 0.0 <= quality <= 1.0:
        raise HTTPException(status_code=400, detail="invalid signal_quality")
    return payload


def _session_event_payload(connection: sqlite3.Connection, session_id: str, event: SessionEventCreate) -> dict[str, Any]:
    session = get_session(connection, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    timestamp = time.time() if event.timestamp is None else event.timestamp
    if not math.isfinite(float(timestamp)):
        raise HTTPException(status_code=422, detail="invalid timestamp")
    try:
        return create_session_event(
            connection,
            session_id=session_id,
            timestamp=float(timestamp),
            kind=event.kind,
            label=event.label,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail="session not found") from error


def _csv_lines(header: tuple[str, ...], rows: Iterable[tuple[Any, ...]]) -> Iterator[str]:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(header)
    yield output.getvalue()
    for row in rows:
        output.seek(0)
        output.truncate(0)
        writer.writerow(row)
        yield output.getvalue()


def _sample_csv(session_id: str) -> Generator[str, None, None]:
    with closing(_connect(check_same_thread=False)) as connection:
        yield from _csv_lines(
            SAMPLE_CSV_COLUMNS,
            iter_session_sample_export_rows(connection, session_id=session_id),
        )


def _event_csv(session_id: str) -> Generator[str, None, None]:
    with closing(_connect(check_same_thread=False)) as connection:
        yield from _csv_lines(
            EVENT_CSV_COLUMNS,
            iter_session_event_export_rows(connection, session_id=session_id),
        )


async def _closing_csv_stream(rows: Generator[str, None, None]) -> AsyncIterator[str]:
    try:
        async for row in iterate_in_threadpool(rows):
            yield row
    finally:
        rows.close()


@app.get("/ready")
def ready() -> dict[str, Any]:
    with closing(_connect()) as connection:
        connection.execute("SELECT 1").fetchone()
    return {"ok": True}


@app.get("/health")
def health() -> dict[str, Any]:
    now = time.time()
    if not LATEST_PATH.exists():
        return {"ok": True, "status": "waiting_for_samples", "now": now}
    try:
        sample = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
        age_seconds = max(0.0, now - float(sample.get("received_at", 0.0)))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"ok": False, "status": "corrupt_latest_sample", "now": now}
    return {
        "ok": True,
        "status": "fresh" if age_seconds <= 15 else "stale",
        "age_sec": round(age_seconds, 3),
        "last_sample_timestamp": sample.get("timestamp"),
        "last_received_at": sample.get("received_at"),
        "source": sample.get("source"),
    }


@app.get("/history")
def history(limit: int = 300) -> dict[str, Any]:
    bounded_limit = max(1, min(limit, 5000))
    with closing(_connect()) as connection:
        items = fetch_sample_history(connection, limit=bounded_limit)
    return {"ok": True, "count": len(items), "items": items}


@app.post("/sessions", response_model=SessionResponse, status_code=201)
def start_session(request: SessionStart) -> dict[str, Any]:
    now = time.time()
    with closing(_connect()) as connection:
        try:
            return create_session(
                connection,
                name=request.name,
                notes=request.notes,
                source=request.source,
                started_at=now,
                software_version=app.version,
                created_at=now,
            )
        except sqlite3.IntegrityError as error:
            raise HTTPException(
                status_code=409, detail="an active session already exists"
            ) from error


@app.get("/sessions", response_model=SessionListResponse)
def sessions(limit: SessionListLimit = 100) -> dict[str, Any]:
    with closing(_connect()) as connection:
        items = list_sessions(connection, limit=limit)
    return {"count": len(items), "items": items}


@app.get("/sessions/{session_id}", response_model=SessionResponse)
def session_detail(session_id: str) -> dict[str, Any]:
    with closing(_connect()) as connection:
        session = get_session(connection, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.get("/sessions/{session_id}/samples")
def session_samples(session_id: str, limit: SessionSampleLimit = 5000) -> dict[str, Any]:
    with closing(_connect()) as connection:
        if get_session(connection, session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        items = fetch_session_samples(connection, session_id=session_id, limit=limit)
    return {"count": len(items), "items": items}


@app.get("/sessions/{session_id}/export.csv")
def export_session_samples(session_id: str) -> StreamingResponse:
    with closing(_connect()) as connection:
        if get_session(connection, session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
    return StreamingResponse(
        _closing_csv_stream(_sample_csv(session_id)),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="session-{session_id}.csv"'},
    )


@app.get("/sessions/{session_id}/events.csv")
def export_session_events(session_id: str) -> StreamingResponse:
    with closing(_connect()) as connection:
        if get_session(connection, session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
    return StreamingResponse(
        _closing_csv_stream(_event_csv(session_id)),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="session-{session_id}-events.csv"'},
    )


@app.get("/sessions/{session_id}/events", response_model=SessionEventListResponse)
def session_events(session_id: str) -> dict[str, Any]:
    with closing(_connect()) as connection:
        if get_session(connection, session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        items = list_session_events(connection, session_id=session_id)
    return {"count": len(items), "items": items}


@app.post("/sessions/{session_id}/events", response_model=SessionEventResponse, status_code=201)
def add_session_event(session_id: str, request: SessionEventCreate) -> dict[str, Any]:
    with closing(_connect()) as connection:
        return _session_event_payload(connection, session_id, request)


@app.post("/sessions/{session_id}/stop", response_model=SessionResponse)
def stop_session(session_id: str) -> dict[str, Any]:
    with closing(_connect()) as connection:
        session = complete_session(connection, session_id, ended_at=time.time())
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.post("/sample")
def ingest_sample(sample: Sample) -> dict[str, Any]:
    payload = _validated_payload(sample)
    now = time.time()
    payload["received_at"] = now
    with closing(_connect()) as connection:
        stored_sample = insert_sample(connection, payload, max_history_rows=MAX_HISTORY_ROWS)
    _atomic_write(LATEST_PATH, stored_sample)
    _atomic_write(HEALTH_PATH, {"updated": now, "source": stored_sample["source"]})
    return {
        "ok": True,
        "received_at": now,
        "session_id": stored_sample["session_id"],
    }
