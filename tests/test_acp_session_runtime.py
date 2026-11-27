"""Tests for ACP server session runtime wrapper and server-side integration."""

from __future__ import annotations

import asyncio
import contextvars
import sys
import threading
import time
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


@pytest.fixture(autouse=True)
def _stop_acp_runtime_loops(monkeypatch):
    """Stop the dedicated event-loop thread of every runtime a test creates.

    Many tests never close their runtime; without this each would leak an
    ``acp-runtime-loop`` daemon thread, and a runtime whose test failed before
    its ``finally`` would also leave a real ACP subprocess running.
    """
    from gptme.server.acp_session_runtime import AcpSessionRuntime

    runtimes: list[AcpSessionRuntime] = []
    orig_init = AcpSessionRuntime.__init__

    def _tracking_init(self, *args: Any, **kwargs: Any) -> None:
        orig_init(self, *args, **kwargs)
        runtimes.append(self)

    monkeypatch.setattr(AcpSessionRuntime, "__init__", _tracking_init)
    yield
    for rt in runtimes:
        # terminate_subprocess_sync closes the client (when the loop is still
        # alive) and stops the loop, so a subprocess started by a test that
        # failed early does not outlive the suite.
        try:
            rt.terminate_subprocess_sync(timeout=1.0)
        except Exception:
            pass
        finally:
            # terminate_subprocess_sync returns early when there is no client,
            # so always stop the loop explicitly.
            rt._stop_loop()


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
        self.prompt_max_tokens: list[int | None] = []
        self.set_model_calls: list[tuple[str, str]] = []
        self._on_update = kwargs.get("on_update")

    async def __aenter__(self):
        self.started = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True

    async def new_session(self, cwd: str | Path | None = None, **kwargs: Any) -> str:
        return "sess-test"

    async def prompt(
        self, session_id: str, message: str, max_tokens: int | None = None
    ):
        self.prompt_calls.append((session_id, message))
        self.prompt_max_tokens.append(max_tokens)
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
    with SessionManager._lock:
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
        with SessionManager._lock:
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


def test_use_acp_step_forwards_max_tokens_to_runtime(
    monkeypatch, client: FlaskClient, tmp_path
):
    """ACP steps must forward the request-scoped max_tokens override to prompt()."""
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
        json={"role": "user", "content": "limit me"},
    )
    assert resp.status_code == 200

    resp = client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={
            "session_id": session_id,
            "use_acp": True,
            "max_tokens": 64,
        },
    )
    assert resp.status_code == 200

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if created and created[0].prompt_calls:
            break
        time.sleep(0.01)

    assert created, "ACP client was never created"
    assert created[0].prompt_calls, "ACP prompt was never called"
    assert created[0].prompt_max_tokens == [64]


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
    with SessionManager._lock:
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
        # The error is followed by step_complete so strict clients see the release.
        assert session.events[-1]["type"] == "step_complete"
        assert not session.generating
        err = session.events[-2]
        assert err["type"] == "error"
        assert "No new user message" in str(err)
    finally:
        with SessionManager._lock:
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
    with SessionManager._lock:
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
        assert session.events[-1]["type"] == "step_complete"
        assert session.events[-2]["type"] == "error"
        assert "No new user message" in str(session.events[-2])
    finally:
        with SessionManager._lock:
            SessionManager._sessions.pop("sid-pending", None)
            SessionManager._conversation_sessions[conversation_id].discard(
                "sid-pending"
            )


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

    from gptme.hooks import HookType, current_conversation_id, current_session_id
    from gptme.message import Message
    from gptme.server.acp_session_runtime import AcpSessionRuntime
    from gptme.server.api_v2_sessions import ConversationSession, SessionManager

    hook_contexts: list[tuple[HookType, str | None, str | None]] = []

    def _fake_trigger_hook(hook_type: HookType, **kwargs):
        hook_contexts.append(
            (
                hook_type,
                current_conversation_id.get(),
                current_session_id.get(),
            )
        )
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
    with SessionManager._lock:
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
        assert hook_contexts == [
            (HookType.SESSION_START, conversation_id, "sid-hooks"),
            (HookType.STEP_PRE, conversation_id, "sid-hooks"),
            (HookType.TURN_POST, conversation_id, "sid-hooks"),
        ]
        assert current_conversation_id.get() is None
        assert current_session_id.get() is None
    finally:
        with SessionManager._lock:
            SessionManager._sessions.pop("sid-hooks", None)
            SessionManager._conversation_sessions[conversation_id].discard("sid-hooks")


