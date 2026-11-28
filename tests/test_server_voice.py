"""Tests for server-managed realtime voice (gptme/server/voice_api.py).

The gptme-voice sidecars are not started here: tests drive VoiceManager with a
fake sidecar, and cover the per-turn transcript endpoint the sidecar posts to.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

pytest.importorskip("flask", reason="flask not installed; install -E server")

from gptme.message import Message
from gptme.server import voice_api
from gptme.server.voice_api import VoiceManager, VoiceSidecar

if TYPE_CHECKING:
    from flask.testing import FlaskClient


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """A VoiceManager with one healthy fake sidecar (openai) and grok unconfigured."""
    mgr = VoiceManager(
        server_url="http://127.0.0.1:5700", server_token="t", workspace=tmp_path
    )
    proc = MagicMock()
    proc.poll.return_value = None
    sidecar = VoiceSidecar(provider="openai", port=65000, proc=proc)
    monkeypatch.setattr(sidecar, "healthy", lambda: True)
    mgr.sidecars["openai"] = sidecar
    mgr.unavailable["grok"] = "XAI_API_KEY not set"
    monkeypatch.setattr(voice_api, "_manager", mgr)
    return mgr


def _new_conversation(client: FlaskClient) -> str:
    conv = f"test-voice-{uuid.uuid4().hex[:8]}"
    assert client.put(f"/api/v2/conversations/{conv}", json={}).status_code == 200
    return conv


def test_status_when_voice_disabled(client: FlaskClient, monkeypatch):
    monkeypatch.setattr(voice_api, "_manager", None)
    data = client.get("/api/v2/voice").get_json()
    assert data["available"] is False
    assert data["providers"] == []


def test_status_reports_providers(client: FlaskClient, manager):
    data = client.get("/api/v2/voice").get_json()
    assert data["available"] is True
    assert data["default_provider"] == "openai"
    by_id = {p["id"]: p for p in data["providers"]}
    assert by_id["openai"]["available"] is True
    assert by_id["grok"] == {
        "id": "grok",
        "label": "xAI Grok Realtime",
        "available": False,
        "reason": "XAI_API_KEY not set",
    }


def test_manager_without_gptme_voice_reports_why(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_api, "_resolve_sidecar_command", lambda: None)
    mgr = VoiceManager(server_url="http://x", server_token=None, workspace=tmp_path)
    mgr.start()
    status = mgr.status()
    assert status["available"] is False
    assert "gptme-voice is not installed" in status["reason"]


def test_ticket_is_single_use_and_bound_to_conversation(manager):
    ticket = manager.issue_ticket("conv-a", "openai")
    assert manager.redeem_ticket(ticket, "conv-b") is None  # wrong conversation
    ticket = manager.issue_ticket("conv-a", "openai")
    entry = manager.redeem_ticket(ticket, "conv-a")
    assert entry is not None and entry.provider == "openai"
    assert manager.redeem_ticket(ticket, "conv-a") is None  # already used


def test_ticket_expires(manager, monkeypatch):
    ticket = manager.issue_ticket("conv-a", "openai")
    later = time.monotonic() + voice_api.TICKET_TTL_SECONDS + 1
    monkeypatch.setattr(voice_api.time, "monotonic", lambda: later)
    assert manager.redeem_ticket(ticket, "conv-a") is None


def test_session_issues_ticket_for_existing_conversation(client: FlaskClient, manager):
    conv = _new_conversation(client)
    resp = client.post(f"/api/v2/conversations/{conv}/voice/session", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["provider"] == "openai"
    assert data["path"] == f"/api/v2/conversations/{conv}/voice"
    assert manager.redeem_ticket(data["ticket"], conv) is not None


def test_session_errors(client: FlaskClient, manager):
    missing = client.post("/api/v2/conversations/nope-xyz/voice/session", json={})
    assert missing.status_code == 404
    conv = _new_conversation(client)
    unknown = client.post(
        f"/api/v2/conversations/{conv}/voice/session", json={"provider": "acme"}
    )
    assert unknown.status_code == 400
    unavailable = client.post(
        f"/api/v2/conversations/{conv}/voice/session", json={"provider": "grok"}
    )
    assert unavailable.status_code == 503
    assert "XAI_API_KEY not set" in unavailable.get_json()["error"]


def test_websocket_route_requires_upgrade_and_valid_ticket(
    client: FlaskClient, manager
):
    conv = _new_conversation(client)
    path = f"/api/v2/conversations/{conv}/voice"
    upgrade = {"Connection": "Upgrade", "Upgrade": "websocket"}
    # Werkzeug routes upgrade requests as ws://; a plain GET does not match.
    assert client.get(path).status_code in (400, 404, 405)
    bad = client.get(f"{path}?ticket=not-a-ticket", headers=upgrade)
    assert bad.status_code == 401


class TestPerTurnTranscript:
    """Live calls post turns as they happen; each turn is added exactly once."""

    @staticmethod
    def _post(client: FlaskClient, conv: str, turns: list[dict]):
        return client.post(
            f"/api/v2/conversations/{conv}/transcript",
            json={"turns": turns, "call_metadata": {"call_sid": "webui-abc"}},
        )

    def test_turns_with_ids_append_incrementally(self, client: FlaskClient):
        conv = f"test-voice-{uuid.uuid4().hex[:8]}"
        first = self._post(
            client, conv, [{"role": "user", "text": "hi", "turn_id": "webui-abc:0"}]
        )
        assert first.get_json()["messages_added"] == 1
        second = self._post(
            client,
            conv,
            [
                {"role": "user", "text": "hi", "turn_id": "webui-abc:0"},
                {"role": "assistant", "text": "hello!", "turn_id": "webui-abc:1"},
            ],
        )
        assert second.get_json()["messages_added"] == 1
        again = self._post(
            client,
            conv,
            [{"role": "assistant", "text": "hello!", "turn_id": "webui-abc:1"}],
        )
        assert again.get_json() == {
            "status": "already_acked",
            "conversation_id": conv,
            "messages_added": 0,
            "messages_revised": 0,
        }
        log = client.get(f"/api/v2/conversations/{conv}").get_json()["log"]
        voice = [m for m in log if (m.get("metadata") or {}).get("voice_call")]
        assert [(m["role"], m["content"]) for m in voice] == [
            ("user", "hi"),
            ("assistant", "hello!"),
        ]
        assert voice[1]["metadata"]["voice_call"]["turn_id"] == "webui-abc:1"

    def test_revised_turn_is_updated_in_place(self, client: FlaskClient):
        """A provider revision (longer final text for the same turn) replaces the
        posted text instead of being dropped as already posted."""
        conv = f"test-voice-{uuid.uuid4().hex[:8]}"
        self._post(
            client, conv, [{"role": "user", "text": "Hello", "turn_id": "webui-abc:0"}]
        )
        self._post(
            client,
            conv,
            [{"role": "assistant", "text": "Hi!", "turn_id": "webui-abc:1"}],
        )
        resp = self._post(
            client,
            conv,
            [
                {
                    "role": "user",
                    "text": "Hello, use the other file",
                    "turn_id": "webui-abc:0",
                },
                {"role": "user", "text": "thanks", "turn_id": "webui-abc:2"},
            ],
        )
        assert resp.get_json()["messages_revised"] == 1
        assert resp.get_json()["messages_added"] == 1
        log = client.get(f"/api/v2/conversations/{conv}").get_json()["log"]
        voice = [
            m["content"] for m in log if (m.get("metadata") or {}).get("voice_call")
        ]
        assert voice == ["Hello, use the other file", "Hi!", "thanks"]

    def test_revision_reaches_full_history_under_a_compacted_view(
        self, client: FlaskClient
    ):
        """With a compacted view active, a revised turn is updated in the full
        history too, a turn compacted out of the view is not re-added, and no
        edit backup branch is created."""
        from gptme.logmanager import Log, LogManager

        conv = f"test-voice-{uuid.uuid4().hex[:8]}"
        self._post(
            client,
            conv,
            [
                {"role": "user", "text": "Hello", "turn_id": "webui-abc:0"},
                {"role": "assistant", "text": "Hi!", "turn_id": "webui-abc:1"},
            ],
        )
        with LogManager.load(conv, lock=True) as manager:
            kept = manager.log.messages[-1]
            manager.create_view("compacted-001", Log([Message("system", "sum"), kept]))
            manager.switch_view("compacted-001")
            manager.write()

        resp = self._post(
            client,
            conv,
            [
                {"role": "user", "text": "Hello", "turn_id": "webui-abc:0"},
                {"role": "assistant", "text": "Hi there!", "turn_id": "webui-abc:1"},
                {"role": "user", "text": "thanks", "turn_id": "webui-abc:2"},
            ],
        )
        assert resp.get_json()["messages_revised"] == 1
        assert resp.get_json()["messages_added"] == 1

        with LogManager.load(conv, lock=False) as manager:
            assert manager.current_view == "compacted-001"
            assert [m.content for m in manager.log] == ["sum", "Hi there!", "thanks"]
            assert [
                m.content
                for m in manager.history_log
                if (m.metadata or {}).get("voice_call")
            ] == ["Hello", "Hi there!", "thanks"]
            assert list(manager._branches) == ["main"]

    def test_turns_without_ids_keep_call_level_dedupe(self, client: FlaskClient):
        """Post-call promotion (no turn ids) is still deduplicated per call."""
        conv = f"test-voice-{uuid.uuid4().hex[:8]}"
        turns = [{"role": "user", "text": "hi"}]
        assert self._post(client, conv, turns).get_json()["messages_added"] == 1
        assert self._post(client, conv, turns).get_json()["status"] == "already_acked"

    def test_rejects_non_string_turn_id(self, client: FlaskClient):
        conv = f"test-voice-{uuid.uuid4().hex[:8]}"
        resp = self._post(client, conv, [{"role": "user", "text": "x", "turn_id": 3}])
        assert resp.status_code == 400

    def test_asks_to_retry_while_a_command_runs(self, client: FlaskClient, monkeypatch):
        from gptme.server.api_v2_sessions import SessionManager

        monkeypatch.setattr(
            SessionManager, "command_is_active", classmethod(lambda cls, c: True)
        )
        conv = f"test-voice-{uuid.uuid4().hex[:8]}"
        resp = self._post(
            client, conv, [{"role": "user", "text": "hi", "turn_id": "webui-abc:0"}]
        )
        assert resp.status_code == 409
        assert resp.get_json()["retry"] is True

    def test_asks_to_retry_while_generating(self, client: FlaskClient, monkeypatch):
        from gptme.server.api_v2_sessions import SessionManager

        monkeypatch.setattr(
            SessionManager, "conversation_generating", classmethod(lambda cls, c: True)
        )
        conv = f"test-voice-{uuid.uuid4().hex[:8]}"
        resp = self._post(
            client, conv, [{"role": "user", "text": "hi", "turn_id": "webui-abc:0"}]
        )
        assert resp.status_code == 409
        assert resp.get_json()["retry"] is True


def test_start_spawns_one_loopback_sidecar_per_configured_provider(
    tmp_path, monkeypatch
):
    """Sidecars get the secret, server URL, identity, gptme and model from the server."""
    spawned: list[tuple[list[str], dict[str, str]]] = []

    class _Proc:
        def poll(self):
            return None

    def _popen(args, env, stdin):
        spawned.append((args, env))
        return _Proc()

    monkeypatch.setattr(voice_api.subprocess, "Popen", _popen)
    monkeypatch.setattr(voice_api.atexit, "register", lambda fn: None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    for key in (
        "GPTME_VOICE_AGENT_NAME",
        "GPTME_VOICE_SUBAGENT_PATH",
        "GPTME_VOICE_SUBAGENT_MODEL",
        "GPTME_VOICE_SUBAGENT_MODEL_FAST",
        "GPTME_VOICE_SUBAGENT_MODEL_SMART",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(voice_api, "_env", lambda key: os.environ.get(key))

    mgr = VoiceManager(
        server_url="http://127.0.0.1:5700",
        server_token="tok",
        workspace=tmp_path,
        model="openrouter/some/model",
        command=["gptme-voice-server"],
    )
    mgr.start()

    assert list(mgr.sidecars) == ["openai"]
    assert mgr.unavailable == {"grok": "XAI_API_KEY not set"}
    (args, env) = spawned[0]
    assert args[:1] == ["gptme-voice-server"]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--provider") + 1] == "openai"
    assert "--enable-browser-transport" in args
    assert env["GPTME_VOICE_BROWSER_SECRET"] == mgr.secret
    assert env["GPTME_VOICE_GPTME_SERVER_URL"] == "http://127.0.0.1:5700"
    assert env["GPTME_VOICE_GPTME_SERVER_KEY"] == "tok"
    assert env["GPTME_VOICE_AGENT_NAME"] == "gptme"
    assert env["GPTME_VOICE_SUBAGENT_MODEL"] == "openrouter/some/model"


def test_start_without_provider_keys_reports_why(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_api, "_env", lambda key: None)
    monkeypatch.setattr(voice_api.atexit, "register", lambda fn: None)
    mgr = VoiceManager(
        server_url="http://x",
        server_token=None,
        workspace=tmp_path,
        command=["gptme-voice-server"],
    )
    mgr.start()
    status = mgr.status()
    assert status["available"] is False
    assert status["reason"] == "no realtime provider API key configured"


def test_workspace_agent_name(tmp_path):
    from gptme.server.voice_api import _workspace_agent_name

    assert _workspace_agent_name(tmp_path) is None
    (tmp_path / "gptme.toml").write_text('[agent]\nname = "Bob"\n')
    assert _workspace_agent_name(tmp_path) == "Bob"


def test_ticket_for_non_ascii_conversation_id(manager):
    ticket = manager.issue_ticket("café", "openai")
    assert manager.redeem_ticket(ticket, "café") is not None


def test_session_path_encodes_conversation_id(client, manager):
    conv = f"test-voice-{uuid.uuid4().hex[:6]}-a?b#c"
    from urllib.parse import quote

    assert (
        client.put(f"/api/v2/conversations/{quote(conv, safe='')}", json={}).status_code
        == 200
    )
    data = client.post(
        f"/api/v2/conversations/{quote(conv, safe='')}/voice/session", json={}
    ).get_json()
    assert data["path"] == f"/api/v2/conversations/{quote(conv, safe='')}/voice"


@pytest.mark.parametrize("body", [[1], "x", {"provider": []}, {"provider": 3}])
def test_session_rejects_malformed_body(client, manager, body):
    conv = _new_conversation(client)
    resp = client.post(f"/api/v2/conversations/{conv}/voice/session", json=body)
    assert resp.status_code == 400


def test_transcript_rejects_mixed_turn_id_batches(client):
    conv = f"test-voice-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        f"/api/v2/conversations/{conv}/transcript",
        json={
            "turns": [
                {"role": "user", "text": "a", "turn_id": "c:0"},
                {"role": "assistant", "text": "b"},
            ],
            "call_metadata": {"call_sid": "c"},
        },
    )
    assert resp.status_code == 400


def test_sidecar_health_is_cached_and_follows_the_process(monkeypatch):
    calls: list[str] = []

    class _Resp:
        ok = True

    def _get(url, timeout):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(voice_api.requests, "get", _get)
    proc = MagicMock()
    proc.poll.return_value = None
    sidecar = VoiceSidecar(provider="openai", port=6123, proc=proc)
    assert sidecar.healthy() is True
    assert sidecar.healthy() is True  # cached: one probe
    assert calls == ["http://127.0.0.1:6123/"]
    proc.poll.return_value = 1  # process exited
    assert sidecar.healthy() is False


def test_sidecar_health_reports_unreachable(monkeypatch):
    def _get(url, timeout):
        raise voice_api.requests.ConnectionError("refused")

    monkeypatch.setattr(voice_api.requests, "get", _get)
    proc = MagicMock()
    proc.poll.return_value = None
    assert VoiceSidecar(provider="grok", port=6124, proc=proc).healthy() is False


def test_stop_terminates_and_kills_stragglers(tmp_path):
    import subprocess

    polite, stubborn = MagicMock(), MagicMock()
    for proc in (polite, stubborn):
        proc.poll.return_value = None
    stubborn.wait.side_effect = subprocess.TimeoutExpired("gptme-voice-server", 5)
    mgr = VoiceManager(server_url="http://x", server_token=None, workspace=tmp_path)
    mgr.sidecars = {
        "openai": VoiceSidecar(provider="openai", port=1, proc=polite),
        "grok": VoiceSidecar(provider="grok", port=2, proc=stubborn),
    }
    mgr.stop()
    polite.terminate.assert_called_once()
    stubborn.terminate.assert_called_once()
    stubborn.kill.assert_called_once()
    polite.kill.assert_not_called()


def test_spawn_failure_marks_provider_unavailable(tmp_path, monkeypatch):
    def _popen(*args, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(voice_api.subprocess, "Popen", _popen)
    monkeypatch.setattr(voice_api.atexit, "register", lambda fn: None)
    monkeypatch.setattr(
        voice_api, "_env", lambda key: "sk-test" if key == "OPENAI_API_KEY" else None
    )
    mgr = VoiceManager(
        server_url="http://x",
        server_token=None,
        workspace=tmp_path,
        command=["missing-voice-server"],
    )
    mgr.start()
    assert mgr.sidecars == {}
    assert "failed to start gptme-voice" in mgr.unavailable["openai"]


def test_resolve_sidecar_command(monkeypatch):
    monkeypatch.setattr(
        voice_api,
        "_env",
        lambda key: (
            "uv run --project /x gptme-voice-server"
            if key == "GPTME_VOICE_SERVER_CMD"
            else None
        ),
    )
    assert voice_api._resolve_sidecar_command() == [
        "uv",
        "run",
        "--project",
        "/x",
        "gptme-voice-server",
    ]
    monkeypatch.setattr(voice_api, "_env", lambda key: None)
    monkeypatch.setattr(voice_api.shutil, "which", lambda name: None)
    assert voice_api._resolve_sidecar_command() is None


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("0.0.0.0", "http://127.0.0.1:5700"),
        ("localhost", "http://127.0.0.1:5700"),
        ("::", "http://[::1]:5700"),
        ("10.0.0.5", "http://10.0.0.5:5700"),
    ],
)
def test_init_voice_connects_sidecars_back_to_the_server(
    tmp_path, monkeypatch, host, expected
):
    monkeypatch.setattr(VoiceManager, "start", lambda self: None)
    monkeypatch.setattr(voice_api, "_manager", None)
    voice_api.init_voice(host=host, port=5700, token="t", workspace=tmp_path, model="m")
    mgr = voice_api.get_voice_manager()
    assert mgr is not None
    assert mgr.server_url == expected
    assert mgr.model == "m"


def test_websocket_route_tunnels_a_redeemed_ticket(client, manager, monkeypatch):
    import flask

    captured: dict = {}

    def _tunnel(port, subpath, environ, *, query=None, extra_headers=None):
        captured.update(
            port=port, subpath=subpath, query=query, extra_headers=extra_headers
        )
        return flask.Response(status=101)

    monkeypatch.setattr(voice_api, "_websocket_tunnel", _tunnel)
    conv = _new_conversation(client)
    ticket = manager.issue_ticket(conv, "openai")
    resp = client.get(
        f"/api/v2/conversations/{conv}/voice?ticket={ticket}",
        headers={"Connection": "Upgrade", "Upgrade": "websocket"},
    )
    assert resp.status_code == 101
    assert captured == {
        "port": 65000,
        "subpath": "voice",
        "query": f"conversation_id={conv}",
        "extra_headers": {voice_api.SECRET_HEADER: manager.secret},
    }

    # The sidecar process died after the ticket was issued.
    ticket = manager.issue_ticket(conv, "openai")
    manager.sidecars["openai"].proc.poll.return_value = 1
    dead = client.get(
        f"/api/v2/conversations/{conv}/voice?ticket={ticket}",
        headers={"Connection": "Upgrade", "Upgrade": "websocket"},
    )
    assert dead.status_code == 503


def test_tunnel_request_line_uses_explicit_query_and_injected_headers():
    import flask

    from gptme.server.preview_proxy_api import _build_upstream_request_line

    app = flask.Flask(__name__)
    with app.test_request_context(
        "/api/v2/conversations/c/voice?ticket=secret-ticket",
        headers={
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": "k",
            # A client copy of the injected header must not reach the sidecar.
            "X-Gptme-Voice-Secret": "forged",
        },
    ):
        raw = _build_upstream_request_line(
            7000,
            "voice",
            flask.request.environ,
            query="conversation_id=c",
            extra_headers={"X-Gptme-Voice-Secret": "real"},
        ).decode("latin-1")
    first_line = raw.split("\r\n", 1)[0]
    assert first_line.startswith("GET /voice?conversation_id=c ")
    assert "secret-ticket" not in raw
    assert "X-Gptme-Voice-Secret: real" in raw
    assert "forged" not in raw
    assert "sec-websocket-key: k" in raw.lower()
