import asyncio
import csv
import importlib
import io
import json
import sqlite3
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import cast

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


def test_session_events_default_timestamp_and_listing(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    monkeypatch.setattr(receiver.time, "time", lambda: 100.0)
    created = client.post("/sessions", json={"name": "eventful"}).json()

    monkeypatch.setattr(receiver.time, "time", lambda: 110.0)
    response = client.post(f"/sessions/{created['id']}/events", json={"kind": "marker", "label": "start"})

    assert response.status_code == 201
    assert response.json()["timestamp"] == 110.0
    events = client.get(f"/sessions/{created['id']}/events").json()
    assert events == {"count": 1, "items": [response.json()]}


def test_session_events_accept_explicit_timestamp_and_order(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    monkeypatch.setattr(receiver.time, "time", lambda: 100.0)
    created = client.post("/sessions", json={"name": "eventful"}).json()
    session_id = created["id"]

    assert client.post(
        f"/sessions/{session_id}/events", json={"kind": "b", "timestamp": 130.0}
    ).status_code == 201
    assert client.post(
        f"/sessions/{session_id}/events", json={"kind": "a", "timestamp": 120.0, "label": "left"}
    ).status_code == 201

    events = client.get(f"/sessions/{session_id}/events").json()["items"]
    assert [event["kind"] for event in events] == ["a", "b"]
    assert [event["timestamp"] for event in events] == [120.0, 130.0]


@pytest.mark.parametrize(
    "payload,status_code",
    [
        ({"kind": ""}, 422),
        ({"kind": "x" * 65}, 422),
        ({"kind": "ok", "label": "x" * 121}, 422),
    ],
)
def test_session_event_validation_rejects_invalid_kind_label_and_timestamp(
    monkeypatch, tmp_path, payload, status_code
):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    monkeypatch.setattr(receiver.time, "time", lambda: 100.0)
    created = client.post("/sessions", json={"name": "eventful"}).json()
    response = client.post(f"/sessions/{created['id']}/events", json=payload)

    assert response.status_code == status_code


def test_session_event_missing_session_returns_404(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    assert client.post("/sessions/missing/events", json={"kind": "marker"}).status_code == 404
    assert client.get("/sessions/missing/events").status_code == 404


def test_completed_session_event_timestamps_must_fall_within_session_interval(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    monkeypatch.setattr(receiver.time, "time", lambda: 100.0)
    created = client.post("/sessions", json={"name": "eventful"}).json()
    session_id = created["id"]
    monkeypatch.setattr(receiver.time, "time", lambda: 200.0)
    client.post(f"/sessions/{session_id}/stop")

    for timestamp in (99.0, 201.0):
        response = client.post(
            f"/sessions/{session_id}/events",
            json={"kind": "marker", "timestamp": timestamp},
        )
        assert response.status_code == 409

    assert client.post(
        f"/sessions/{session_id}/events", json={"kind": "marker", "timestamp": 100.0}
    ).status_code == 201
    assert client.post(
        f"/sessions/{session_id}/events", json={"kind": "marker", "timestamp": 200.0}
    ).status_code == 201


def test_aborted_session_rejects_event_markers(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    monkeypatch.setattr(receiver.time, "time", lambda: 100.0)
    created = client.post("/sessions", json={"name": "eventful"}).json()
    with closing(receiver._connect()) as connection:
        connection.execute(
            "UPDATE sessions SET status = 'aborted', ended_at = ? WHERE id = ?",
            (150.0, created["id"]),
        )
        connection.commit()

    response = client.post(f"/sessions/{created['id']}/events", json={"kind": "marker"})
    assert response.status_code == 409
    assert client.get(f"/sessions/{created['id']}/events").json() == {"count": 0, "items": []}


def test_active_session_event_timestamp_cannot_precede_session_start(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    monkeypatch.setattr(receiver.time, "time", lambda: 100.0)
    created = client.post("/sessions", json={"name": "eventful"}).json()
    response = client.post(
        f"/sessions/{created['id']}/events", json={"kind": "marker", "timestamp": 99.0}
    )
    assert response.status_code == 409


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


def test_session_samples_are_bounded_and_ordered(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    monkeypatch.setattr(receiver.time, "time", lambda: 100.0)
    session_id = client.post("/sessions", json={"name": "ordered"}).json()["id"]
    for timestamp in (130.0, 110.0, 120.0):
        sample = extended_sample()
        sample["timestamp"] = timestamp
        assert client.post("/sample", json=sample).status_code == 200

    response = client.get(f"/sessions/{session_id}/samples?limit=2")

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert [item["timestamp"] for item in response.json()["items"]] == [110.0, 120.0]
    assert response.json()["items"][0]["session_id"] == session_id
    assert client.get(f"/sessions/{session_id}/samples?limit=0").status_code == 422
    assert client.get(f"/sessions/{session_id}/samples?limit=5001").status_code == 422


def test_session_csv_exports_have_exact_headers_order_rows_and_quoting(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    monkeypatch.setattr(receiver.time, "time", lambda: 100.0)
    session_id = client.post("/sessions", json={"name": "export"}).json()["id"]
    for timestamp, source in ((120.0, "source,second"), (110.0, "source,first")):
        sample = extended_sample()
        sample.update({"timestamp": timestamp, "source": source})
        assert client.post("/sample", json=sample).status_code == 200
    for timestamp, kind, label in (
        (130.0, "second", "plain"),
        (125.0, "first", 'quoted, "label"'),
    ):
        assert client.post(
            f"/sessions/{session_id}/events",
            json={"timestamp": timestamp, "kind": kind, "label": label},
        ).status_code == 201

    sample_response = client.get(f"/sessions/{session_id}/export.csv")
    event_response = client.get(f"/sessions/{session_id}/events.csv")

    assert sample_response.status_code == 200
    assert event_response.status_code == 200
    assert sample_response.headers["content-type"].startswith("text/csv")
    assert event_response.headers["content-type"].startswith("text/csv")
    sample_rows = list(csv.reader(io.StringIO(sample_response.text)))
    event_rows = list(csv.reader(io.StringIO(event_response.text)))
    assert sample_rows[0] == [
        "session_id", "timestamp", "received_at", "source", "delta", "theta", "alpha",
        "beta", "gamma", "signal_quality", "quality_label", "channel_quality_json",
        "artifact_flags_json",
    ]
    assert event_rows[0] == ["session_id", "event_id", "timestamp", "kind", "label"]
    assert len(sample_rows) == 3
    assert len(event_rows) == 3
    assert [float(row[1]) for row in sample_rows[1:]] == [110.0, 120.0]
    assert [row[3] for row in sample_rows[1:]] == ["source,first", "source,second"]
    assert sample_rows[1][11] == '{"AF7":0.86,"AF8":0.85,"TP10":0.83,"TP9":0.82}'
    assert [float(row[2]) for row in event_rows[1:]] == [125.0, 130.0]
    assert event_rows[1][4] == 'quoted, "label"'
    assert '"source,first"' in sample_response.text
    assert '"quoted, ""label"""' in event_response.text


def test_empty_session_csv_exports_return_headers_without_data_rows(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    session_id = client.post("/sessions", json={"name": "empty"}).json()["id"]

    assert client.get(f"/sessions/{session_id}/samples").json() == {"count": 0, "items": []}
    assert len(list(csv.reader(io.StringIO(client.get(f"/sessions/{session_id}/export.csv").text)))) == 1
    assert len(list(csv.reader(io.StringIO(client.get(f"/sessions/{session_id}/events.csv").text)))) == 1


@pytest.mark.parametrize("suffix", ["samples", "export.csv", "events.csv"])
def test_session_retrieval_and_exports_return_404_for_unknown_session(monkeypatch, tmp_path, suffix):
    receiver = load_receiver(monkeypatch, tmp_path)

    assert TestClient(receiver.app).get(f"/sessions/missing/{suffix}").status_code == 404


def test_session_export_storage_iterator_fetches_in_bounded_batches():
    from services.storage import iter_session_sample_export_rows

    expected = [("session", 1.0), ("session", 2.0)]

    class Cursor:
        calls = []

        def fetchmany(self, size):
            self.calls.append(size)
            return expected if len(self.calls) == 1 else []

    cursor = Cursor()

    class Connection:
        def execute(self, sql, parameters):
            assert "WHERE session_id = ?" in sql
            assert parameters == ("session",)
            return cursor

    rows = list(
        iter_session_sample_export_rows(
            cast(sqlite3.Connection, Connection()), session_id="session", batch_size=2
        )
    )

    assert rows == expected
    assert cursor.calls == [2, 2]


def test_session_csv_streams_can_advance_on_a_different_worker_thread(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)

    monkeypatch.setattr(receiver.time, "time", lambda: 100.0)
    session_id = client.post("/sessions", json={"name": "threaded export"}).json()["id"]
    sample = extended_sample()
    sample["timestamp"] = 110.0
    assert client.post("/sample", json=sample).status_code == 200
    assert client.post(
        f"/sessions/{session_id}/events",
        json={"timestamp": 120.0, "kind": "marker", "label": "thread hop"},
    ).status_code == 201

    for csv_stream in (receiver._sample_csv(session_id), receiver._event_csv(session_id)):
        assert next(csv_stream)
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(next, csv_stream).result()
        csv_stream.close()


def test_session_sample_ordering_index_is_created(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)

    with closing(receiver._connect()) as connection:
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(samples)")}

    assert "idx_samples_session_timestamp" in indexes


def test_aborted_session_csv_stream_closes_database_connection(monkeypatch, tmp_path):
    receiver = load_receiver(monkeypatch, tmp_path)
    client = TestClient(receiver.app)
    original_connect = sqlite3.connect
    connections = []

    class TrackingConnection(sqlite3.Connection):
        was_closed = False

        def close(self):
            self.was_closed = True
            super().close()

    def tracked_connect(*args, **kwargs):
        connection = original_connect(*args, factory=TrackingConnection, **kwargs)
        connections.append(connection)
        return connection

    session_id = client.post("/sessions", json={"name": "aborted export"}).json()["id"]
    monkeypatch.setattr("services.storage.sqlite3.connect", tracked_connect)

    async def abort_after_header():
        stream = receiver._closing_csv_stream(receiver._sample_csv(session_id))
        assert await anext(stream)
        await stream.aclose()

    asyncio.run(abort_after_header())

    assert len(connections) == 1
    assert connections[0].was_closed