def test_start_acp_step_thread_propagates_contextvars(monkeypatch, tmp_path):
    import gptme.server.session_step as sessions_mod
    from gptme.hooks import current_conversation_id, current_session_id
    from gptme.server.api_v2_sessions import ConversationSession

    caller_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
        "caller_var", default=None
    )
    seen: dict[str, str | None] = {}
    done = threading.Event()

    async def _fake_acp_step(
        conversation_id: str,
        session,
        workspace,
        step_seq: int | None = None,
        max_tokens: int | None = None,
        **_kwargs: object,
    ) -> None:
        seen["caller_var"] = caller_var.get()
        seen["conversation_id"] = current_conversation_id.get()
        seen["session_id"] = current_session_id.get()
        session.generating = False
        session.generating_since = None
        done.set()

    monkeypatch.setattr(sessions_mod, "_acp_step", _fake_acp_step)

    session = ConversationSession(id="sid-thread", conversation_id="conv-thread")

    token = caller_var.set("copied")
    try:
        sessions_mod._start_acp_step_thread("conv-thread", session, tmp_path)
        assert done.wait(timeout=2), "ACP thread did not complete"
    finally:
        caller_var.reset(token)

    assert seen == {
        "caller_var": "copied",
        "conversation_id": "conv-thread",
        "session_id": "sid-thread",
    }


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
    with SessionManager._lock:
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
        with SessionManager._lock:
            SessionManager._sessions.pop("sid-stream", None)
            SessionManager._conversation_sessions[conversation_id].discard("sid-stream")


@pytest.mark.timeout(15)
def test_acp_step_last_error_set_on_failure(monkeypatch, client: FlaskClient, tmp_path):
    """ACP failures should set session.last_error and clear generating."""
    import gptme.server.acp_session_runtime as rt_mod
    from gptme.server.api_v2_sessions import SessionManager

    class _FailingClient(_DummyClient):
        async def prompt(
            self, session_id: str, message: str, max_tokens: int | None = None
        ):
            raise RuntimeError("ACP runtime exploded")

    monkeypatch.setattr(rt_mod, "GptmeAcpClient", _FailingClient)

    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]
    session_id = conv["session_id"]

    resp = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "trigger acp failure"},
    )
    assert resp.status_code == 200

    resp = client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "use_acp": True},
    )
    assert resp.status_code == 500
    data = resp.get_json()
    assert data is not None
    assert data["status"] == "error"
    assert "ACP runtime exploded" in data["error"]

    deadline = time.time() + 2.0
    while time.time() < deadline:
        sess = SessionManager.get_session(session_id)
        if sess is not None and not sess.generating:
            break
        time.sleep(0.05)

    sess = SessionManager.get_session(session_id)
    assert sess is not None
    assert sess.last_error == "ACP runtime exploded"
    assert not sess.generating


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
    # May return 500 if no API key (LLM errors are now surfaced).
    # This test verifies use_acp config, not the step response code.
    assert resp.status_code in (200, 500)

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
    # Note: may return 500 if no API key is available (LLM error is now surfaced
    # in the HTTP response instead of being swallowed), but that's fine — this test
    # only checks that use_acp defaults to False.
    resp = client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "model": "openai/gpt-4o"},
    )
    assert resp.status_code in (200, 500)

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
    runtime._client = type(
        "C",
        (),
        {"_process": type("P", (), {"pid": 99, "poll": lambda self: 1})()},
    )()
    session.acp_runtime = runtime

    with SessionManager._lock:
        SessionManager._sessions["dead-session"] = session
        SessionManager._conversation_sessions["conv-dead"].add("dead-session")

    try:
        _run_health_check()

        # Session should have been removed
        assert "dead-session" not in SessionManager._sessions
    finally:
        # Cleanup in case test failed
        with SessionManager._lock:
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
    runtime._client = type(
        "C",
        (),
        {"_process": type("P", (), {"pid": 88, "poll": lambda self: 1})()},
    )()
    session.acp_runtime = runtime

    with SessionManager._lock:
        SessionManager._sessions["gen-session"] = session
        SessionManager._conversation_sessions["conv-gen"].add("gen-session")

    try:
        _run_health_check()

        # Session should still be present (generating flag protects it)
        assert "gen-session" in SessionManager._sessions
    finally:
        with SessionManager._lock:
            SessionManager._sessions.pop("gen-session", None)
            SessionManager._conversation_sessions.pop("conv-gen", None)


def test_health_monitor_start_stop():
    """start/stop_session_health_monitor should be safe to call."""
    from gptme.server.api_v2_sessions import (
        start_session_health_monitor,
        stop_session_health_monitor,
    )

    # Start with short interval for testing
    start_session_health_monitor(interval=1)
    try:
        # Starting again should be a no-op
        start_session_health_monitor(interval=1)
    finally:
        # Stop should clean up; always run so the thread never leaks on failure
        stop_session_health_monitor()


# ---------------------------------------------------------------------------
# Regression: runtime must survive across per-step asyncio.run loops (#5177)
# ---------------------------------------------------------------------------

_ECHO_AGENT = (
    "import sys\n"
    "for line in sys.stdin:\n"
    "    sys.stdout.write('echo:' + line)\n"
    "    sys.stdout.flush()\n"
)


