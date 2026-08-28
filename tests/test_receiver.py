import importlib
import json
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def load_receiver(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SENSELAYER_DATA_DIR", str(tmp_path / "data"))
    sys.modules.pop("services.receiver", None)
    return importlib.import_module("services.receiver")


def valid_sample():
    return {
        "timestamp": time.time(),
        "source": "pytest-sim",
        "delta": 0.1,
        "theta": 0.2,
        "alpha": 0.3,
        "beta": 0.3,
        "gamma": 0.1,
        "signal_quality": 1.0,
    }


def extended_sample():
    return {
        **valid_sample(),
        "signal_quality": 0.84,
        "quality_label": "good",
        "channel_quality": {
            "TP9": 0.82,
            "AF7": 0.86,
            "AF8": 0.85,
            "TP10": 0.83,
        },
        "artifact_flags": ["high_frequency_noise"],
    }


def test_waiting_ingest_history_and_fresh_health(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)
    assert client.get("/ready").json() == {"ok": True}
    with closing(receiver._connect()) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000
    assert client.get("/health").json()["status"] == "waiting_for_samples"

    response = client.post("/sample", json=valid_sample())
    assert response.status_code == 200
    assert response.json()["session_id"] is None
    health = client.get("/health").json()
    assert health["status"] == "fresh"
    assert health["source"] == "pytest-sim"
    history = client.get("/history?limit=10").json()
    assert history["count"] == 1
    assert history["items"][0]["alpha"] == pytest.approx(0.3)
    assert history["items"][0]["session_id"] is None
    latest = json.loads(receiver.LATEST_PATH.read_text(encoding="utf-8"))
    assert latest["source"] == "pytest-sim"


def test_sample_endpoint_exposes_session_id_for_active_and_inactive_sessions(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    monkeypatch.setattr(receiver.time, "time", lambda: 100.0)
    created = client.post(
        "/sessions",
        json={"name": "baseline", "notes": "active", "source": "pytest-muse"},
    ).json()

    active_response = client.post("/sample", json=valid_sample())
    assert active_response.status_code == 200
    assert active_response.json()["session_id"] == created["id"]
    assert client.get("/history").json()["items"][0]["session_id"] == created["id"]

    monkeypatch.setattr(receiver.time, "time", lambda: 150.0)
    client.post(f"/sessions/{created['id']}/stop")
    inactive_response = client.post("/sample", json=valid_sample())
    assert inactive_response.json()["session_id"] is None
    assert client.get("/history").json()["items"][-1]["session_id"] is None


def test_receiver_closes_every_database_connection(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    connections = []
    original_connect = sqlite3.connect

    class TrackingConnection(sqlite3.Connection):
        was_closed = False

        def close(self):
            self.was_closed = True
            super().close()

    def tracked_connect(*args, **kwargs):
        connection = original_connect(*args, factory=TrackingConnection, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr("services.storage.sqlite3.connect", tracked_connect)
    client = TestClient(receiver.app)
    assert client.get("/ready").status_code == 200
    assert client.get("/history").status_code == 200
    assert client.post("/sample", json=valid_sample()).status_code == 200

    assert len(connections) == 3
    assert all(connection.was_closed for connection in connections)


@pytest.mark.parametrize(
    "field,value",
    [("alpha", -0.1), ("gamma", 1.1), ("signal_quality", 1.1), ("timestamp", -1.0)],
)
def test_rejects_invalid_values(monkeypatch, tmp_path, field, value):
    receiver = load_receiver(monkeypatch, tmp_path)
    payload = valid_sample()
    payload[field] = value
    assert TestClient(receiver.app).post("/sample", json=payload).status_code in (400, 422)


def test_rejects_non_normalized_bands(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    payload = valid_sample()
    payload["alpha"] = 0.1
    response = TestClient(receiver.app).post("/sample", json=payload)
    assert response.status_code == 400
    assert "sum to 1" in response.json()["detail"]


def test_extended_quality_round_trip_uses_canonical_json(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    assert client.post("/sample", json=extended_sample()).status_code == 200

    history_item = client.get("/history").json()["items"][0]
    assert history_item["quality_label"] == "good"
    assert history_item["channel_quality"] == {
        "TP9": 0.82,
        "AF7": 0.86,
        "AF8": 0.85,
        "TP10": 0.83,
    }
    assert history_item["artifact_flags"] == ["high_frequency_noise"]
    with closing(receiver._connect()) as connection:
        stored = connection.execute(
            "SELECT quality_label, channel_quality_json, artifact_flags_json FROM samples"
        ).fetchone()
    assert stored == (
        "good",
        '{"AF7":0.86,"AF8":0.85,"TP10":0.83,"TP9":0.82}',
        '["high_frequency_noise"]',
    )


def test_legacy_payload_gets_detailed_quality_defaults(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    assert client.post("/sample", json=valid_sample()).status_code == 200

    history_item = client.get("/history").json()["items"][0]
    assert history_item["quality_label"] == "unknown"
    assert history_item["channel_quality"] == {}
    assert history_item["artifact_flags"] == []
    assert history_item["signal_quality"] == 1.0


@pytest.mark.parametrize("quality_label", ["excellent", "", 123])
def test_rejects_invalid_quality_labels(monkeypatch, tmp_path, quality_label):
    receiver = load_receiver(monkeypatch, tmp_path)
    payload = extended_sample()
    payload["quality_label"] = quality_label

    assert TestClient(receiver.app).post("/sample", json=payload).status_code == 422


def test_rejects_unknown_channel_quality_keys(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    payload = extended_sample()
    payload["channel_quality"]["AUX"] = 0.5

    assert TestClient(receiver.app).post("/sample", json=payload).status_code == 422


@pytest.mark.parametrize("score", ["NaN", "Infinity", "-Infinity"])
def test_rejects_non_finite_channel_scores(monkeypatch, tmp_path, score):
    receiver = load_receiver(monkeypatch, tmp_path)
    payload = extended_sample()
    payload["channel_quality"]["TP9"] = score

    assert TestClient(receiver.app).post("/sample", json=payload).status_code == 422


def test_rejects_oversized_artifact_lists(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    payload = extended_sample()
    payload["artifact_flags"] = [f"artifact_{index}" for index in range(17)]

    assert TestClient(receiver.app).post("/sample", json=payload).status_code == 422


def test_session_lifecycle_success_and_idempotent_stop(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    monkeypatch.setattr(receiver.time, "time", lambda: 100.0)
    client = TestClient(receiver.app)

    response = client.post(
        "/sessions",
        json={"name": "eyes open", "notes": "baseline", "source": "pytest-muse"},
    )

    assert response.status_code == 201
    created = response.json()
    session_id = created["id"]
    assert str(uuid.UUID(session_id)) == session_id
    assert created == {
        "id": session_id,
        "name": "eyes open",
        "notes": "baseline",
        "source": "pytest-muse",
        "started_at": 100.0,
        "ended_at": None,
        "status": "active",
        "software_version": "2.0.0",
        "created_at": 100.0,
    }
    assert client.get(f"/sessions/{session_id}").json() == created
    assert client.get("/sessions").json() == {"count": 1, "items": [created]}

    monkeypatch.setattr(receiver.time, "time", lambda: 150.0)
    stopped = client.post(f"/sessions/{session_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json() == {**created, "ended_at": 150.0, "status": "completed"}

    monkeypatch.setattr(receiver.time, "time", lambda: 999.0)
    assert client.post(f"/sessions/{session_id}/stop").json() == stopped.json()


def test_session_start_conflict_returns_409(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    assert client.post("/sessions", json={"name": "first"}).status_code == 201
    response = client.post("/sessions", json={"name": "second"})

    assert response.status_code == 409
    assert response.json()["detail"] == "an active session already exists"


def test_missing_session_detail_and_stop_return_404(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    assert client.get("/sessions/missing").status_code == 404
    assert client.post("/sessions/missing/stop").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": "x" * 121},
        {"name": "valid", "notes": "x" * 4001},
        {"name": "valid", "source": ""},
    ],
)
def test_session_start_validation_returns_422(monkeypatch, tmp_path, payload):
    receiver = load_receiver(monkeypatch, tmp_path)

    assert TestClient(receiver.app).post("/sessions", json=payload).status_code == 422


@pytest.mark.parametrize("limit", [0, 1001])
def test_session_list_limit_validation_returns_422(monkeypatch, tmp_path, limit):
    receiver = load_receiver(monkeypatch, tmp_path)

    assert TestClient(receiver.app).get(f"/sessions?limit={limit}").status_code == 422
