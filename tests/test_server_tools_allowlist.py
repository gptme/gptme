"""The server's ``--tools`` allowlist must bound new conversations (not just init())."""

import random

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("GPTME_DISABLE_AUTH", "true")
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))


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


def test_explicit_empty_tools_stays_empty_on_restricted_server():
    client = _client(["read", "save"])
    conv_id, resp = _put(client, {"config": {"chat": {"tools": []}}})
    assert resp.status_code == 200
    assert not _conversation_tools(client, conv_id)


def test_file_path_tool_entry_rejected_unless_allowlisted():
    client = _client(["read"])
    _, resp = _put(client, {"config": {"chat": {"tools": ["/tmp/evil_tool.py"]}}})
    assert resp.status_code == 403
    assert "evil_tool.py" in resp.get_json()["error"]


def test_file_path_entry_in_allowlist_kept_in_default(tmp_path):
    tool_file = str(tmp_path / "mytool.py")
    client = _client(["read", tool_file])
    conv_id, resp = _put(client)
    assert resp.status_code == 200
    assert set(_conversation_tools(client, conv_id)) == {"read", tool_file}


def test_patch_cannot_escalate_beyond_server_allowlist():
    client = _client(["read"])
    conv_id, resp = _put(client)
    assert resp.status_code == 200
    resp = client.patch(
        f"/api/v2/conversations/{conv_id}/config",
        json={"chat": {"tools": ["read", "shell"]}},
    )
    assert resp.status_code == 403
    assert _conversation_tools(client, conv_id) == ["read"]


def test_patch_may_narrow_server_allowlist():
    client = _client(["read", "save"])
    conv_id, _ = _put(client)
    resp = client.patch(
        f"/api/v2/conversations/{conv_id}/config", json={"chat": {"tools": ["read"]}}
    )
    assert resp.status_code == 200
    assert _conversation_tools(client, conv_id) == ["read"]


def test_use_acp_rejected_on_restricted_server():
    client = _client(["read"])
    conv_id, put_resp = _put(client)
    resp = client.post(
        f"/api/v2/conversations/{conv_id}/step",
        json={"session_id": put_resp.get_json()["session_id"], "use_acp": True},
    )
    assert resp.status_code == 403