class _LoopBoundClient:
    """Fake ACP client whose transport is bound to the loop that started it.

    Mirrors ``acp.stdio.spawn_agent_process``: an asyncio subprocess held open
    by an async-generator context manager. Using it from a different (or
    closed) event loop than the one that entered it breaks, like the real one.
    """

    def __init__(self, workspace: Path, **kwargs: Any) -> None:
        self._on_update = kwargs.get("on_update")
        self._ctx: Any = None
        self._process: Any = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.closed = False

    async def __aenter__(self):
        import sys
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _spawn():
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _ECHO_AGENT,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
            )
            try:
                yield proc
            finally:
                if proc.returncode is None:
                    proc.terminate()
                    await proc.wait()

        self.loop = asyncio.get_running_loop()
        self._ctx = _spawn()
        self._process = await self._ctx.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._ctx is not None:
            await self._ctx.__aexit__(exc_type, exc, tb)
        self._ctx = None
        self.closed = True

    async def new_session(self, cwd: str | Path | None = None, **kwargs: Any) -> str:
        return "sess-loop"

    async def prompt(
        self, session_id: str, message: str, max_tokens: int | None = None
    ):
        assert asyncio.get_running_loop() is self.loop, "used from foreign loop"
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        self._process.stdin.write(message.replace("\n", " ").encode() + b"\n")
        await self._process.stdin.drain()
        line = await asyncio.wait_for(self._process.stdout.readline(), 10)
        if not line:
            raise ConnectionError("Connection closed")
        return {"text": line.decode().rstrip("\n")}

    async def set_session_model(self, session_id: str, model_id: str) -> None:
        pass

    def set_on_update(self, on_update):
        self._on_update = on_update


def test_runtime_survives_across_event_loops(monkeypatch, tmp_path):
    """Each server step runs in its own asyncio.run(); the ACP subprocess and
    its transport must outlive those loops (#5177)."""
    import gptme.server.acp_session_runtime as mod

    created: list[_LoopBoundClient] = []

    def _factory(*args, **kwargs):
        c = _LoopBoundClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(mod, "GptmeAcpClient", _factory)

    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    try:
        for i in range(3):
            text, _ = _run(runtime.prompt(f"turn {i}"))
            assert text == f"echo:turn {i}"
            assert runtime.is_subprocess_alive()

        # One subprocess served all turns.
        assert len(created) == 1
        proc = created[0]._process
        loop_thread = runtime._loop_thread
    finally:
        _run(runtime.close())

    assert created[0].closed is True
    assert proc.returncode is not None
    assert not runtime.is_subprocess_alive()
    # The dedicated loop thread is stopped (joined, not just forgotten) on close.
    assert runtime._loop_thread is None
    assert loop_thread is not None and not loop_thread.is_alive()


def test_runtime_terminate_sync_closes_gracefully(monkeypatch, tmp_path):
    """terminate_subprocess_sync() closes the client on the runtime loop."""
    import gptme.server.acp_session_runtime as mod

    created: list[_LoopBoundClient] = []

    def _factory(*args, **kwargs):
        c = _LoopBoundClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(mod, "GptmeAcpClient", _factory)

    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    _run(runtime.start())
    proc = created[0]._process
    loop_thread = runtime._loop_thread
    assert runtime.is_subprocess_alive()

    runtime.terminate_subprocess_sync()

    assert created[0].closed is True
    assert proc.returncode is not None
    assert runtime.session_id is None
    assert runtime._loop_thread is None
    assert loop_thread is not None and not loop_thread.is_alive()


def test_health_check_releases_loop_thread_of_dead_runtime(monkeypatch, tmp_path):
    """A dead ACP subprocess must not leave the runtime's loop thread running."""
    import gptme.server.acp_session_runtime as rt_mod
    from gptme.server.api_v2_sessions import (
        ConversationSession,
        SessionManager,
        _run_health_check,
    )

    session = ConversationSession(id="dead-loop", conversation_id="conv-dead-loop")
    session.use_acp = True
    runtime = rt_mod.AcpSessionRuntime(workspace=tmp_path)
    runtime._ensure_loop()
    loop_thread = runtime._loop_thread
    assert loop_thread is not None and loop_thread.is_alive()
    runtime._client = type(
        "C",
        (),
        {"_process": type("P", (), {"pid": 99, "poll": lambda self: 1})()},
    )()
    session.acp_runtime = runtime

    with SessionManager._lock:
        SessionManager._sessions["dead-loop"] = session
        SessionManager._conversation_sessions["conv-dead-loop"].add("dead-loop")
    try:
        _run_health_check()
        assert "dead-loop" not in SessionManager._sessions
        loop_thread.join(timeout=5)
        assert not loop_thread.is_alive()
    finally:
        with SessionManager._lock:
            SessionManager._sessions.pop("dead-loop", None)
            SessionManager._conversation_sessions.pop("conv-dead-loop", None)
        runtime._stop_loop()


def test_health_check_releases_runtime_when_session_removal_fails(
    monkeypatch, tmp_path
):
    """A failing remove_session() must not leave the detached runtime running."""
    import gptme.server.acp_session_runtime as rt_mod
    from gptme.server.api_v2_sessions import (
        ConversationSession,
        SessionManager,
        _run_health_check,
    )

    session = ConversationSession(id="dead-rm", conversation_id="conv-dead-rm")
    session.use_acp = True
    runtime = rt_mod.AcpSessionRuntime(workspace=tmp_path)
    runtime._ensure_loop()
    loop_thread = runtime._loop_thread
    assert loop_thread is not None and loop_thread.is_alive()
    runtime._client = type(
        "C",
        (),
        {"_process": type("P", (), {"pid": 99, "poll": lambda self: 1})()},
    )()
    session.acp_runtime = runtime

    def failing_remove(session_id):
        raise OSError("cannot recreate logs dir")

    monkeypatch.setattr(SessionManager, "remove_session", failing_remove)
    with SessionManager._lock:
        SessionManager._sessions["dead-rm"] = session
        SessionManager._conversation_sessions["conv-dead-rm"].add("dead-rm")
    try:
        with pytest.raises(OSError, match="cannot recreate logs dir"):
            _run_health_check()
        loop_thread.join(timeout=5)
        assert not loop_thread.is_alive()
    finally:
        with SessionManager._lock:
            SessionManager._sessions.pop("dead-rm", None)
            SessionManager._conversation_sessions.pop("conv-dead-rm", None)
        runtime._stop_loop()


