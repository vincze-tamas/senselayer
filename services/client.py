from __future__ import annotations

import os
from typing import Any, Mapping
from urllib.parse import quote

import requests

DEFAULT_RECEIVER_URL = "http://127.0.0.1:8787"
DEFAULT_TIMEOUT_SECONDS = 5.0


class ReceiverClientError(RuntimeError):
    """An error safe to display in the dashboard."""


def receiver_url(environment: Mapping[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment
    configured = source.get("SENSELAYER_RECEIVER_URL", "").strip()
    return (configured or DEFAULT_RECEIVER_URL).rstrip("/")


class ReceiverClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: Any | None = None,
    ) -> None:
        self.base_url = (base_url or receiver_url()).rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout,
                **kwargs,
            )
        except requests.Timeout as error:
            raise ReceiverClientError("Receiver timed out. Try again.") from error
        except requests.RequestException as error:
            raise ReceiverClientError("Receiver unavailable. Check the local receiver service.") from error

        if not 200 <= response.status_code < 300:
            detail = "request failed"
            try:
                payload = response.json()
                if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
                    detail = payload["detail"]
            except (TypeError, ValueError):
                pass
            raise ReceiverClientError(f"Receiver error ({response.status_code}): {detail}")
        return response

    def _json_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._request(method, path, **kwargs)
        try:
            payload = response.json()
        except (TypeError, ValueError) as error:
            raise ReceiverClientError("Receiver returned an invalid response.") from error
        if not isinstance(payload, dict):
            raise ReceiverClientError("Receiver returned an invalid response.")
        return payload

    @staticmethod
    def _session_path(session_id: str) -> str:
        return f"/sessions/{quote(str(session_id), safe='')}"

    def start_session(self, name: str, notes: str = "") -> dict[str, Any]:
        return self._json_request(
            "POST",
            "/sessions",
            json={"name": name, "notes": notes},
        )

    def list_sessions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        payload = self._json_request("GET", "/sessions", params={"limit": limit})
        items = payload.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ReceiverClientError("Receiver returned an invalid response.")
        return items

    def stop_session(self, session_id: str) -> dict[str, Any]:
        return self._json_request("POST", self._session_path(session_id) + "/stop")

    def add_event(self, session_id: str, kind: str, label: str = "") -> dict[str, Any]:
        return self._json_request(
            "POST",
            self._session_path(session_id) + "/events",
            json={"kind": kind, "label": label},
        )

    def download_samples_csv(self, session_id: str) -> bytes:
        response = self._request("GET", self._session_path(session_id) + "/export.csv")
        return bytes(response.content)

    def download_events_csv(self, session_id: str) -> bytes:
        response = self._request("GET", self._session_path(session_id) + "/events.csv")
        return bytes(response.content)
