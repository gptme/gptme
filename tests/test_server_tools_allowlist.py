"""The server's ``--tools`` allowlist must bound new conversations (not just init())."""

import random

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setenv("GPTME_DISABLE_AUTH", "true")


def _client(tool_allowlist: list[str] | None) -> FlaskClient:
    from gptme.server.app import create_app  # fmt: skip

    app = create_app(tool_allowlist=tool_allowlist)
    app.config["TESTING"] = True
    return app.test_client()


def _put(client: FlaskClient, body: dict | None = None):
    conv_id = f"test-tools-allowlist-{random.randint(0, 10_000_000)}"
    resp = client.put(f"/api/v2/conversations/{conv_id}", json=body or {})
    return conv_id, resp


def _conversation_tools(client: FlaskClient, conv_id: str) -> list[str]:
    resp = client.get(f"/api/v2/conversations/{conv_id}/config")
    assert resp.status_code == 200
    return resp.get_json()["chat"]["tools"]


def test_new_conversation_defaults_to_server_allowlist():
    client = _client(["read", "save", "shell"])
    conv_id, resp = _put(client)
    assert resp.status_code == 200
    assert set(_conversation_tools(client, conv_id)) == {"read", "save", "shell"}


def test_server_allowlist_none_means_no_tools():
    client = _client([])
    conv_id, resp = _put(client)
    assert resp.status_code == 200
    assert not _conversation_tools(client, conv_id)


def test_request_cannot_escalate_beyond_server_allowlist():
    client = _client(["read"])
    _, resp = _put(client, {"config": {"chat": {"tools": ["read", "shell"]}}})
    assert resp.status_code == 403
    assert "shell" in resp.get_json()["error"]


def test_request_may_narrow_server_allowlist():
    client = _client(["read", "save"])
    conv_id, resp = _put(client, {"config": {"chat": {"tools": ["read"]}}})
    assert resp.status_code == 200
    assert _conversation_tools(client, conv_id) == ["read"]


def test_unrestricted_server_keeps_full_default():
    client = _client(None)
    conv_id, resp = _put(client)
    assert resp.status_code == 200
    assert len(_conversation_tools(client, conv_id)) > 3