def test_call_on_stopped_loop_raises_instead_of_hanging(tmp_path):
    """A caller that obtained the loop just before shutdown gets an error."""
    import gptme.server.acp_session_runtime as mod

    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    stale = runtime._ensure_loop_calls()
    loop_thread = runtime._loop_thread
    runtime._stop_loop()
    assert loop_thread is not None and not loop_thread.is_alive()
    runtime._ensure_loop_calls = lambda: stale  # type: ignore[method-assign]

    async def work() -> int:
        return 1

    with pytest.raises(RuntimeError):
        _run(asyncio.wait_for(runtime._run_on_loop(work), 5))


@pytest.mark.timeout(20)
@pytest.mark.parametrize("sync_close", [False, True])
@pytest.mark.parametrize("resume_before_cancel", [False, True])
def test_stop_loop_releases_queued_call_before_loop_thread_exits(
    monkeypatch, tmp_path, sync_close, resume_before_cancel
):
    """A blocked transport callback must not keep a queued caller waiting."""
    import gptme.server.acp_session_runtime as mod

    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    loop = runtime._ensure_loop()
    loop_thread = runtime._loop_thread
    blocked = threading.Event()
    release = threading.Event()
    submitted = threading.Event()
    completed = threading.Event()
    ran = threading.Event()
    errors: list[BaseException] = []
    submit = asyncio.run_coroutine_threadsafe
    drained = threading.Event()

    def block_loop() -> None:
        blocked.set()
        release.wait(15)

    def track_submit(coro, target_loop):
        future = submit(coro, target_loop)
        cancel = future.cancel

        def cancel_after_loop_resumes():
            # Force the loop to resume after admission closes but before the
            # concurrent future's cancellation reaches its queued task.
            release.set()
            loop.call_soon_threadsafe(loop.call_soon, drained.set)
            assert drained.wait(5)
            return cancel()

        if resume_before_cancel:
            monkeypatch.setattr(future, "cancel", cancel_after_loop_resumes)
        submitted.set()
        return future

    async def work() -> None:
        ran.set()

    monkeypatch.setattr(runtime, "_close_impl", work)

    def caller() -> None:
        try:
            if sync_close:
                if not runtime._close_on_loop_sync(timeout=60):
                    raise ConnectionError("close cancelled; process fallback required")
            else:
                _run(runtime._run_on_loop(work))
        except BaseException as exc:
            errors.append(exc)
        finally:
            completed.set()

    loop.call_soon_threadsafe(block_loop)
    thread = threading.Thread(target=caller, daemon=True)
    try:
        assert blocked.wait(5)
        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", track_submit)
        thread.start()
        assert submitted.wait(5)
        runtime._stop_loop(join_timeout=0)
        assert completed.wait(2), "Caller waits for the blocked runtime thread to exit"
        if not resume_before_cancel:
            assert loop_thread is not None and loop_thread.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], ConnectionError)
        assert not ran.is_set()
    finally:
        release.set()
        thread.join(5)
        if loop_thread is not None:
            loop_thread.join(5)
        runtime._stop_loop()
    assert loop_thread is not None and not loop_thread.is_alive()
    assert not ran.is_set(), "Rejected work ran after the runtime loop resumed"


def test_runtime_call_from_own_loop(monkeypatch, tmp_path):
    """A callback already on the transport loop can await another runtime call."""
    import gptme.server.acp_session_runtime as mod

    monkeypatch.setattr(mod, "GptmeAcpClient", _DummyClient)
    runtime = mod.AcpSessionRuntime(workspace=tmp_path)

    async def nested_prompt():
        return await runtime.prompt("nested")

    text, _ = _run(asyncio.wait_for(runtime._run_on_loop(nested_prompt), 5))
    assert text == "hello world"
    _run(runtime.close())


@pytest.mark.timeout(15)
def test_failed_close_on_runtime_loop_does_not_join_itself(monkeypatch, tmp_path):
    """The own-loop close fallback signals the child without a self-join."""
    import gptme.server.acp_session_runtime as mod

    class _FailingExitClient(_DummyClient):
        _process = object()

        async def __aexit__(self, exc_type, exc, tb):
            raise OSError("broken transport")

    monkeypatch.setattr(mod, "GptmeAcpClient", _FailingExitClient)
    signalled: list[Any] = []
    monkeypatch.setattr(
        mod,
        "_terminate_asyncio_process_sync",
        lambda proc, timeout: signalled.append(proc),
    )
    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    _run(runtime.start())
    loop = runtime._ensure_loop()
    loop_thread = runtime._loop_thread
    completed = threading.Event()
    errors: list[BaseException] = []

    async def close_from_runtime() -> None:
        try:
            await runtime.close()
        except BaseException as exc:
            errors.append(exc)
        finally:
            completed.set()

    asyncio.run_coroutine_threadsafe(close_from_runtime(), loop)
    assert completed.wait(5)
    assert loop_thread is not None
    loop_thread.join(5)
    assert not loop_thread.is_alive()
    assert errors == []
    assert signalled == [_FailingExitClient._process]
    assert runtime._client is None


