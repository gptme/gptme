"""Tests for POST/GET /api/v2/provider/setup."""

import threading
import time
import unittest.mock

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

pytestmark = [pytest.mark.timeout(15)]


@pytest.fixture(autouse=True)
def _clear_setup_registry():
    import gptme.server.provider_setup_api as psa

    psa._setups.clear()
    psa._cancels.clear()
    yield
    psa._setups.clear()
    psa._cancels.clear()


def test_start_provider_setup_unknown_provider(client: FlaskClient):
    resp = client.post(
        "/api/v2/provider/setup", json={"provider": "not-a-real-provider"}
    )
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_start_provider_setup_rejects_non_object(client: FlaskClient):
    resp = client.post("/api/v2/provider/setup", json=["openai-subscription"])
    assert resp.status_code == 400


def test_poll_unknown_setup_id(client: FlaskClient):
    resp = client.get("/api/v2/provider/setup/does-not-exist")
    assert resp.status_code == 404


def test_provider_setup_success_applies_runtime_model(client: FlaskClient, monkeypatch):
    """OAuth success persists the default model through the running-server path."""
    persist = unittest.mock.Mock(return_value=False)
    monkeypatch.setattr("gptme.server.api_v2._persist_default_model", persist)
    monkeypatch.setattr(
        "gptme.llm.models.get_recommended_model", lambda _provider: "gpt-5.2"
    )
    monkeypatch.setattr(
        "gptme.llm.llm_openai_subscription.oauth_authenticate", lambda **_kw: None
    )

    resp = client.post(
        "/api/v2/provider/setup", json={"provider": "openai-subscription"}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "pending"
    setup_id = data["setup_id"]

    deadline = time.monotonic() + 5
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/v2/provider/setup/{setup_id}").get_json()
        if status["status"] != "pending":
            break
        time.sleep(0.05)

    assert status is not None
    assert status["status"] == "connected"
    assert status["model"] == "openai-subscription/gpt-5.2"
    persist.assert_called_once_with("openai-subscription/gpt-5.2")


def test_provider_setup_error(client: FlaskClient, monkeypatch):
    monkeypatch.setattr(
        "gptme.llm.llm_openai_subscription.oauth_authenticate",
        unittest.mock.Mock(side_effect=RuntimeError("Port 1455 is in use")),
    )

    setup_id = client.post(
        "/api/v2/provider/setup", json={"provider": "openai-subscription"}
    ).get_json()["setup_id"]

    deadline = time.monotonic() + 5
    data = None
    while time.monotonic() < deadline:
        data = client.get(f"/api/v2/provider/setup/{setup_id}").get_json()
        if data["status"] != "pending":
            break
        time.sleep(0.05)

    assert data is not None
    assert data["status"] == "error"
    assert "Port 1455" in data["error"]


def test_retry_cancels_pending_setup_and_skips_persist(
    client: FlaskClient, monkeypatch
):
    """A second start for the same provider cancels the first and must not persist it."""
    persist = unittest.mock.Mock(return_value=False)
    monkeypatch.setattr("gptme.server.api_v2._persist_default_model", persist)
    monkeypatch.setattr(
        "gptme.llm.models.get_recommended_model", lambda _provider: "gpt-5.2"
    )

    calls = {"n": 0}
    saw_cancel = threading.Event()

    def _oauth(cancel_event=None, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    saw_cancel.set()
                    raise TimeoutError("OAuth cancelled")
                time.sleep(0.05)
            raise TimeoutError("test oauth was not cancelled")
        return

    monkeypatch.setattr("gptme.llm.llm_openai_subscription.oauth_authenticate", _oauth)

    first = client.post(
        "/api/v2/provider/setup", json={"provider": "openai-subscription"}
    ).get_json()
    first_id = first["setup_id"]

    second = client.post(
        "/api/v2/provider/setup", json={"provider": "openai-subscription"}
    ).get_json()
    second_id = second["setup_id"]
    assert first_id != second_id

    first_status = client.get(f"/api/v2/provider/setup/{first_id}").get_json()
    assert first_status["status"] == "cancelled"
    assert saw_cancel.wait(timeout=2)

    deadline = time.monotonic() + 5
    second_status = None
    while time.monotonic() < deadline:
        second_status = client.get(f"/api/v2/provider/setup/{second_id}").get_json()
        if second_status["status"] != "pending":
            break
        time.sleep(0.05)
    assert second_status is not None
    assert second_status["status"] == "connected"
    persist.assert_called_once_with("openai-subscription/gpt-5.2")
    # Cancelled first worker must not overwrite the replacement's persist.
    assert persist.call_count == 1


def test_openapi_spec_includes_provider_setup(client: FlaskClient):
    spec = client.get("/api/docs/openapi.json").get_json()
    paths = spec["paths"]
    assert "/api/v2/provider/setup" in paths
    assert "/api/v2/provider/setup/{setup_id}" in paths
