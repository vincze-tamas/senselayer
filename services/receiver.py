from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

BANDS = ("delta", "theta", "alpha", "beta", "gamma")
DATA_DIR = Path(os.environ.get("SENSELAYER_DATA_DIR", "data"))
LATEST_PATH = DATA_DIR / "latest_sample.json"
HEALTH_PATH = DATA_DIR / "health.json"
DB_PATH = DATA_DIR / "history.db"
MAX_HISTORY_ROWS = 250_000

app = FastAPI(title="SenseLayer Muse Receiver", version="2.0.0")


class Sample(BaseModel):
    timestamp: float = Field(..., description="Unix epoch seconds")
    source: str = Field(default="muse2-edge", min_length=1, max_length=100)
    delta: float
    theta: float
    alpha: float
    beta: float
    gamma: float
    signal_quality: float = 1.0


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
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
    connection.execute("CREATE INDEX IF NOT EXISTS idx_samples_received_at ON samples(received_at)")
    connection.commit()
    return connection


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


@app.get("/ready")
def ready() -> dict[str, Any]:
    with _connect() as connection:
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
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT timestamp, received_at, source, delta, theta, alpha, beta, gamma, signal_quality
            FROM samples ORDER BY id DESC LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
    items = [
        {
            "timestamp": row[0], "received_at": row[1], "source": row[2],
            "delta": row[3], "theta": row[4], "alpha": row[5], "beta": row[6],
            "gamma": row[7], "signal_quality": row[8],
        }
        for row in reversed(rows)
    ]
    return {"ok": True, "count": len(items), "items": items}


@app.post("/sample")
def ingest_sample(sample: Sample) -> dict[str, Any]:
    payload = _validated_payload(sample)
    now = time.time()
    payload["received_at"] = now
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO samples(timestamp, received_at, source, delta, theta, alpha, beta, gamma, signal_quality)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                float(payload["timestamp"]), now, str(payload["source"]),
                *(float(payload[band]) for band in BANDS),
                float(payload["signal_quality"]),
            ),
        )
        connection.execute(
            "DELETE FROM samples WHERE id IN "
            "(SELECT id FROM samples ORDER BY id DESC LIMIT -1 OFFSET ?)",
            (MAX_HISTORY_ROWS,),
        )
        connection.commit()
    _atomic_write(LATEST_PATH, payload)
    _atomic_write(HEALTH_PATH, {"updated": now, "source": payload["source"]})
    return {"ok": True, "received_at": now}