def test_runtime_rejected_submission_closes_coroutine(monkeypatch, tmp_path):
    """A loop closed during submission must not leave an unawaited coroutine."""
    import inspect

    import gptme.server.acp_session_runtime as mod

    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    submitted = []

    def reject(coro, loop):
        submitted.append(coro)
        raise RuntimeError("Event loop is closed")

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", reject)
    with pytest.raises(RuntimeError, match="Event loop is closed"):
        _run(runtime.start())

    assert len(submitted) == 1
    assert inspect.getcoroutinestate(submitted[0]) == inspect.CORO_CLOSED
    assert runtime.session_id is None


@pytest.mark.timeout(15)
def test_runtime_cancelled_sync_close_uses_process_fallback(monkeypatch, tmp_path):
    """Cancelling graceful close must release its waiter and still signal the child."""
    import gptme.server.acp_session_runtime as mod

    closing = threading.Event()

    class _BlockingCloseClient(_DummyClient):
        _process = object()

        async def __aexit__(self, exc_type, exc, tb):
            closing.set()
            await asyncio.Future()

    monkeypatch.setattr(mod, "GptmeAcpClient", _BlockingCloseClient)
    fallback = []
    monkeypatch.setattr(
        mod,
        "_terminate_asyncio_process_sync",
        lambda proc, timeout: fallback.append(proc),
    )
    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    _run(runtime.start())
    loop_thread = runtime._loop_thread
    errors = []
    close_futures = []
    submit = asyncio.run_coroutine_threadsafe

    def track_close(coro, loop):
        future = submit(coro, loop)
        close_futures.append(future)
        return future

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", track_close)

    def terminate():
        try:
            # Longer than the test's entire deadline: timeout must not be what
            # releases this thread after the runtime loop is stopped.
            runtime.terminate_subprocess_sync(timeout=60)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=terminate, daemon=True)
    thread.start()
    try:
        assert closing.wait(5), "Graceful close never started"
        runtime._stop_loop()
    finally:
        runtime._stop_loop()
        thread.join(5)

    assert not thread.is_alive()
    assert loop_thread is not None and not loop_thread.is_alive()
    assert errors == []
    assert len(close_futures) == 1
    assert close_futures[0].cancelled()
    assert fallback == [_BlockingCloseClient._process]
    assert runtime._client is None
    assert runtime.session_id is None


@pytest.mark.timeout(15)
def test_start_entry_failure_stops_loop_and_leaves_no_child(monkeypatch, tmp_path):
    """A failed client entry must not leak the dedicated loop thread (#5360)."""
    import gptme.server.acp_session_runtime as mod

    class _MissingAgentClient(_DummyClient):
        async def __aenter__(self):
            raise FileNotFoundError("ACP agent command not found: 'missing-acp'")

    monkeypatch.setattr(mod, "GptmeAcpClient", _MissingAgentClient)
    runtime = mod.AcpSessionRuntime(workspace=tmp_path, command="missing-acp")
    runtime._ensure_loop()
    loop_thread = runtime._loop_thread
    assert loop_thread is not None and loop_thread.is_alive()

    with pytest.raises(FileNotFoundError, match="missing-acp"):
        _run(runtime.start())

    assert runtime._client is None
    assert runtime.session_id is None
    assert not runtime.is_subprocess_alive()
    assert runtime._loop_thread is None
    assert not loop_thread.is_alive()


@pytest.mark.timeout(15)
def test_failed_start_keeps_child_when_cleanup_raises(monkeypatch, tmp_path):
    """A raising __aexit__ after failed setup must not drop the child handle."""
    import gptme.server.acp_session_runtime as mod

    class _FailingSetupClient(_DummyClient):
        _process = object()

        async def set_session_model(self, session_id: str, model_id: str) -> None:
            raise RuntimeError("model rejected")

        async def __aexit__(self, exc_type, exc, tb):
            raise OSError("broken transport")

    monkeypatch.setattr(mod, "GptmeAcpClient", _FailingSetupClient)
    fallback: list[Any] = []
    monkeypatch.setattr(
        mod,
        "_terminate_asyncio_process_sync",
        lambda proc, timeout: fallback.append(proc),
    )
    runtime = mod.AcpSessionRuntime(workspace=tmp_path, model="openai/gpt-4o-mini")
    runtime._ensure_loop()
    loop_thread = runtime._loop_thread

    with pytest.raises(RuntimeError, match="model rejected"):
        _run(runtime.start())

    assert fallback == [_FailingSetupClient._process]
    assert runtime._client is None
    assert runtime.session_id is None
    assert runtime._loop_thread is None
    assert loop_thread is not None and not loop_thread.is_alive()


