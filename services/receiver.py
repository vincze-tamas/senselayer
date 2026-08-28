from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from services.storage import connect_database, fetch_sample_history, insert_sample

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


def _connect() -> sqlite3.Connection:
    return connect_database(DB_PATH)


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


@app.post("/sample")
def ingest_sample(sample: Sample) -> dict[str, Any]:
    payload = _validated_payload(sample)
    now = time.time()
    payload["received_at"] = now
    with closing(_connect()) as connection:
        insert_sample(connection, payload, max_history_rows=MAX_HISTORY_ROWS)
    _atomic_write(LATEST_PATH, payload)
    _atomic_write(HEALTH_PATH, {"updated": now, "source": payload["source"]})
    return {"ok": True, "received_at": now}
