from __future__ import annotations

import requests
import pytest
from streamlit.testing.v1 import AppTest

import ui
from services.client import ReceiverClient, ReceiverClientError, receiver_url


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def test_receiver_url_uses_loopback_default_and_environment_override():
    assert receiver_url({}) == "http://127.0.0.1:8787"
    assert receiver_url({"SENSELAYER_RECEIVER_URL": " http://receiver:9000/ "}) == "http://receiver:9000"


def test_start_list_stop_and_marker_requests_use_bounded_timeout():
    session = FakeSession(
        [
            FakeResponse(status_code=201, payload={"id": "abc", "status": "active"}),
            FakeResponse(payload={"count": 1, "items": [{"id": "abc", "status": "active"}]}),
            FakeResponse(status_code=201, payload={"id": 1}),
            FakeResponse(payload={"id": "abc", "status": "completed"}),
        ]
    )
    client = ReceiverClient(base_url="http://receiver:8787/", timeout=2.5, session=session)

    assert client.start_session("Baseline", "eyes open")["id"] == "abc"
    assert client.list_sessions(limit=12)[0]["status"] == "active"
    assert client.add_event("abc", "eyes_open", "ready")["id"] == 1
    assert client.stop_session("abc")["status"] == "completed"

    assert session.calls == [
        ("POST", "http://receiver:8787/sessions", {"json": {"name": "Baseline", "notes": "eyes open"}, "timeout": 2.5}),
        ("GET", "http://receiver:8787/sessions", {"params": {"limit": 12}, "timeout": 2.5}),
        ("POST", "http://receiver:8787/sessions/abc/events", {"json": {"kind": "eyes_open", "label": "ready"}, "timeout": 2.5}),
        ("POST", "http://receiver:8787/sessions/abc/stop", {"timeout": 2.5}),
    ]


def test_csv_download_returns_bytes_and_uses_same_timeout():
    session = FakeSession(
        [
            FakeResponse(content=b"session_id,timestamp\nabc,1\n"),
            FakeResponse(content=b"session_id,event_id\nabc,2\n"),
        ]
    )
    client = ReceiverClient(base_url="http://receiver:8787", timeout=3.0, session=session)

    assert client.download_samples_csv("abc") == b"session_id,timestamp\nabc,1\n"
    assert client.download_events_csv("abc") == b"session_id,event_id\nabc,2\n"
    assert session.calls == [
        ("GET", "http://receiver:8787/sessions/abc/export.csv", {"timeout": 3.0}),
        ("GET", "http://receiver:8787/sessions/abc/events.csv", {"timeout": 3.0}),
    ]


def test_timeout_has_safe_user_facing_message():
    session = FakeSession([requests.Timeout("socket details")])
    client = ReceiverClient(session=session)

    with pytest.raises(ReceiverClientError, match="Receiver timed out") as error:
        client.list_sessions()

    assert "socket details" not in str(error.value)


def test_http_error_displays_receiver_detail():
    session = FakeSession([FakeResponse(status_code=409, payload={"detail": "an active session already exists"})])
    client = ReceiverClient(session=session)

    with pytest.raises(ReceiverClientError) as error:
        client.start_session("Duplicate")

    assert str(error.value) == "Receiver error (409): an active session already exists"


def test_network_and_malformed_json_errors_are_user_facing():
    network_client = ReceiverClient(session=FakeSession([requests.ConnectionError("secret endpoint")]))
    with pytest.raises(ReceiverClientError, match="Receiver unavailable"):
        network_client.list_sessions()

    malformed_client = ReceiverClient(session=FakeSession([FakeResponse(payload=ValueError("bad json"))]))
    with pytest.raises(ReceiverClientError, match="invalid response"):
        malformed_client.list_sessions()


@pytest.mark.parametrize(
    ("sessions", "active_id", "start_disabled", "active_actions_disabled"),
    [
        ([], None, False, True),
        ([{"id": "old", "status": "completed"}], None, False, True),
        (
            [{"id": "old", "status": "completed"}, {"id": "current", "status": "active"}],
            "current",
            True,
            False,
        ),
    ],
)
def test_session_action_state_disables_invalid_actions(
    sessions, active_id, start_disabled, active_actions_disabled
):
    state = ui.session_action_state(sessions)
    assert (state.active_session or {}).get("id") == active_id
    assert state.start_disabled is start_disabled
    assert state.active_actions_disabled is active_actions_disabled