@pytest.mark.skipif(
    sys.platform == "win32", reason="needs POSIX signals and os.WNOHANG"
)
@pytest.mark.timeout(20)
def test_cancelled_close_kills_term_ignoring_child(monkeypatch, tmp_path):
    """Cancelled close() still SIGKILLs a SIGTERM-ignoring child (#5360)."""
    import os

    import gptme.server.acp_session_runtime as mod

    waiting = threading.Event()

    class _TermIgnoringClient(_DummyClient):
        def __init__(self, workspace: Path, **kwargs: Any) -> None:
            super().__init__(workspace, **kwargs)
            self._process: Any = None

        async def __aenter__(self):
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                (
                    "import signal, sys, time; "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    "print('ready', flush=True); time.sleep(30)"
                ),
                stdout=asyncio.subprocess.PIPE,
            )
            assert proc.stdout is not None
            await proc.stdout.readline()
            self._process = proc
            self.started = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            if self._process is not None and self._process.returncode is None:
                self._process.terminate()
                waiting.set()
                await self._process.wait()
            self.closed = True

    monkeypatch.setattr(mod, "GptmeAcpClient", _TermIgnoringClient)
    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    _run(runtime.start())
    pid = runtime.process_pid
    loop_thread = runtime._loop_thread
    assert isinstance(pid, int)
    assert runtime.is_subprocess_alive()

    result: list[BaseException] = []

    async def _main() -> None:
        task = asyncio.create_task(runtime.close())
        await asyncio.get_running_loop().run_in_executor(None, waiting.wait, 5)
        assert waiting.is_set(), "Graceful close never started waiting on the child"
        task.cancel()
        try:
            await task
        except BaseException as exc:
            result.append(exc)

    try:
        asyncio.run(_main())

        # Assert before the safety-net cleanup below, which would kill the
        # child itself and hide a close() that never signalled it.
        assert len(result) == 1
        assert isinstance(result[0], asyncio.CancelledError)
        assert runtime._client is None
        assert runtime.session_id is None
        assert loop_thread is not None and not loop_thread.is_alive()
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)
    finally:
        runtime.terminate_subprocess_sync(graceful=False)


def test_loop_calls_cancels_pending_futures_on_close():
    """Calls accepted but never started are cancelled when the loop closes."""
    import concurrent.futures

    from gptme.server.acp_session_runtime import _LoopCalls

    calls = _LoopCalls()
    fut: concurrent.futures.Future[int] = concurrent.futures.Future()
    calls.futures.add(fut)
    calls.close_and_cancel()
    assert fut.cancelled()
    assert calls.closed


@pytest.mark.skipif(
    sys.platform == "win32", reason="needs POSIX signals and os.WNOHANG"
)
def test_terminate_asyncio_process_sync_kills_term_ignoring_child():
    """Fallback termination outside the owning loop escalates to SIGKILL."""
    import subprocess

    from gptme.server.acp_session_runtime import _terminate_asyncio_process_sync

    popen = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "print('ready', flush=True); time.sleep(30)"
            ),
        ],
        stdout=subprocess.PIPE,
    )
    assert popen.stdout is not None
    popen.stdout.readline()

    class _AsyncioLike:
        """Mimics asyncio.subprocess.Process: no blocking wait(timeout=)."""

        pid = popen.pid
        returncode = None

        def terminate(self) -> None:
            popen.send_signal(15)

        def kill(self) -> None:
            popen.kill()

    try:
        _terminate_asyncio_process_sync(_AsyncioLike(), timeout=0.3)
        # Reaped by pid: the child is gone, not a zombie we still own.
        import os

        with pytest.raises(ChildProcessError):
            os.waitpid(popen.pid, os.WNOHANG)
    finally:
        if popen.poll() is None:
            popen.kill()


def test_acp_step_second_turn_reuses_live_runtime(
    monkeypatch, client: FlaskClient, tmp_path
):
    """A server-hosted ACP conversation must handle more than one turn (#5177)."""
    import gptme.server.acp_session_runtime as rt_mod
    import gptme.server.session_step as sessions_mod

    created: list[_LoopBoundClient] = []

    def _factory(*args, **kwargs):
        c = _LoopBoundClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(rt_mod, "GptmeAcpClient", _factory)
    monkeypatch.setattr(sessions_mod, "trigger_hook", lambda *args, **kwargs: [])

    from gptme.logmanager import LogManager
    from gptme.server.acp_session_runtime import AcpSessionRuntime
    from gptme.server.api_v2_sessions import ConversationSession, SessionManager

    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]

    session = ConversationSession(id="sid-2turn", conversation_id=conversation_id)
    session.use_acp = True
    runtime = AcpSessionRuntime(workspace=tmp_path)
    session.acp_runtime = runtime
    with SessionManager._lock:
        SessionManager._sessions["sid-2turn"] = session
        SessionManager._conversation_sessions[conversation_id].add("sid-2turn")

    try:
        for content in ["first turn", "second turn"]:
            resp = client.post(
                f"/api/v2/conversations/{conversation_id}",
                json={"role": "user", "content": content},
            )
            assert resp.status_code == 200
            # Like _start_acp_step_thread: one asyncio.run() per step.
            _run(sessions_mod._acp_step(conversation_id, session, tmp_path))
            assert runtime.is_subprocess_alive()

        errors = [e for e in session.events if e["type"] == "error"]
        assert errors == []
        assert session.last_error is None
        assert len(created) == 1

        manager = LogManager.load(conversation_id)
        assistant_msgs = [
            m.content for m in manager.log.messages if m.role == "assistant"
        ]
        assert assistant_msgs == ["echo:first turn", "echo:second turn"]
    finally:
        _run(runtime.close())
        with SessionManager._lock:
            SessionManager._sessions.pop("sid-2turn", None)
            SessionManager._conversation_sessions[conversation_id].discard("sid-2turn")


