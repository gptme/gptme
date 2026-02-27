"""Tests for ACP server session runtime wrapper and server-side integration."""

from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

if TYPE_CHECKING:
    from pathlib import Path

    from flask.testing import FlaskClient


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _DummyBlock:
    def __init__(self, text: str):
        self.text = text


class _DummyClient:
    def __init__(self, workspace: Path, **kwargs: Any) -> None:
        self.workspace = workspace
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        self.prompt_calls: list[tuple[str, str]] = []

    async def __aenter__(self):
        self.started = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True

    async def new_session(self, cwd: str | Path | None = None, **kwargs: Any) -> str:
        return "sess-test"

    async def prompt(self, session_id: str, message: str):
        self.prompt_calls.append((session_id, message))
        return SimpleNamespace(output=[_DummyBlock("hello"), _DummyBlock(" world")])


@pytest.mark.parametrize(
    ("resp", "expected"),
    [
        ({"output": [{"text": "abc"}, {"text": "123"}]}, "abc123"),
        (SimpleNamespace(output=[_DummyBlock("a"), _DummyBlock("b")]), "ab"),
        ({"text": "fallback"}, "fallback"),
        (SimpleNamespace(text="fallback2"), "fallback2"),
        ({"output": [{"no_text": True}]}, ""),
    ],
)
def test_extract_text_from_prompt_response(resp, expected):
    from gptme.server.acp_session_runtime import extract_text_from_prompt_response

    assert extract_text_from_prompt_response(resp) == expected


def test_runtime_lifecycle_and_prompt(monkeypatch, tmp_path):
    import gptme.server.acp_session_runtime as mod

    created: list[_DummyClient] = []

    def _factory(*args, **kwargs):
        c = _DummyClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(mod, "GptmeAcpClient", _factory)

    runtime = mod.AcpSessionRuntime(workspace=tmp_path)

    # Explicit start + prompt
    _run(runtime.start())
    assert runtime.session_id == "sess-test"
    assert len(created) == 1
    assert created[0].started is True

    text, raw = _run(runtime.prompt("hi there"))
    assert text == "hello world"
    assert created[0].prompt_calls == [("sess-test", "hi there")]
    assert raw is not None

    # Idempotent start should not create additional clients
    _run(runtime.start())
    assert len(created) == 1

    _run(runtime.close())
    assert created[0].closed is True
    assert runtime.session_id is None


def test_runtime_lazy_start_on_prompt(monkeypatch, tmp_path):
    import gptme.server.acp_session_runtime as mod

    created: list[_DummyClient] = []

    def _factory(*args, **kwargs):
        c = _DummyClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(mod, "GptmeAcpClient", _factory)

    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    text, _ = _run(runtime.prompt("start lazily"))

    assert text == "hello world"
    assert runtime.session_id == "sess-test"
    assert len(created) == 1


# ---------------------------------------------------------------------------
# Server-side integration: _acp_step + ConversationSession.use_acp routing
# ---------------------------------------------------------------------------


def _make_v2_conversation(client: FlaskClient, name: str | None = None) -> dict:
    """Create a V2 conversation and return {conversation_id, session_id}."""
    convname = name or f"test-acp-{random.randint(0, 1_000_000)}"
    resp = client.put(
        f"/api/v2/conversations/{convname}",
        json={"prompt": "You are a test assistant."},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    return {"conversation_id": convname, "session_id": data["session_id"]}


def test_acp_step_emits_events(monkeypatch, client: FlaskClient, tmp_path):
    """_acp_step() should emit generation_started + generation_complete events."""
    import gptme.server.acp_session_runtime as rt_mod
    import gptme.server.api_v2_sessions as sessions_mod

    created: list[_DummyClient] = []

    def _factory(*args, **kwargs):
        c = _DummyClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(rt_mod, "GptmeAcpClient", _factory)

    from gptme.logmanager import LogManager
    from gptme.server.acp_session_runtime import AcpSessionRuntime
    from gptme.server.api_v2_sessions import ConversationSession, SessionManager

    # Create a conversation via the API so LogManager can find it
    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]

    # Append a user message via the API
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "hello from acp test"},
    )
    assert resp.status_code == 200

    # Create a session backed by the dummy ACP runtime
    session = ConversationSession(id="sid-evt", conversation_id=conversation_id)
    session.use_acp = True
    session.acp_runtime = AcpSessionRuntime(workspace=tmp_path)
    SessionManager._sessions["sid-evt"] = session
    SessionManager._conversation_sessions[conversation_id].add("sid-evt")

    try:
        _run(sessions_mod._acp_step(conversation_id, session, tmp_path))

        event_types = [e["type"] for e in session.events]
        assert "generation_started" in event_types
        assert "generation_complete" in event_types

        # The ACP runtime should have been called with the user message
        assert len(created) == 1
        assert created[0].prompt_calls == [("sess-test", "hello from acp test")]

        # Assistant message should have been persisted
        manager = LogManager.load(conversation_id)
        assistant_msgs = [m for m in manager.log.messages if m.role == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].content == "hello world"
    finally:
        SessionManager._sessions.pop("sid-evt", None)
        SessionManager._conversation_sessions[conversation_id].discard("sid-evt")


@pytest.mark.timeout(10)
def test_use_acp_flag_in_step_request(monkeypatch, client: FlaskClient, tmp_path):
    """Posting use_acp=True to /step should create an ACP runtime and route through it."""
    import gptme.server.acp_session_runtime as rt_mod

    created: list[_DummyClient] = []

    def _factory(*args, **kwargs):
        c = _DummyClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(rt_mod, "GptmeAcpClient", _factory)

    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]
    session_id = conv["session_id"]

    # Send a user message first
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "hi from step test"},
    )
    assert resp.status_code == 200

    # Trigger a step with use_acp=True
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "use_acp": True},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert data.get("status") == "ok"

    # Verify the session now has use_acp=True and an ACP runtime attached
    from gptme.server.api_v2_sessions import SessionManager

    sess = SessionManager.get_session(session_id)
    assert sess is not None
    assert sess.use_acp is True
    assert sess.acp_runtime is not None
