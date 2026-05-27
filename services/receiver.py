from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DATA_DIR = Path("data")
LATEST_PATH = DATA_DIR / "latest_sample.json"
HEALTH_PATH = DATA_DIR / "health.json"
DB_PATH = DATA_DIR / "history.db"

app = FastAPI(title="BCI Muse Receiver", version="1.1.0")


class Sample(BaseModel):
    timestamp: float = Field(..., description="Unix epoch seconds")
    source: str = "muse2-edge"
    delta: float
    theta: float
    alpha: float
    beta: float
    gamma: float
    signal_quality: float = 1.0


def _db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_received_at ON samples(received_at)")
    conn.commit()
    return conn


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


@app.get("/health")
def health() -> Dict[str, Any]:
    now = time.time()
    if not LATEST_PATH.exists():
        return {"ok": True, "status": "waiting_for_samples", "now": now}

    sample = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    age_sec = now - float(sample.get("received_at", 0.0))
    status = "fresh" if age_sec <= 15 else "stale"
    return {
        "ok": True,
        "status": status,
        "age_sec": round(age_sec, 3),
        "last_sample_timestamp": sample.get("timestamp"),
        "last_received_at": sample.get("received_at"),
        "source": sample.get("source"),
        "db_path": str(DB_PATH),
    }


@app.get("/history")
def history(limit: int = 300) -> Dict[str, Any]:
    lim = max(1, min(limit, 5000))
    conn = _db()
    rows = conn.execute(
        """
        SELECT timestamp, received_at, source, delta, theta, alpha, beta, gamma, signal_quality
        FROM samples ORDER BY id DESC LIMIT ?
        """,
        (lim,),
    ).fetchall()
    conn.close()
    out: List[Dict[str, Any]] = []
    for r in reversed(rows):
        out.append({
            "timestamp": r[0], "received_at": r[1], "source": r[2],
            "delta": r[3], "theta": r[4], "alpha": r[5], "beta": r[6], "gamma": r[7],
            "signal_quality": r[8],
        })
    return {"ok": True, "count": len(out), "items": out}


@app.post("/sample")
def ingest_sample(sample: Sample) -> Dict[str, Any]:
    now = time.time()
    payload = sample.model_dump()

    for k in ("delta", "theta", "alpha", "beta", "gamma"):
        v = float(payload[k])
        if not (0.0 <= v <= 1e6):
            raise HTTPException(status_code=400, detail=f"invalid {k}={v}")

    payload["received_at"] = now

    conn = _db()
    conn.execute(
        """
        INSERT INTO samples(timestamp, received_at, source, delta, theta, alpha, beta, gamma, signal_quality)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            float(payload["timestamp"]),
            now,
            str(payload.get("source", "unknown")),
            float(payload["delta"]),
            float(payload["theta"]),
            float(payload["alpha"]),
            float(payload["beta"]),
            float(payload["gamma"]),
            float(payload.get("signal_quality", 1.0)),
        ),
    )
    conn.commit()

    conn.execute(
        "DELETE FROM samples WHERE id IN (SELECT id FROM samples ORDER BY id DESC LIMIT -1 OFFSET 250000)"
    )
    conn.commit()
    conn.close()

    _atomic_write(LATEST_PATH, payload)
    _atomic_write(HEALTH_PATH, {"updated": now, "source": payload.get("source", "unknown")})
    return {"ok": True, "received_at": now}