def test_load_dashboard_sessions_returns_safe_error_for_display():
    class BrokenClient:
        def list_sessions(self, *, limit):
            assert limit == 20
            raise ReceiverClientError("Receiver timed out. Try again.")

    sessions, error = ui.load_dashboard_sessions(BrokenClient())
    assert sessions == []
    assert error == "Receiver timed out. Try again."


def test_session_controls_display_receiver_error_and_fail_closed():
    app = AppTest.from_string(
        """
import ui
from services.client import ReceiverClientError

class BrokenClient:
    def list_sessions(self, *, limit):
        raise ReceiverClientError("Receiver timed out. Try again.")

ui.render_session_controls(BrokenClient())
"""
    ).run(timeout=10)

    assert app.exception == []
    assert app.error[0].value == "Receiver timed out. Try again."
    assert app.button == []


def test_standard_marker_contract_matches_roadmap():
    assert ui.STANDARD_SESSION_MARKERS == (
        ("Eyes open", "eyes_open"),
        ("Eyes closed", "eyes_closed"),
        ("Breathing", "breathing"),
        ("Task start", "task_start"),
    )


def test_session_controls_render_no_active_session_with_invalid_actions_disabled():
    app = AppTest.from_string(
        """
import ui

class Client:
    def list_sessions(self, *, limit):
        return []

ui.render_session_controls(Client())
"""
    ).run(timeout=10)

    assert app.exception == []
    assert app.header[0].value == "Measurement sessions"
    buttons = {button.label: button.disabled for button in app.button}
    assert buttons["Start session"] is True
    assert buttons["Stop session"] is True
    assert all(buttons[label] is True for label, _kind in ui.STANDARD_SESSION_MARKERS)


def test_session_controls_render_active_session_with_start_disabled_and_markers_enabled():
    app = AppTest.from_string(
        """
import ui

class Client:
    def list_sessions(self, *, limit):
        return [{
            "id": "abc",
            "name": "Baseline",
            "status": "active",
            "started_at": 1_700_000_000.0,
            "ended_at": None,
        }]

ui.render_session_controls(Client())
"""
    ).run(timeout=10)

    assert app.exception == []
    buttons = {button.label: button.disabled for button in app.button}
    assert buttons["Start session"] is True
    assert buttons["Stop session"] is False
    assert all(buttons[label] is False for label, _kind in ui.STANDARD_SESSION_MARKERS)
    assert app.text_input[0].disabled is True
    assert app.text_area[0].disabled is True
    assert app.selectbox[0].value == "abc"


def test_session_controls_start_mark_and_stop_workflow():
    app = AppTest.from_string(
        """
import streamlit as st
import ui

class Client:
    def list_sessions(self, *, limit):
        session = st.session_state.get("fake_session")
        return [session] if session else []

    def start_session(self, name, notes=""):
        st.session_state["fake_session"] = {
            "id": "abc", "name": name, "notes": notes, "status": "active",
            "started_at": 1_700_000_000.0, "ended_at": None,
        }

    def add_event(self, session_id, kind, label=""):
        st.session_state["fake_event"] = (session_id, kind, label)

    def stop_session(self, session_id):
        st.session_state["fake_session"]["status"] = "completed"
        st.session_state["fake_session"]["ended_at"] = 1_700_000_100.0

ui.render_session_controls(Client())
"""
    ).run(timeout=10)

    app.text_input[0].input("Baseline")
    app.text_area[0].input("Protocol notes")
    app.run(timeout=10)
    next(button for button in app.button if button.label == "Start session").click().run(timeout=10)
    assert app.session_state["fake_session"]["status"] == "active"
    assert app.success[0].value == "Measurement session started."

    next(button for button in app.button if button.label == "Eyes open").click().run(timeout=10)
    assert app.session_state["fake_event"] == ("abc", "eyes_open", "")

    next(item for item in app.text_input if item.label == "Free-text marker").input("Blink")
    app.run(timeout=10)
    next(button for button in app.button if button.label == "Add note marker").click().run(timeout=10)
    assert app.session_state["fake_event"] == ("abc", "note", "Blink")

    next(button for button in app.button if button.label == "Stop session").click().run(timeout=10)
    assert app.session_state["fake_session"]["status"] == "completed"
    buttons = {button.label: button.disabled for button in app.button}
    assert buttons["Start session"] is False
    assert buttons["Stop session"] is True
