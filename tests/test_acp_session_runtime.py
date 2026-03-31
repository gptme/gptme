"""Tests for ACP server session runtime wrapper and server-side integration."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

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
        self.set_model_calls: list[tuple[str, str]] = []
        self._on_update = kwargs.get("on_update")

    async def __aenter__(self):
        self.started = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True

    async def new_session(self, cwd: str | Path | None = None, **kwargs: Any) -> str:
        return "sess-test"

    async def prompt(self, session_id: str, message: str):
        self.prompt_calls.append((session_id, message))
        if self._on_update is not None:
            await self._on_update(session_id, {"text": "hello world"})
        return SimpleNamespace(output=[_DummyBlock("hello"), _DummyBlock(" world")])

    async def set_session_model(self, session_id: str, model_id: str) -> None:
        self.set_model_calls.append((session_id, model_id))

    def set_on_update(self, on_update):
        self._on_update = on_update


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


def test_runtime_sets_session_model_on_start(monkeypatch, tmp_path):
    import gptme.server.acp_session_runtime as mod

    created: list[_DummyClient] = []

    def _factory(*args, **kwargs):
        c = _DummyClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(mod, "GptmeAcpClient", _factory)

    runtime = mod.AcpSessionRuntime(workspace=tmp_path, model="openai/gpt-4o-mini")
    _run(runtime.start())

    assert runtime.session_id == "sess-test"
    assert len(created) == 1
    assert created[0].set_model_calls == [("sess-test", "openai/gpt-4o-mini")]


# ---------------------------------------------------------------------------
# Server-side integration: _acp_step + ConversationSession.use_acp routing
# ---------------------------------------------------------------------------


def _make_v2_conversation(client: FlaskClient, name: str | None = None) -> dict:
    """Create a V2 conversation and return {conversation_id, session_id}."""
    convname = name or f"test-acp-{uuid.uuid4().hex[:12]}"
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
    import gptme.server.session_step as sessions_mod

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


def test_use_acp_step_rejects_non_boolean_flag(client: FlaskClient):
    """/step should reject non-boolean use_acp values with 400."""
    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]
    session_id = conv["session_id"]

    # Send a user message first
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "hello"},
    )
    assert resp.status_code == 200

    # Non-boolean use_acp should fail validation
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "use_acp": "false"},
    )
    assert resp.status_code == 400

    data = resp.get_json()
    assert data is not None
    assert data.get("error") == "Invalid 'use_acp' value"


def test_step_rejects_invalid_auto_confirm_type(client: FlaskClient):
    """/step should reject non-bool/non-int auto_confirm values with 400."""
    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]
    session_id = conv["session_id"]

    # Send a user message first
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "hello"},
    )
    assert resp.status_code == 200

    # Non-bool/non-int auto_confirm should fail validation
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "auto_confirm": "true"},
    )
    assert resp.status_code == 400

    data = resp.get_json()
    assert data is not None
    assert data.get("error") == "Invalid 'auto_confirm' value"


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


def test_use_acp_step_forwards_model_to_runtime(
    monkeypatch, client: FlaskClient, tmp_path
):
    """When ACP mode is active, /step should update runtime model from request."""
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

    resp = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "model me"},
    )
    assert resp.status_code == 200

    # First call enables ACP and sets model from request
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={
            "session_id": session_id,
            "use_acp": True,
            "model": "openai/gpt-4o-mini",
        },
    )
    assert resp.status_code == 200

    from gptme.server.api_v2_sessions import SessionManager

    sess = SessionManager.get_session(session_id)
    assert sess is not None
    assert sess.acp_runtime is not None
    assert sess.acp_runtime.model == "openai/gpt-4o-mini"


def test_acp_step_rejects_duplicate_without_new_user_message(
    monkeypatch, client: FlaskClient, tmp_path
):
    """Calling ACP /step twice without a new user message should not re-send prompt."""
    import gptme.server.acp_session_runtime as rt_mod
    import gptme.server.session_step as sessions_mod

    created: list[_DummyClient] = []

    def _factory(*args, **kwargs):
        c = _DummyClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(rt_mod, "GptmeAcpClient", _factory)
    monkeypatch.setattr(sessions_mod, "trigger_hook", lambda *args, **kwargs: [])

    from gptme.server.acp_session_runtime import AcpSessionRuntime
    from gptme.server.api_v2_sessions import ConversationSession, SessionManager

    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]

    # Add one user message
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "hello once"},
    )
    assert resp.status_code == 200

    session = ConversationSession(id="sid-dupe", conversation_id=conversation_id)
    session.use_acp = True
    session.acp_runtime = AcpSessionRuntime(workspace=tmp_path)
    SessionManager._sessions["sid-dupe"] = session
    SessionManager._conversation_sessions[conversation_id].add("sid-dupe")

    try:
        # First run consumes pending user message(s)
        _run(sessions_mod._acp_step(conversation_id, session, tmp_path))

        prompt_calls_before = sum(len(c.prompt_calls) for c in created)

        # Second run with no new user message should fail fast and send no prompt
        _run(sessions_mod._acp_step(conversation_id, session, tmp_path))

        prompt_calls_after = sum(len(c.prompt_calls) for c in created)
        assert prompt_calls_after == prompt_calls_before
        assert session.events[-1]["type"] == "error"
        err = session.events[-1]
        assert "No new user message" in str(err)
    finally:
        SessionManager._sessions.pop("sid-dupe", None)
        SessionManager._conversation_sessions[conversation_id].discard("sid-dupe")


def test_acp_step_processes_all_pending_user_messages(
    monkeypatch, client: FlaskClient, tmp_path
):
    """ACP step should process all pending user messages in order."""
    import gptme.server.acp_session_runtime as rt_mod
    import gptme.server.session_step as sessions_mod

    created: list[_DummyClient] = []

    def _factory(*args, **kwargs):
        c = _DummyClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(rt_mod, "GptmeAcpClient", _factory)

    from gptme.server.acp_session_runtime import AcpSessionRuntime
    from gptme.server.api_v2_sessions import ConversationSession, SessionManager

    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]

    # Two user messages should both be processed in one ACP step call
    for content in ["first question", "second question"]:
        resp = client.post(
            f"/api/v2/conversations/{conversation_id}",
            json={"role": "user", "content": content},
        )
        assert resp.status_code == 200

    session = ConversationSession(id="sid-pending", conversation_id=conversation_id)
    session.use_acp = True
    session.acp_runtime = AcpSessionRuntime(workspace=tmp_path)
    SessionManager._sessions["sid-pending"] = session
    SessionManager._conversation_sessions[conversation_id].add("sid-pending")

    try:
        _run(sessions_mod._acp_step(conversation_id, session, tmp_path))

        all_calls = [call for c in created for call in c.prompt_calls]
        question_calls = [
            call
            for call in all_calls
            if call[1] in {"first question", "second question"}
        ]
        assert question_calls == [
            ("sess-test", "first question"),
            ("sess-test", "second question"),
        ]
        # Cursor should now point at the second user message index
        assert session.acp_last_user_msg_index == 1

        # A second run without a new user message should fail fast
        _run(sessions_mod._acp_step(conversation_id, session, tmp_path))
        assert session.events[-1]["type"] == "error"
        assert "No new user message" in str(session.events[-1])
    finally:
        SessionManager._sessions.pop("sid-pending", None)
        SessionManager._conversation_sessions[conversation_id].discard("sid-pending")


def test_acp_step_runs_session_start_step_pre_and_turn_post_hooks(
    monkeypatch, client: FlaskClient, tmp_path
):
    """ACP step should preserve key server-side hook semantics."""
    import gptme.server.acp_session_runtime as rt_mod
    import gptme.server.session_step as sessions_mod

    created: list[_DummyClient] = []

    def _factory(*args, **kwargs):
        c = _DummyClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(rt_mod, "GptmeAcpClient", _factory)

    from gptme.hooks import HookType
    from gptme.message import Message
    from gptme.server.acp_session_runtime import AcpSessionRuntime
    from gptme.server.api_v2_sessions import ConversationSession, SessionManager

    def _fake_trigger_hook(hook_type: HookType, **kwargs):
        if hook_type == HookType.SESSION_START:
            return [Message("system", "HOOK_SESSION_START")]
        if hook_type == HookType.STEP_PRE:
            return [Message("system", "HOOK_STEP_PRE")]
        if hook_type == HookType.TURN_POST:
            return [Message("system", "HOOK_TURN_POST")]
        return []

    monkeypatch.setattr(sessions_mod, "trigger_hook", _fake_trigger_hook)

    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]

    # One user message to trigger one ACP prompt
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "hello hooks"},
    )
    assert resp.status_code == 200

    session = ConversationSession(id="sid-hooks", conversation_id=conversation_id)
    session.use_acp = True
    session.acp_runtime = AcpSessionRuntime(workspace=tmp_path)
    SessionManager._sessions["sid-hooks"] = session
    SessionManager._conversation_sessions[conversation_id].add("sid-hooks")

    try:
        _run(sessions_mod._acp_step(conversation_id, session, tmp_path))

        assert len(created) == 1
        assert created[0].prompt_calls == [("sess-test", "hello hooks")]

        from gptme.logmanager import LogManager

        manager = LogManager.load(conversation_id)
        contents = [m.content for m in manager.log.messages]
        assert "HOOK_SESSION_START" in contents
        assert "HOOK_STEP_PRE" in contents
        assert "HOOK_TURN_POST" in contents
    finally:
        SessionManager._sessions.pop("sid-hooks", None)
        SessionManager._conversation_sessions[conversation_id].discard("sid-hooks")


def test_iter_text_from_acp_update_shapes():
    import gptme.server.session_step as sessions_mod

    assert list(sessions_mod._iter_text_from_acp_update("abc")) == ["abc"]
    assert list(sessions_mod._iter_text_from_acp_update({"text": "abc"})) == ["abc"]
    assert list(
        sessions_mod._iter_text_from_acp_update(
            {"message": {"content": [{"text": "a"}, {"text": "b"}]}}
        )
    ) == ["a", "b"]
    assert list(
        sessions_mod._iter_text_from_acp_update({"content": [{"text": "x"}]})
    ) == ["x"]


def test_acp_step_bridges_generation_progress_events(
    monkeypatch, client: FlaskClient, tmp_path
):
    """ACP session_update text should be bridged to generation_progress SSE events."""
    import gptme.server.acp_session_runtime as rt_mod
    import gptme.server.session_step as sessions_mod

    created: list[_DummyClient] = []

    def _factory(*args, **kwargs):
        c = _DummyClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(rt_mod, "GptmeAcpClient", _factory)

    from gptme.server.acp_session_runtime import AcpSessionRuntime
    from gptme.server.api_v2_sessions import ConversationSession, SessionManager

    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]

    resp = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "hello stream bridge"},
    )
    assert resp.status_code == 200

    session = ConversationSession(id="sid-stream", conversation_id=conversation_id)
    session.use_acp = True
    session.acp_runtime = AcpSessionRuntime(workspace=tmp_path)
    SessionManager._sessions["sid-stream"] = session
    SessionManager._conversation_sessions[conversation_id].add("sid-stream")

    try:
        _run(sessions_mod._acp_step(conversation_id, session, tmp_path))

        progress_tokens: list[str] = []
        for event in session.events:
            if event.get("type") != "generation_progress":
                continue
            token = cast(dict[str, Any], event).get("token")
            if isinstance(token, str):
                progress_tokens.append(token)

        assert "".join(progress_tokens) == "hello world"
        # Each SSE token should be a complete text chunk, not individual characters.
        # The dummy client sends one {"text": "hello world"} update, so we expect
        # exactly one progress token — not 11 single-character tokens.
        assert progress_tokens == ["hello world"], (
            f"Expected chunk-level tokens, got character-level: {progress_tokens}"
        )

        completion_events = [
            e for e in session.events if e.get("type") == "generation_complete"
        ]
        assert completion_events
        completion = cast(dict[str, Any], completion_events[-1])
        message_dict = cast(dict[str, Any], completion["message"])
        final_content = message_dict["content"]
        if isinstance(final_content, list):
            final_text = "".join(
                block.get("text", "")
                for block in final_content
                if isinstance(block, dict)
            )
        else:
            final_text = str(final_content)
        assert final_text == "hello world"
    finally:
        SessionManager._sessions.pop("sid-stream", None)
        SessionManager._conversation_sessions[conversation_id].discard("sid-stream")


# ---------------------------------------------------------------------------
# GPTME_USE_ACP_DEFAULT config tests
# ---------------------------------------------------------------------------


def test_use_acp_default_env_var_enables_acp(
    monkeypatch, client: FlaskClient, tmp_path
):
    """GPTME_USE_ACP_DEFAULT=true should cause ACP mode by default (no use_acp in request)."""
    import gptme.server.acp_session_runtime as rt_mod

    created: list[_DummyClient] = []

    def _factory(*args, **kwargs):
        c = _DummyClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(rt_mod, "GptmeAcpClient", _factory)
    monkeypatch.setenv("GPTME_USE_ACP_DEFAULT", "true")

    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]
    session_id = conv["session_id"]

    # Send a user message
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "hello default acp"},
    )
    assert resp.status_code == 200

    # Step WITHOUT explicit use_acp — should default to True from env var
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id},
    )
    assert resp.status_code == 200

    from gptme.server.api_v2_sessions import SessionManager

    sess = SessionManager.get_session(session_id)
    assert sess is not None
    assert sess.use_acp is True
    assert sess.acp_runtime is not None


def test_explicit_use_acp_false_overrides_default(
    monkeypatch, client: FlaskClient, tmp_path
):
    """Explicit use_acp=False in request should override GPTME_USE_ACP_DEFAULT=true."""
    monkeypatch.setenv("GPTME_USE_ACP_DEFAULT", "true")

    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]
    session_id = conv["session_id"]

    # Send a user message
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "hello override"},
    )
    assert resp.status_code == 200

    # Step with explicit use_acp=False — should NOT enable ACP despite env var
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "use_acp": False, "model": "openai/gpt-4o"},
    )
    assert resp.status_code == 200

    from gptme.server.api_v2_sessions import SessionManager

    sess = SessionManager.get_session(session_id)
    assert sess is not None
    assert sess.use_acp is False


def test_without_env_var_default_remains_false(
    monkeypatch, client: FlaskClient, tmp_path
):
    """Without GPTME_USE_ACP_DEFAULT, use_acp should default to False (non-ACP)."""
    monkeypatch.delenv("GPTME_USE_ACP_DEFAULT", raising=False)
    monkeypatch.delenv("USE_ACP_DEFAULT", raising=False)
    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]
    session_id = conv["session_id"]

    # Send a user message
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "hello no env"},
    )
    assert resp.status_code == 200

    # Step without use_acp and no env var — should use non-ACP path
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "model": "openai/gpt-4o"},
    )
    assert resp.status_code == 200

    from gptme.server.api_v2_sessions import SessionManager

    sess = SessionManager.get_session(session_id)
    assert sess is not None
    assert sess.use_acp is False


# ---------------------------------------------------------------------------
# ACP Health Monitoring Tests
# ---------------------------------------------------------------------------


def test_runtime_is_subprocess_alive(monkeypatch, tmp_path):
    """is_subprocess_alive reports False when no client / dead subprocess."""
    import gptme.server.acp_session_runtime as mod

    runtime = mod.AcpSessionRuntime(workspace=tmp_path)

    # No client yet → not alive
    assert runtime.is_subprocess_alive() is False
    assert runtime.process_pid is None

    # Mock a client with a process that has poll() returning None (alive)
    class _MockProcess:
        pid = 12345

        def poll(self):
            return None  # Still running

    class _MockClient:
        _process = _MockProcess()

    runtime._client = _MockClient()  # type: ignore[assignment]
    assert runtime.is_subprocess_alive() is True
    assert runtime.process_pid == 12345

    # Process died (poll returns exit code)
    class _DeadProcess:
        pid = 12345

        def poll(self) -> int:
            return 1

    class _DeadClient:
        _process = _DeadProcess()

    runtime._client = _DeadClient()  # type: ignore[assignment]
    assert runtime.is_subprocess_alive() is False


def test_health_check_cleans_dead_subprocess(monkeypatch, tmp_path):
    """_run_health_check removes sessions with dead ACP subprocesses."""
    import gptme.server.acp_session_runtime as rt_mod
    from gptme.server.api_v2_sessions import (
        ConversationSession,
        SessionManager,
        _run_health_check,
    )

    # Create a session with a dead ACP runtime
    session = ConversationSession(id="dead-session", conversation_id="conv-dead")
    session.use_acp = True
    runtime = rt_mod.AcpSessionRuntime(workspace=tmp_path)

    # Mock client with dead process
    runtime._client = type(  # type: ignore[assignment]
        "C",
        (),
        {"_process": type("P", (), {"pid": 99, "poll": lambda self: 1})()},
    )()
    session.acp_runtime = runtime

    SessionManager._sessions["dead-session"] = session
    SessionManager._conversation_sessions["conv-dead"].add("dead-session")

    try:
        _run_health_check()

        # Session should have been removed
        assert "dead-session" not in SessionManager._sessions
    finally:
        # Cleanup in case test failed
        SessionManager._sessions.pop("dead-session", None)
        SessionManager._conversation_sessions.pop("conv-dead", None)


def test_health_check_skips_generating_sessions(monkeypatch, tmp_path):
    """_run_health_check should not disturb sessions that are generating."""
    import gptme.server.acp_session_runtime as rt_mod
    from gptme.server.api_v2_sessions import (
        ConversationSession,
        SessionManager,
        _run_health_check,
    )

    session = ConversationSession(id="gen-session", conversation_id="conv-gen")
    session.use_acp = True
    session.generating = True  # Actively generating
    runtime = rt_mod.AcpSessionRuntime(workspace=tmp_path)

    # Even with dead process, should not be cleaned while generating
    runtime._client = type(  # type: ignore[assignment]
        "C",
        (),
        {"_process": type("P", (), {"pid": 88, "poll": lambda self: 1})()},
    )()
    session.acp_runtime = runtime

    SessionManager._sessions["gen-session"] = session
    SessionManager._conversation_sessions["conv-gen"].add("gen-session")

    try:
        _run_health_check()

        # Session should still be present (generating flag protects it)
        assert "gen-session" in SessionManager._sessions
    finally:
        SessionManager._sessions.pop("gen-session", None)
        SessionManager._conversation_sessions.pop("conv-gen", None)


def test_health_monitor_start_stop():
    """start/stop_acp_health_monitor should be safe to call."""
    from gptme.server.api_v2_sessions import (
        start_acp_health_monitor,
        stop_acp_health_monitor,
    )

    # Start with short interval for testing
    start_acp_health_monitor(interval=1)

    # Starting again should be a no-op
    start_acp_health_monitor(interval=1)

    # Stop should clean up
    stop_acp_health_monitor()
