"""Tests for ACP agent module internals.

Tests the GptmeAgent class methods that don't require the full ACP package,
focusing on tool call management, permission policies, and state tracking.

Note: Uses asyncio.run() instead of pytest-asyncio to avoid adding a
new test dependency.
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from gptme.acp.agent import GptmeAgent, _import_acp
from gptme.acp.types import (
    ToolCall,
    ToolCallStatus,
    ToolKind,
)


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _mock_permission_response(option_id: str | None = None, cancelled: bool = False):
    """Create a mock RequestPermissionResponse matching the ACP SDK Pydantic model.

    Args:
        option_id: The option_id for an allowed response (e.g. "allow-once", "allow-always")
        cancelled: If True, return a denied/cancelled response
    """
    try:
        from acp.schema import (  # type: ignore[import-not-found]
            AllowedOutcome,
            DeniedOutcome,
            RequestPermissionResponse,
        )

        if cancelled:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=option_id or "")
        )
    except ImportError:
        pytest.skip("acp not installed")


class TestGptmeAgentInit:
    """Tests for GptmeAgent initialization."""

    def test_create(self):
        agent = GptmeAgent()
        assert agent._conn is None
        assert agent._initialized is False
        assert agent._model is None
        assert agent._tool_calls == {}
        assert agent._permission_policies == {}

    def test_on_connect(self):
        agent = GptmeAgent()
        mock_conn = MagicMock()
        agent.on_connect(mock_conn)
        assert agent._conn is mock_conn

    def test_create_has_session_models_dict(self):
        agent = GptmeAgent()
        assert agent._session_models == {}

    def test_create_agent_factory(self):
        from gptme.acp.agent import create_agent

        agent = create_agent()
        assert isinstance(agent, GptmeAgent)


class TestImportAcp:
    """Tests for lazy ACP import."""

    def test_import_returns_bool(self):
        """_import_acp should return True if package is available, False otherwise."""
        result = _import_acp()
        assert isinstance(result, bool)


def _make_agent_with_conn():
    """Create an agent with a mock connection."""
    agent = GptmeAgent()
    conn = MagicMock()
    conn.session_update = AsyncMock()
    agent._conn = conn
    return agent


class TestReportToolCall:
    """Tests for _report_tool_call method."""

    def test_report_stores_tool_call(self):
        agent = _make_agent_with_conn()
        tc = ToolCall(
            tool_call_id="call_test123",
            title="Test call",
            kind=ToolKind.EXECUTE,
        )
        _run(agent._report_tool_call("session_1", tc))

        assert "session_1" in agent._tool_calls
        assert "call_test123" in agent._tool_calls["session_1"]

    def test_report_sends_update(self):
        agent = _make_agent_with_conn()
        tc = ToolCall(
            tool_call_id="call_test123",
            title="Test call",
            kind=ToolKind.EXECUTE,
        )
        _run(agent._report_tool_call("session_1", tc))

        agent._conn.session_update.assert_awaited_once_with(
            session_id="session_1",
            update=tc.to_dict(),
            source="gptme",
        )

    def test_report_no_connection(self):
        """Without a connection, report should silently return."""
        agent = GptmeAgent()
        tc = ToolCall(
            tool_call_id="call_test",
            title="Test",
            kind=ToolKind.EXECUTE,
        )
        # Should not raise
        _run(agent._report_tool_call("session_1", tc))
        assert "session_1" not in agent._tool_calls

    def test_report_multiple_calls_same_session(self):
        agent = _make_agent_with_conn()
        tc1 = ToolCall(tool_call_id="call_1", title="Call 1", kind=ToolKind.EXECUTE)
        tc2 = ToolCall(tool_call_id="call_2", title="Call 2", kind=ToolKind.READ)

        _run(agent._report_tool_call("session_1", tc1))
        _run(agent._report_tool_call("session_1", tc2))

        assert len(agent._tool_calls["session_1"]) == 2


def _make_agent_with_tool():
    """Create an agent with a pre-populated tool call."""
    agent = GptmeAgent()
    conn = MagicMock()
    conn.session_update = AsyncMock()
    agent._conn = conn

    tc = ToolCall(
        tool_call_id="call_existing",
        title="Existing call",
        kind=ToolKind.EXECUTE,
        status=ToolCallStatus.PENDING,
    )
    agent._tool_calls["session_1"] = {"call_existing": tc}
    return agent


class TestUpdateToolCall:
    """Tests for _update_tool_call method."""

    def test_update_status(self):
        agent = _make_agent_with_tool()
        _run(
            agent._update_tool_call(
                "session_1", "call_existing", ToolCallStatus.IN_PROGRESS
            )
        )

        tc = agent._tool_calls["session_1"]["call_existing"]
        assert tc.status == ToolCallStatus.IN_PROGRESS

    def test_update_with_content(self):
        agent = _make_agent_with_tool()
        content = [{"type": "text", "text": "result"}]
        _run(
            agent._update_tool_call(
                "session_1",
                "call_existing",
                ToolCallStatus.COMPLETED,
                content=content,
            )
        )

        tc = agent._tool_calls["session_1"]["call_existing"]
        assert tc.content == content

    def test_update_sends_session_update(self):
        agent = _make_agent_with_tool()
        _run(
            agent._update_tool_call(
                "session_1", "call_existing", ToolCallStatus.COMPLETED
            )
        )

        agent._conn.session_update.assert_awaited_once()
        call_args = agent._conn.session_update.call_args
        update = call_args.kwargs["update"]
        assert update["sessionUpdate"] == "tool_call_update"
        assert update["toolCallId"] == "call_existing"
        assert update["status"] == "completed"

    def test_update_no_connection(self):
        agent = GptmeAgent()
        # Should not raise
        _run(agent._update_tool_call("session_1", "call_x", ToolCallStatus.COMPLETED))


def _make_agent_with_mixed_calls():
    """Create an agent with tool calls in various states."""
    agent = GptmeAgent()
    conn = MagicMock()
    conn.session_update = AsyncMock()
    agent._conn = conn

    agent._tool_calls["session_1"] = {
        "call_pending": ToolCall(
            tool_call_id="call_pending",
            title="Pending",
            kind=ToolKind.EXECUTE,
            status=ToolCallStatus.PENDING,
        ),
        "call_progress": ToolCall(
            tool_call_id="call_progress",
            title="In Progress",
            kind=ToolKind.READ,
            status=ToolCallStatus.IN_PROGRESS,
        ),
        "call_done": ToolCall(
            tool_call_id="call_done",
            title="Done",
            kind=ToolKind.EDIT,
            status=ToolCallStatus.COMPLETED,
        ),
        "call_failed": ToolCall(
            tool_call_id="call_failed",
            title="Failed",
            kind=ToolKind.EXECUTE,
            status=ToolCallStatus.FAILED,
        ),
    }
    return agent


class TestCompletePendingToolCalls:
    """Tests for _complete_pending_tool_calls method."""

    def test_completes_pending_and_in_progress(self):
        agent = _make_agent_with_mixed_calls()
        _run(agent._complete_pending_tool_calls("session_1"))

        calls = agent._tool_calls["session_1"]
        assert calls["call_pending"].status == ToolCallStatus.COMPLETED
        assert calls["call_progress"].status == ToolCallStatus.COMPLETED
        # Already terminal states should be unchanged
        assert calls["call_done"].status == ToolCallStatus.COMPLETED
        assert calls["call_failed"].status == ToolCallStatus.FAILED

    def test_complete_with_failure(self):
        agent = _make_agent_with_mixed_calls()
        _run(agent._complete_pending_tool_calls("session_1", success=False))

        calls = agent._tool_calls["session_1"]
        assert calls["call_pending"].status == ToolCallStatus.FAILED
        assert calls["call_progress"].status == ToolCallStatus.FAILED

    def test_no_tool_calls_for_session(self):
        agent = GptmeAgent()
        conn = MagicMock()
        conn.session_update = AsyncMock()
        agent._conn = conn

        # Should not raise for unknown session
        _run(agent._complete_pending_tool_calls("nonexistent_session"))


class TestRequestToolPermission:
    """Tests for _request_tool_permission method."""

    def test_no_connection_auto_allows(self):
        agent = GptmeAgent()
        tc = ToolCall(tool_call_id="call_1", title="Test", kind=ToolKind.EXECUTE)
        result = _run(agent._request_tool_permission("session_1", tc))
        assert result is True

    def test_cached_allow_policy(self):
        agent = GptmeAgent()
        agent._conn = MagicMock()
        agent._permission_policies["session_1"] = {"execute": "allow"}

        tc = ToolCall(tool_call_id="call_1", title="Test", kind=ToolKind.EXECUTE)
        result = _run(agent._request_tool_permission("session_1", tc))
        assert result is True

    def test_cached_reject_policy(self):
        agent = GptmeAgent()
        agent._conn = MagicMock()
        agent._permission_policies["session_1"] = {"execute": "reject"}

        tc = ToolCall(tool_call_id="call_1", title="Test", kind=ToolKind.EXECUTE)
        result = _run(agent._request_tool_permission("session_1", tc))
        assert result is False

    def test_allow_once_response(self):
        agent = GptmeAgent()
        conn = MagicMock()
        conn.request_permission = AsyncMock(
            return_value=_mock_permission_response(option_id="allow-once")
        )
        agent._conn = conn

        tc = ToolCall(tool_call_id="call_1", title="Test", kind=ToolKind.EXECUTE)
        result = _run(agent._request_tool_permission("session_1", tc))
        assert result is True
        # Should NOT cache allow-once
        assert "session_1" not in agent._permission_policies

    def test_allow_always_response(self):
        agent = GptmeAgent()
        conn = MagicMock()
        conn.request_permission = AsyncMock(
            return_value=_mock_permission_response(option_id="allow-always")
        )
        agent._conn = conn

        tc = ToolCall(tool_call_id="call_1", title="Test", kind=ToolKind.EXECUTE)
        result = _run(agent._request_tool_permission("session_1", tc))
        assert result is True
        assert agent._permission_policies["session_1"]["execute"] == "allow"

    def test_reject_always_response(self):
        agent = GptmeAgent()
        conn = MagicMock()
        conn.request_permission = AsyncMock(
            return_value=_mock_permission_response(option_id="reject-always")
        )
        agent._conn = conn

        tc = ToolCall(tool_call_id="call_1", title="Test", kind=ToolKind.EXECUTE)
        result = _run(agent._request_tool_permission("session_1", tc))
        assert result is False
        assert agent._permission_policies["session_1"]["execute"] == "reject"

    def test_cancelled_response(self):
        agent = GptmeAgent()
        conn = MagicMock()
        conn.request_permission = AsyncMock(
            return_value=_mock_permission_response(cancelled=True)
        )
        agent._conn = conn

        tc = ToolCall(tool_call_id="call_1", title="Test", kind=ToolKind.EXECUTE)
        result = _run(agent._request_tool_permission("session_1", tc))
        assert result is False

    def test_permission_request_exception_auto_allows(self):
        agent = GptmeAgent()
        conn = MagicMock()
        conn.request_permission = AsyncMock(side_effect=RuntimeError("connection lost"))
        agent._conn = conn

        tc = ToolCall(tool_call_id="call_1", title="Test", kind=ToolKind.EXECUTE)
        result = _run(agent._request_tool_permission("session_1", tc))
        assert result is True  # Auto-allow on failure


class TestCreateConfirmWithTools:
    """Tests for the confirm callback factory.

    The callback uses run_coroutine_threadsafe to post async work to the event loop,
    which requires the loop to be running in a separate thread (matching the real
    usage where the callback runs in run_in_executor while the event loop runs
    in the main async thread).
    """

    @staticmethod
    def _run_callback_with_threaded_loop(agent, msg):
        """Run the confirm callback with a properly threaded event loop."""
        loop = asyncio.new_event_loop()
        exception = None

        def run_loop():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()

        try:
            callback = agent._create_confirm_with_tools("session_1", loop)
            result = callback(msg)
        except Exception as e:
            exception = e
            result = None
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            loop.close()

        if exception:
            raise exception
        return result

    def test_callback_parses_shell_command(self):
        agent = GptmeAgent()
        conn = MagicMock()
        conn.session_update = AsyncMock()
        conn.request_permission = AsyncMock(
            return_value=_mock_permission_response(option_id="allow-once")
        )
        agent._conn = conn

        result = self._run_callback_with_threaded_loop(agent, "Run command: ls -la")
        assert result is True

        assert "session_1" in agent._tool_calls
        tool_calls = agent._tool_calls["session_1"]
        assert len(tool_calls) == 1
        tc = list(tool_calls.values())[0]
        assert tc.kind == ToolKind.EXECUTE

    def test_callback_parses_save_command(self):
        agent = GptmeAgent()
        conn = MagicMock()
        conn.session_update = AsyncMock()
        conn.request_permission = AsyncMock(
            return_value=_mock_permission_response(option_id="allow-once")
        )
        agent._conn = conn

        result = self._run_callback_with_threaded_loop(agent, "Save to /tmp/test.py")
        assert result is True

        tc = list(agent._tool_calls["session_1"].values())[0]
        assert tc.kind == ToolKind.EDIT

    def test_callback_parses_python_execution(self):
        agent = GptmeAgent()
        conn = MagicMock()
        conn.session_update = AsyncMock()
        conn.request_permission = AsyncMock(
            return_value=_mock_permission_response(option_id="allow-once")
        )
        agent._conn = conn

        result = self._run_callback_with_threaded_loop(
            agent, "Execute this code in Python"
        )
        assert result is True

        tc = list(agent._tool_calls["session_1"].values())[0]
        assert tc.kind == ToolKind.EXECUTE

    def test_callback_permission_denied(self):
        agent = GptmeAgent()
        conn = MagicMock()
        conn.session_update = AsyncMock()
        conn.request_permission = AsyncMock(
            return_value=_mock_permission_response(option_id="reject-once")
        )
        agent._conn = conn

        result = self._run_callback_with_threaded_loop(agent, "Run command: rm -rf /")
        assert result is False

        tc = list(agent._tool_calls["session_1"].values())[0]
        assert tc.status == ToolCallStatus.FAILED


class TestInitializeWithoutAcp:
    """Tests for initialize/new_session when ACP package is not installed."""

    def test_initialize_without_acp_package(self):
        """If ACP package is not installed, initialize should raise RuntimeError."""
        agent = GptmeAgent()
        # This will either succeed (if ACP is installed) or raise RuntimeError
        try:
            result = _run(agent.initialize(protocol_version=1))
            # If ACP is installed, we should get a response
            assert result is not None
        except RuntimeError as e:
            assert "agent-client-protocol" in str(e)

    def test_load_session_not_implemented(self):
        agent = GptmeAgent()
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            _run(agent.load_session(session_id="fake_session"))


class TestCleanupSession:
    """Tests for _cleanup_session and cancel methods."""

    def test_cleanup_removes_all_state(self):
        """_cleanup_session should remove session_models, tool_calls, and permission_policies."""
        agent = GptmeAgent()
        sid = "session_cleanup_test"

        # Populate all per-session state
        agent._session_models[sid] = "anthropic/claude-sonnet-4-6"
        agent._tool_calls[sid] = {
            "call_1": ToolCall(
                tool_call_id="call_1", title="Test", kind=ToolKind.EXECUTE
            )
        }
        agent._permission_policies[sid] = {"execute": "allow"}
        agent._registry.create(sid)

        agent._cleanup_session(sid)

        assert sid not in agent._session_models
        assert sid not in agent._tool_calls
        assert sid not in agent._permission_policies
        assert agent._registry.get(sid) is None

    def test_cleanup_idempotent_on_unknown_session(self):
        """Cleaning up a nonexistent session should not raise."""
        agent = GptmeAgent()
        # Should not raise
        agent._cleanup_session("nonexistent_session")

    def test_cleanup_isolates_other_sessions(self):
        """Cleaning up one session should not affect another."""
        agent = GptmeAgent()

        agent._session_models["s1"] = "model-a"
        agent._session_models["s2"] = "model-b"
        agent._tool_calls["s1"] = {}
        agent._tool_calls["s2"] = {}

        agent._cleanup_session("s1")

        assert "s1" not in agent._session_models
        assert agent._session_models["s2"] == "model-b"
        assert "s1" not in agent._tool_calls
        assert "s2" in agent._tool_calls

    def test_cancel_calls_cleanup(self):
        """cancel() should clean up all per-session state."""
        agent = GptmeAgent()
        sid = "session_cancel_test"

        agent._session_models[sid] = "some-model"
        agent._tool_calls[sid] = {}
        agent._permission_policies[sid] = {"execute": "allow"}

        _run(agent.cancel(session_id=sid))

        assert sid not in agent._session_models
        assert sid not in agent._tool_calls
        assert sid not in agent._permission_policies


class TestPerSessionModel:
    """Tests for per-session model override behavior.

    Validates that _session_models is correctly populated and used
    when resolving the effective model for a session.
    """

    def test_set_session_model_stores_model(self):
        """set_session_model should store model in _session_models dict."""
        agent = GptmeAgent()
        _run(
            agent.set_session_model(
                model_id="openrouter/meta-llama/llama-3", session_id="s1"
            )
        )
        assert agent._session_models["s1"] == "openrouter/meta-llama/llama-3"

    def test_set_session_model_overwrites_existing(self):
        """Calling set_session_model twice should overwrite the previous value."""
        agent = GptmeAgent()
        agent._session_models["s1"] = "old-model"
        _run(agent.set_session_model(model_id="new-model", session_id="s1"))
        assert agent._session_models["s1"] == "new-model"

    def test_set_session_model_isolates_sessions(self):
        """Setting model for one session should not affect another."""
        agent = GptmeAgent()
        _run(agent.set_session_model(model_id="model-a", session_id="s1"))
        _run(agent.set_session_model(model_id="model-b", session_id="s2"))
        assert agent._session_models["s1"] == "model-a"
        assert agent._session_models["s2"] == "model-b"

    def test_effective_model_uses_per_session_override(self):
        """When session has a per-session model, it should be used over the global default."""
        agent = GptmeAgent()
        agent._model = "global-default"
        agent._session_models["s1"] = "per-session-override"

        effective = agent._session_models.get("s1", agent._model)
        assert effective == "per-session-override"

    def test_effective_model_falls_back_to_global(self):
        """When no per-session model is set, the global model should be used."""
        agent = GptmeAgent()
        agent._model = "global-default"

        effective = agent._session_models.get("unknown-session", agent._model)
        assert effective == "global-default"

    def test_effective_model_none_when_both_unset(self):
        """When neither per-session nor global model is set, effective should be None."""
        agent = GptmeAgent()

        effective = agent._session_models.get("unknown-session", agent._model)
        assert effective is None

    def test_cleanup_removes_session_model(self):
        """_cleanup_session should remove the per-session model."""
        agent = GptmeAgent()
        agent._session_models["s1"] = "some-model"
        agent._session_models["s2"] = "other-model"

        agent._cleanup_session("s1")

        assert "s1" not in agent._session_models
        assert agent._session_models["s2"] == "other-model"

    def test_per_session_model_with_provider_suffix(self):
        """Per-session models with @provider routing syntax should be stored as-is."""
        agent = GptmeAgent()
        model_with_routing = "z-ai/glm-5@together"
        _run(agent.set_session_model(model_id=model_with_routing, session_id="s1"))
        assert agent._session_models["s1"] == model_with_routing


def _make_mock_acp_factories():
    """Create mock ACP text_block and update_agent_message factories.

    These return plain dicts so tests can inspect the output without
    needing the real ACP package installed.
    """

    def text_block(text: str) -> dict:
        return {"type": "text", "text": text}

    def update_agent_message(block: dict) -> dict:
        return {"sessionUpdate": "agent_message", "content": [block]}

    return text_block, update_agent_message


def _make_mock_log():
    """Create a minimal mock LogManager that _handle_slash_command can use."""
    log = MagicMock()
    log.append = MagicMock()
    log.undo = MagicMock()
    log.write = MagicMock()
    log.log = MagicMock()
    log.workspace = None
    return log


class TestHandleSlashCommand:
    """Tests for _handle_slash_command method.

    These tests verify that slash commands are detected and handled correctly
    without requiring the full ACP package to be installed.
    """

    def _make_agent_with_conn(self):
        agent = GptmeAgent()
        conn = MagicMock()
        conn.session_update = AsyncMock()
        agent._conn = conn
        # Inject PromptResponse mock so the method can construct a response
        import gptme.acp.agent as agent_mod

        agent_mod.PromptResponse = MagicMock(
            side_effect=lambda stop_reason: {"stop_reason": stop_reason}
        )
        return agent

    def test_blocked_command_exit(self):
        """The /exit command should be blocked in ACP context."""
        agent = self._make_agent_with_conn()
        log = _make_mock_log()
        text_block, update_agent_message = _make_mock_acp_factories()

        from gptme.message import Message

        msg = Message("user", "/exit")
        result = _run(
            agent._handle_slash_command(
                msg, log, "session_1", text_block, update_agent_message
            )
        )

        # Should return end_turn, not cancelled
        assert result["stop_reason"] == "end_turn"
        # Should send a message about the command being unavailable
        agent._conn.session_update.assert_awaited_once()
        call_kwargs = agent._conn.session_update.call_args.kwargs
        update = call_kwargs["update"]
        assert "exit" in update["content"][0]["text"].lower()
        assert "not available" in update["content"][0]["text"].lower()

    def test_blocked_command_restart(self):
        """The /restart command should be blocked in ACP context."""
        agent = self._make_agent_with_conn()
        log = _make_mock_log()
        text_block, update_agent_message = _make_mock_acp_factories()

        from gptme.message import Message

        msg = Message("user", "/restart")
        result = _run(
            agent._handle_slash_command(
                msg, log, "session_1", text_block, update_agent_message
            )
        )

        assert result["stop_reason"] == "end_turn"
        agent._conn.session_update.assert_awaited_once()
        call_kwargs = agent._conn.session_update.call_args.kwargs
        update = call_kwargs["update"]
        assert "restart" in update["content"][0]["text"].lower()

    def test_help_command_returns_output(self):
        """The /help command should return command list output."""
        agent = self._make_agent_with_conn()
        log = _make_mock_log()
        text_block, update_agent_message = _make_mock_acp_factories()

        from gptme.message import Message

        msg = Message("user", "/help")
        result = _run(
            agent._handle_slash_command(
                msg, log, "session_1", text_block, update_agent_message
            )
        )

        assert result["stop_reason"] == "end_turn"
        agent._conn.session_update.assert_awaited_once()
        call_kwargs = agent._conn.session_update.call_args.kwargs
        update = call_kwargs["update"]
        # /help prints the available commands list
        output = update["content"][0]["text"]
        assert "Available commands" in output or "/help" in output

    def test_unknown_command_returns_end_turn(self):
        """An unknown command should return end_turn (handle_cmd prints 'Unknown command')."""
        agent = self._make_agent_with_conn()
        log = _make_mock_log()
        text_block, update_agent_message = _make_mock_acp_factories()

        from gptme.message import Message

        msg = Message("user", "/nonexistentcommand")
        result = _run(
            agent._handle_slash_command(
                msg, log, "session_1", text_block, update_agent_message
            )
        )

        assert result["stop_reason"] == "end_turn"
        agent._conn.session_update.assert_awaited_once()


class TestConnNullGuard:
    """Tests for connection null guards in prompt error paths.

    Ensures that session_update calls are guarded against self._conn being None,
    which can happen if the client disconnects or if prompt() is called before
    on_connect().
    """

    def test_complete_pending_tool_calls_no_conn(self):
        """_complete_pending_tool_calls should work without a connection."""
        agent = GptmeAgent()
        # Populate a pending tool call
        tc = ToolCall(
            tool_call_id="call_1",
            title="Test",
            kind=ToolKind.EXECUTE,
            status=ToolCallStatus.IN_PROGRESS,
        )
        agent._tool_calls["s1"] = {"call_1": tc}

        # Should not raise even without self._conn
        _run(agent._complete_pending_tool_calls("s1", success=False))

    def test_report_tool_call_no_conn_stores_nothing(self):
        """_report_tool_call without connection should not store the call."""
        agent = GptmeAgent()
        assert agent._conn is None

        tc = ToolCall(
            tool_call_id="call_orphan",
            title="Orphan",
            kind=ToolKind.EXECUTE,
        )
        _run(agent._report_tool_call("s1", tc))
        # Early return means no storage
        assert "s1" not in agent._tool_calls

    def test_update_tool_call_no_conn_silently_returns(self):
        """_update_tool_call without connection should not raise."""
        agent = GptmeAgent()
        assert agent._conn is None

        # Should not raise
        _run(agent._update_tool_call("s1", "call_1", ToolCallStatus.COMPLETED))