def test_runtime_stop_loop_releases_inflight_prompt(monkeypatch, tmp_path):
    """Stopping the runtime loop mid-prompt fails the call instead of hanging."""
    import gptme.server.acp_session_runtime as mod

    started = threading.Event()

    class _HangingClient(_DummyClient):
        async def prompt(self, session_id, message, max_tokens=None):
            started.set()
            await asyncio.sleep(3600)

    monkeypatch.setattr(mod, "GptmeAcpClient", _HangingClient)
    runtime = mod.AcpSessionRuntime(workspace=tmp_path)

    result: list[BaseException] = []

    def _caller() -> None:
        try:
            asyncio.run(runtime.prompt("hang"))
        except BaseException as e:
            result.append(e)

    t = threading.Thread(target=_caller, daemon=True)
    t.start()
    assert started.wait(5)
    runtime._stop_loop()
    t.join(5)
    assert not t.is_alive()
    assert len(result) == 1
    # Not CancelledError: the caller's task was never cancelled, so a
    # BaseException would escape every ``except Exception`` error path.
    assert isinstance(result[0], ConnectionError)
    assert "runtime loop stopped" in str(result[0])


def test_prompt_cancellation_propagates_to_caller(monkeypatch, tmp_path):
    """A caller cancelling its own task still sees CancelledError."""
    import gptme.server.acp_session_runtime as mod

    started = threading.Event()

    class _HangingClient(_DummyClient):
        async def prompt(self, session_id, message, max_tokens=None):
            started.set()
            await asyncio.sleep(3600)

    monkeypatch.setattr(mod, "GptmeAcpClient", _HangingClient)
    runtime = mod.AcpSessionRuntime(workspace=tmp_path)

    result: list[BaseException] = []

    async def _main() -> None:
        task = asyncio.create_task(runtime.prompt("hang"))
        await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
        task.cancel()
        try:
            await task
        except BaseException as e:
            result.append(e)

    try:
        asyncio.run(_main())
    finally:
        runtime._stop_loop()
    assert len(result) == 1
    assert isinstance(result[0], asyncio.CancelledError)


def test_prompt_cancelled_inside_runtime_is_an_error(monkeypatch, tmp_path):
    """A call cancelled inside the runtime loop is an error, not caller cancellation."""
    import gptme.server.acp_session_runtime as mod

    class _SelfCancellingClient(_DummyClient):
        async def prompt(self, session_id, message, max_tokens=None):
            raise asyncio.CancelledError

    monkeypatch.setattr(mod, "GptmeAcpClient", _SelfCancellingClient)
    runtime = mod.AcpSessionRuntime(workspace=tmp_path)

    with pytest.raises(RuntimeError, match="cancelled before it completed"):
        _run(runtime.prompt("cancel"))
    _run(runtime.close())


@pytest.mark.timeout(15)
def test_stop_loop_does_not_hang_on_task_that_ignores_cancellation(
    monkeypatch, tmp_path
):
    """A task that swallows CancelledError must not block loop shutdown."""
    import gptme.server.acp_session_runtime as mod

    monkeypatch.setattr(mod, "_SHUTDOWN_GRACE_S", 0.2)
    started = threading.Event()

    class _StubbornClient(_DummyClient):
        async def prompt(self, session_id, message, max_tokens=None):
            started.set()
            while True:
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    continue

    monkeypatch.setattr(mod, "GptmeAcpClient", _StubbornClient)
    runtime = mod.AcpSessionRuntime(workspace=tmp_path)

    result: list[BaseException] = []

    def _caller() -> None:
        try:
            asyncio.run(runtime.prompt("stubborn"))
        except BaseException as e:
            result.append(e)

    t = threading.Thread(target=_caller, daemon=True)
    t.start()
    assert started.wait(5)
    loop_thread = runtime._loop_thread
    began = time.monotonic()
    runtime._stop_loop()
    assert time.monotonic() - began < 10
    assert loop_thread is not None and not loop_thread.is_alive()
    t.join(5)
    assert not t.is_alive()
    # The caller is released with an error rather than hanging on the loop.
    assert len(result) == 1
    assert isinstance(result[0], ConnectionError)


@pytest.mark.timeout(60)
def test_acp_step_thread_two_turns_reuse_live_runtime(
    monkeypatch, client: FlaskClient, tmp_path
):
    """The production thread-spawned step path keeps one runtime across turns."""
    import gptme.server.acp_session_runtime as rt_mod
    import gptme.server.session_step as sessions_mod

    created: list[_LoopBoundClient] = []

    def _factory(*args, **kwargs):
        c = _LoopBoundClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(rt_mod, "GptmeAcpClient", _factory)
    monkeypatch.setattr(sessions_mod, "trigger_hook", lambda *args, **kwargs: [])

    from gptme.logmanager import LogManager
    from gptme.server.acp_session_runtime import AcpSessionRuntime
    from gptme.server.api_v2_sessions import ConversationSession, SessionManager

    conv = _make_v2_conversation(client)
    conversation_id = conv["conversation_id"]

    session = ConversationSession(
        id="sid-thread-2turn", conversation_id=conversation_id
    )
    session.use_acp = True
    runtime = AcpSessionRuntime(workspace=tmp_path)
    session.acp_runtime = runtime
    with SessionManager._lock:
        SessionManager._sessions["sid-thread-2turn"] = session
        SessionManager._conversation_sessions[conversation_id].add("sid-thread-2turn")

    def _wait_step_complete(before: int) -> int:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            count = len([e for e in session.events if e["type"] == "step_complete"])
            if count > before and not session.generating:
                return count
            time.sleep(0.05)
        raise AssertionError("ACP step thread did not complete")

    try:
        completions = 0
        for content in ["first turn", "second turn"]:
            resp = client.post(
                f"/api/v2/conversations/{conversation_id}",
                json={"role": "user", "content": content},
            )
            assert resp.status_code == 200
            assert sessions_mod._start_acp_step_thread(
                conversation_id, session, tmp_path
            )
            completions = _wait_step_complete(completions)
            assert runtime.is_subprocess_alive()

        errors = [e for e in session.events if e["type"] == "error"]
        assert errors == []
        assert session.last_error is None
        assert len(created) == 1

        manager = LogManager.load(conversation_id)
        assistant_msgs = [
            m.content for m in manager.log.messages if m.role == "assistant"
        ]
        assert assistant_msgs == ["echo:first turn", "echo:second turn"]
    finally:
        _run(runtime.close())
        with SessionManager._lock:
            SessionManager._sessions.pop("sid-thread-2turn", None)
            SessionManager._conversation_sessions[conversation_id].discard(
                "sid-thread-2turn"
            )


@pytest.mark.timeout(15)
def test_failed_close_keeps_client_handle_for_sync_terminate(monkeypatch, tmp_path):
    """A raising __aexit__ must not drop the only handle to the subprocess."""
    import gptme.server.acp_session_runtime as mod

    class _FailingExitClient(_DummyClient):
        _process = object()

        async def __aexit__(self, exc_type, exc, tb):
            raise ConnectionError("transport already gone")

    monkeypatch.setattr(mod, "GptmeAcpClient", _FailingExitClient)
    fallback: list[Any] = []
    monkeypatch.setattr(
        mod,
        "_terminate_asyncio_process_sync",
        lambda proc, timeout: fallback.append(proc),
    )
    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    _run(runtime.start())

    # close() keeps the handle while __aexit__ runs, so a failing close still
    # reaches the subprocess through the synchronous fallback.
    _run(runtime.close())
    assert fallback == [_FailingExitClient._process]
    assert runtime._client is None


@pytest.mark.timeout(15)
def test_failed_close_signals_process_even_if_a_second_exit_would_succeed(
    monkeypatch, tmp_path
):
    """A finished context manager "exits" cleanly on retry without cleaning up."""
    from contextlib import asynccontextmanager

    import gptme.server.acp_session_runtime as mod

    class _ExhaustedExitClient(_DummyClient):
        _process = object()

        async def __aenter__(self):
            @asynccontextmanager
            async def _transport():
                yield
                raise OSError("shutdown failed before the process was stopped")

            self._ctx = _transport()
            await self._ctx.__aenter__()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            # First call raises; the second finds the generator exhausted and
            # returns normally.
            await self._ctx.__aexit__(exc_type, exc, tb)

    monkeypatch.setattr(mod, "GptmeAcpClient", _ExhaustedExitClient)
    fallback: list[Any] = []
    monkeypatch.setattr(
        mod,
        "_terminate_asyncio_process_sync",
        lambda proc, timeout: fallback.append(proc),
    )
    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    _run(runtime.start())

    _run(runtime.close())

    assert fallback == [_ExhaustedExitClient._process]
    assert runtime._client is None


@pytest.mark.timeout(15)
def test_runtime_refuses_to_reuse_a_transport_on_a_stopped_loop(monkeypatch, tmp_path):
    """A runtime whose loop was stopped externally must not run the old client."""
    import gptme.server.acp_session_runtime as mod

    class _StaticClient(_DummyClient):
        _process = object()

    monkeypatch.setattr(mod, "GptmeAcpClient", _StaticClient)
    fallback: list[Any] = []
    monkeypatch.setattr(
        mod,
        "_terminate_asyncio_process_sync",
        lambda proc, timeout: fallback.append(proc),
    )
    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    _run(runtime.start())

    runtime._stop_loop()

    with pytest.raises(ConnectionError, match="stopped loop"):
        _run(runtime.prompt("after stop"))

    # close() still terminates the orphaned subprocess instead of raising.
    _run(runtime.close())
    assert fallback == [_StaticClient._process]
    assert runtime._client is None
