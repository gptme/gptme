"""Tests for ACP agent module internals.

Tests the GptmeAgent class methods that don't require the full ACP package,
focusing on tool call management, permission policies, and state tracking.

Note: Uses asyncio.run() instead of pytest-asyncio to avoid adding a
new test dependency.
"""

import asyncio
import builtins
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gptme.acp.agent import (
    _CANCEL_GRACE_S,
    GptmeAgent,
    _acp_turn,
    _AcpTurn,
    _import_acp,
    _TurnCancelled,
)
from gptme.acp.types import (
    ToolCall,
    ToolCallStatus,
    ToolKind,
)
from gptme.hooks.confirm import ConfirmAction
from gptme.tools import ToolUse


def _acp_has_session_model_state() -> bool:
    """Check if ACP schema has SessionModelState (removed in 0.11.0)."""
    try:
        from acp.schema import (  # type: ignore[attr-defined]
            SessionModelState,  # noqa: F401
        )

        return True
    except ImportError:
        return False


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
        from acp.schema import (
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
        assert agent._session_commands_advertised == set()

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

        update = agent._conn.session_update.await_args.kwargs["update"]
        assert update["content"] == content
        assert not agent._tool_calls["session_1"]

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

    def test_terminal_update_is_retried_with_its_original_outcome(self):
        agent = _make_agent_with_tool()
        agent._conn.session_update.side_effect = [RuntimeError("disconnected"), None]

        with pytest.raises(RuntimeError, match="disconnected"):
            _run(
                agent._update_tool_call(
                    "session_1", "call_existing", ToolCallStatus.FAILED
                )
            )

        assert "call_existing" in agent._tool_calls["session_1"]
        _run(agent._complete_pending_tool_calls("session_1", success=True))
        assert agent._conn.session_update.await_count == 2
        assert (
            agent._conn.session_update.await_args.kwargs["update"]["status"] == "failed"
        )
        assert "call_existing" not in agent._tool_calls["session_1"]

    def test_delivered_terminal_update_releases_call(self):
        agent = _make_agent_with_tool()
        _run(
            agent._update_tool_call(
                "session_1", "call_existing", ToolCallStatus.COMPLETED
            )
        )
        assert not agent._tool_calls["session_1"]

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

        updates = {
            call.kwargs["update"]["toolCallId"]: call.kwargs["update"]["status"]
            for call in agent._conn.session_update.await_args_list
        }
        assert updates == {
            "call_pending": "failed",
            "call_progress": "completed",
            "call_done": "completed",
            "call_failed": "failed",
        }
        assert not agent._tool_calls["session_1"]

    def test_complete_with_failure(self):
        agent = _make_agent_with_mixed_calls()
        _run(agent._complete_pending_tool_calls("session_1", success=False))

        updates = {
            call.kwargs["update"]["toolCallId"]: call.kwargs["update"]["status"]
            for call in agent._conn.session_update.await_args_list
        }
        assert updates["call_pending"] == "failed"
        assert updates["call_progress"] == "failed"
        assert updates["call_done"] == "completed"
        assert not agent._tool_calls["session_1"]

    def test_failed_permission_check_marks_call_failed(self):
        """A denial that bypasses the normal reply path must not stay PENDING."""
        agent = GptmeAgent()
        conn = MagicMock()
        conn.session_update = AsyncMock()
        agent._conn = conn

        async def broken_permission(session_id, tool_call):
            raise RuntimeError("client went away")

        agent._request_tool_permission = broken_permission  # type: ignore[method-assign]
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:
            confirm = agent._create_confirm_with_tools("session_1", loop)
            assert confirm("Run command: ls") is False
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            loop.close()

        statuses = [
            call.kwargs["update"]["status"]
            for call in conn.session_update.await_args_list
            if call.kwargs["update"].get("sessionUpdate") == "tool_call_update"
        ]
        assert statuses == ["failed"]
        # The delivered FAILED outcome leaves nothing for the end-of-turn sweep.
        assert not agent._tool_calls["session_1"]
        _run(agent._complete_pending_tool_calls("session_1"))
        assert "completed" not in [
            call.kwargs["update"]["status"]
            for call in conn.session_update.await_args_list
            if "status" in call.kwargs["update"]
        ]

    def test_permission_timeout_cancels_request_before_late_approval(self):
        """A late approval must not revive a call denied by the sync timeout."""
        agent = _make_agent_with_conn()
        started = threading.Event()
        cancelled = threading.Event()
        loop = asyncio.new_event_loop()
        reply = loop.create_future()

        async def request_permission(**kwargs):
            started.set()
            try:
                # The transport can still receive an answer after our waiter exits.
                return await asyncio.shield(reply)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        agent._conn.request_permission = request_permission
        schedule = asyncio.run_coroutine_threadsafe
        permission_future = None

        def schedule_with_timeout(coro, target_loop):
            nonlocal permission_future
            future = schedule(coro, target_loop)
            if permission_future is None:
                permission_future = future
                result = future.result

                def timeout_while_pending(timeout=None):
                    assert started.wait(timeout=5)
                    # Exercise a real Future timeout without waiting 60 seconds.
                    return result(timeout=0)

                future.result = timeout_while_pending  # type: ignore[method-assign]
            return future

        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:
            with patch(
                "gptme.acp.agent.asyncio.run_coroutine_threadsafe",
                side_effect=schedule_with_timeout,
            ):
                confirm = agent._create_confirm_with_tools("session_1", loop)
                assert confirm("Run command: ls") is False

            # The delivered FAILED update settles the call, so it is no longer stored.
            assert not agent._tool_calls["session_1"]

            async def approve_late_and_complete():
                reply.set_result(_mock_permission_response("allow-once"))
                # Let callbacks for the late transport response run before sweeping.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                await agent._complete_pending_tool_calls("session_1")

            schedule(approve_late_and_complete(), loop).result(timeout=5)
            # A late approval must not re-add the call or move it to in_progress.
            assert not agent._tool_calls["session_1"]
            assert cancelled.is_set()
            statuses = [
                item.kwargs["update"]["status"]
                for item in agent._conn.session_update.await_args_list
            ]
            assert statuses == ["pending", "failed"]
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            loop.close()

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

    def test_permission_request_exception_denies(self):
        agent = GptmeAgent()
        conn = MagicMock()
        conn.request_permission = AsyncMock(side_effect=RuntimeError("connection lost"))
        agent._conn = conn

        tc = ToolCall(tool_call_id="call_1", title="Test", kind=ToolKind.EXECUTE)
        result = _run(agent._request_tool_permission("session_1", tc))
        assert result is False  # Fail-closed: deny on error for safety


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

        update = agent._conn.session_update.await_args.kwargs["update"]
        assert update["status"] == "failed"
        assert not agent._tool_calls["session_1"]


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

    def test_initialize_returns_agent_info(self):
        """Initialize response should include agentInfo with name and version."""
        agent = GptmeAgent()
        try:
            result = _run(agent.initialize(protocol_version=1))
        except RuntimeError:
            pytest.skip("ACP package not installed")
        assert result.agentInfo is not None
        assert result.agentInfo.name == "gptme"
        assert result.agentInfo.title == "gptme ACP Agent"
        assert result.agentInfo.version  # non-empty version string

    def test_load_session_returns_none_for_missing(self):
        """load_session returns None for sessions not on disk.

        Returning None (instead of raising) lets ACP clients like Zed
        gracefully fall back to new_session() without an RPC error that
        would disrupt the session lifecycle.
        """
        agent = GptmeAgent()
        result = _run(agent.load_session(session_id="nonexistent-session-id"))
        assert result is None


class TestCleanupSession:
    """Tests for _cleanup_session and cancel methods."""

    def test_cleanup_removes_all_state(self):
        """_cleanup_session should remove session_models, session_modes, tool_calls, and permission_policies."""
        agent = GptmeAgent()
        sid = "session_cleanup_test"

        # Populate all per-session state
        agent._session_models[sid] = "anthropic/claude-sonnet-4-6"
        agent._session_modes[sid] = "auto"
        agent._tool_calls[sid] = {
            "call_1": ToolCall(
                tool_call_id="call_1", title="Test", kind=ToolKind.EXECUTE
            )
        }
        agent._permission_policies[sid] = {"execute": "allow"}
        agent._session_commands_advertised.add(sid)
        agent._registry.create(sid)

        agent._cleanup_session(sid)

        assert sid not in agent._session_models
        assert sid not in agent._session_modes
        assert sid not in agent._tool_calls
        assert sid not in agent._permission_policies
        assert sid not in agent._session_commands_advertised
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

    def test_cancel_closes_mcp_clients_before_dropping_session(self):
        """cancel() must close owned MCP clients, then drop session state."""
        agent = GptmeAgent()
        sid = "session_cancel_mcp"
        client = MagicMock()
        agent._session_mcp_clients[sid] = {"notebook": client}
        agent._session_models[sid] = "some-model"

        _run(agent.cancel(session_id=sid))

        client.close.assert_called_once_with()
        assert sid not in agent._session_mcp_clients
        assert sid not in agent._session_models

    def test_cancel_does_not_hang_on_stalled_mcp_close(self, monkeypatch):
        """cancel() must drop session state even if MCP close never returns."""
        monkeypatch.setattr("gptme.acp.agent._MCP_CLIENT_CLOSE_TIMEOUT_S", 0.2)
        agent = GptmeAgent()
        sid = "session_cancel_stall"
        client = MagicMock()
        # Longer than the cancel timeout so a missing wait_for would fail.
        # Short enough that asyncio.run can drain the executor thread.
        client.close.side_effect = lambda: time.sleep(1.0)
        agent._session_mcp_clients[sid] = {"notebook": client}
        agent._session_models[sid] = "some-model"

        async def run_cancel() -> float:
            started = time.monotonic()
            await agent.cancel(session_id=sid)
            return time.monotonic() - started

        elapsed = _run(run_cancel())
        assert elapsed < 0.8, f"cancel() hung for {elapsed:.2f}s"
        assert sid not in agent._session_mcp_clients
        assert sid not in agent._session_models


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


class TestSendSessionOpenNotifications:
    """Tests for _send_session_open_notifications method."""

    def test_no_connection_skips_silently(self):
        """Without a connection, should return without error."""
        agent = GptmeAgent()
        assert agent._conn is None
        # Should not raise
        _run(agent._send_session_open_notifications("session_1", None, "/tmp"))

    def test_runs_without_error_with_mock_conn(self):
        """Should run without raising even if acp is not installed (import handled)."""
        agent = _make_agent_with_conn()
        # Should not raise regardless of whether acp is installed
        _run(agent._send_session_open_notifications("session_1", "test-model", "/tmp"))

    @pytest.mark.skipif(not _import_acp(), reason="requires acp package")
    def test_sends_model_info_and_commands_when_acp_installed(self):
        """When acp is installed, should call session_update for model info + commands."""
        agent = _make_agent_with_conn()
        _run(
            agent._send_session_open_notifications(
                "session_1", "anthropic/claude-sonnet-4-6", "/tmp"
            )
        )
        # session_update should be called at least twice: model info + AvailableCommands
        assert agent._conn.session_update.await_count >= 2
        assert "session_1" in agent._session_commands_advertised


class TestSendAvailableCommands:
    """Tests for _send_available_commands method."""

    def test_no_connection_returns_without_error(self):
        """Without a connection, _send_available_commands should return silently."""
        agent = GptmeAgent()
        assert agent._conn is None
        # Should not raise
        _run(agent._send_available_commands("session_1"))
        assert "session_1" not in agent._session_commands_advertised

    def test_already_advertised_skips(self):
        """If session already advertised, should not call session_update again."""
        agent = _make_agent_with_conn()
        agent._session_commands_advertised.add("session_1")
        _run(agent._send_available_commands("session_1"))
        agent._conn.session_update.assert_not_awaited()

    def test_failure_does_not_add_to_advertised(self):
        """If send fails, session should not be added to _session_commands_advertised."""
        agent = GptmeAgent()
        conn = MagicMock()
        conn.session_update = AsyncMock(side_effect=RuntimeError("connection lost"))
        agent._conn = conn

        _run(agent._send_available_commands("session_1"))

        assert "session_1" not in agent._session_commands_advertised

    @pytest.mark.skipif(not _import_acp(), reason="requires acp package")
    def test_command_names_have_no_slash_prefix(self):
        """Command names must not have a '/' prefix — the ACP client (Zed) adds it."""
        from acp.schema import AvailableCommand

        agent = _make_agent_with_conn()
        _run(agent._send_available_commands("session_1"))

        # Extract the AvailableCommand objects from the session_update call
        call_args = agent._conn.session_update.await_args
        assert call_args is not None, (
            "session_update was not called — _send_available_commands may have failed"
        )
        update = call_args.kwargs.get("update") or call_args.args[0]

        # Walk the update to find commands
        commands = []
        if hasattr(update, "available_commands"):
            commands = update.available_commands
        elif isinstance(update, dict) and "available_commands" in update:
            commands = update["available_commands"]

        assert len(commands) > 0, "Expected at least one command to be advertised"

        for cmd in commands:
            name = cmd.name if isinstance(cmd, AvailableCommand) else cmd["name"]
            assert not name.startswith("/"), (
                f"Command name '{name}' should not have a '/' prefix — "
                "the ACP client (e.g. Zed) adds the slash itself"
            )

    def test_cleanup_removes_from_advertised(self):
        """_cleanup_session should remove session from _session_commands_advertised."""
        agent = GptmeAgent()
        agent._session_commands_advertised.add("s1")
        agent._session_commands_advertised.add("s2")

        agent._cleanup_session("s1")

        assert "s1" not in agent._session_commands_advertised
        assert "s2" in agent._session_commands_advertised


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


class TestGetCommandsWithDescriptions:
    """Tests for the get_commands_with_descriptions helper."""

    def test_returns_nonempty_list(self):
        """Should return a non-empty list of (name, description) tuples."""
        from gptme.commands import get_commands_with_descriptions

        commands = get_commands_with_descriptions()
        assert len(commands) > 0
        for name, desc in commands:
            assert isinstance(name, str) and len(name) > 0
            assert isinstance(desc, str) and len(desc) > 0

    def test_includes_builtin_commands(self):
        """Should include built-in commands like help, model, tools."""
        from gptme.commands import get_commands_with_descriptions

        names = {name for name, _ in get_commands_with_descriptions()}
        assert "checkpoint" in names
        assert "help" in names
        assert "model" in names
        assert "tools" in names

    def test_descriptions_match_action_descriptions(self):
        """Built-in command descriptions should match action_descriptions."""
        from gptme.commands import get_commands_with_descriptions
        from gptme.commands.meta import action_descriptions

        cmd_dict = dict(get_commands_with_descriptions())
        for name, desc in action_descriptions.items():
            if name in cmd_dict:
                assert cmd_dict[name] == desc

    def test_sorted_by_name(self):
        """Commands should be sorted alphabetically by name."""
        from gptme.commands import get_commands_with_descriptions

        commands = get_commands_with_descriptions()
        names = [name for name, _ in commands]
        assert names == sorted(names)

    def test_no_duplicate_names(self):
        """Each command name should appear only once."""
        from gptme.commands import get_commands_with_descriptions

        names = [name for name, _ in get_commands_with_descriptions()]
        assert len(names) == len(set(names))


class TestSessionPersistence:
    """Tests for ACP session persistence (persistent log storage)."""

    def test_load_session_returns_none_for_nonexistent(self):
        """load_session should return None for sessions that don't exist on disk."""
        agent = GptmeAgent()
        result = _run(agent.load_session(session_id="2099-01-01-nonexistent-session"))
        assert result is None

    def test_load_session_returns_already_loaded(self):
        """load_session should return response for sessions already in registry."""
        if not _import_acp():
            pytest.skip("acp not installed")

        agent = GptmeAgent()
        sid = "test-already-loaded"
        agent._registry.create(sid)

        result = _run(agent.load_session(session_id=sid))
        assert result is not None

    def test_load_session_restores_from_disk(self, tmp_path):
        """load_session should restore a session from persistent log files."""
        if not _import_acp():
            pytest.skip("acp not installed")

        import json
        from unittest.mock import patch

        # Create a fake log directory with a conversation.jsonl
        session_dir = tmp_path / "test-session-restore"
        session_dir.mkdir()
        logfile = session_dir / "conversation.jsonl"

        # Write a minimal valid JSONL entry
        entry = {
            "role": "system",
            "content": "test system message",
            "timestamp": "2025-01-01T00:00:00+00:00",
        }
        logfile.write_text(json.dumps(entry) + "\n")

        agent = GptmeAgent()

        # Patch get_logs_dir to use our tmp dir
        with patch("gptme.acp.agent.get_logs_dir", return_value=tmp_path):
            result = _run(agent.load_session(session_id="test-session-restore"))

        assert result is not None
        # Session should now be in registry
        assert agent._registry.get("test-session-restore") is not None

    def test_list_sessions_includes_in_memory(self):
        """list_sessions should include active in-memory sessions."""
        if not _import_acp():
            pytest.skip("acp not installed")

        from unittest.mock import patch

        agent = GptmeAgent()
        agent._registry.create("in-memory-session")

        # Patch list_conversations to return empty (no on-disk sessions)
        with patch("gptme.acp.agent.list_conversations", return_value=[]):
            result = _run(agent.list_sessions())

        session_ids = [s.session_id for s in result.sessions]
        assert "in-memory-session" in session_ids

    def test_list_sessions_includes_persistent(self):
        """list_sessions should include sessions from persistent storage."""
        if not _import_acp():
            pytest.skip("acp not installed")

        from dataclasses import dataclass
        from unittest.mock import patch

        @dataclass(frozen=True)
        class FakeConv:
            id: str = "2025-01-01-test-session"
            name: str = "test"
            path: str = "/tmp/fake"
            created: float = 0.0
            modified: float = 0.0
            messages: int = 5
            branches: int = 1
            workspace: str = "/home/user/project"

        agent = GptmeAgent()

        with patch(
            "gptme.acp.agent.list_conversations",
            return_value=[FakeConv()],
        ):
            result = _run(agent.list_sessions())

        session_ids = [s.session_id for s in result.sessions]
        assert "2025-01-01-test-session" in session_ids
        # Check that workspace is passed as cwd
        matching = [
            s for s in result.sessions if s.session_id == "2025-01-01-test-session"
        ]
        assert matching[0].cwd == "/home/user/project"

    def test_list_sessions_deduplicates(self):
        """In-memory sessions that are also on disk should not appear twice."""
        if not _import_acp():
            pytest.skip("acp not installed")

        from dataclasses import dataclass
        from unittest.mock import patch

        @dataclass(frozen=True)
        class FakeConv:
            id: str = "shared-session"
            name: str = "shared"
            path: str = "/tmp/fake"
            created: float = 0.0
            modified: float = 0.0
            messages: int = 5
            branches: int = 1
            workspace: str = "."

        agent = GptmeAgent()
        agent._registry.create("shared-session")

        with patch(
            "gptme.acp.agent.list_conversations",
            return_value=[FakeConv()],
        ):
            result = _run(agent.list_sessions())

        session_ids = [s.session_id for s in result.sessions]
        assert session_ids.count("shared-session") == 1

    def test_load_session_returns_modes_and_models(self):
        """load_session should include modes and models in the response."""
        if not _import_acp():
            pytest.skip("acp not installed")

        agent = GptmeAgent()
        sid = "test-modes-models"
        agent._registry.create(sid)

        result = _run(agent.load_session(session_id=sid))
        assert result is not None
        # Verify modes are present
        assert result.modes is not None
        assert result.modes.current_mode_id == "default"
        assert len(result.modes.available_modes) == 2

    def test_load_session_initializes_session_model(self):
        """load_session should set up per-session model tracking."""
        if not _import_acp():
            pytest.skip("acp not installed")

        agent = GptmeAgent()
        agent._model = "test-provider/test-model"
        sid = "test-session-model-init"
        agent._registry.create(sid)

        _run(agent.load_session(session_id=sid))
        # Per-session model should be initialized from global model
        assert sid in agent._session_models
        assert agent._session_models[sid] == "test-provider/test-model"

    def test_load_session_from_disk_returns_modes_and_models(self, tmp_path):
        """load_session from disk should include modes and models."""
        if not _import_acp():
            pytest.skip("acp not installed")

        import json
        from unittest.mock import patch

        session_dir = tmp_path / "test-disk-modes"
        session_dir.mkdir()
        logfile = session_dir / "conversation.jsonl"
        entry = {
            "role": "system",
            "content": "test message",
            "timestamp": "2025-01-01T00:00:00+00:00",
        }
        logfile.write_text(json.dumps(entry) + "\n")

        agent = GptmeAgent()

        with patch("gptme.acp.agent.get_logs_dir", return_value=tmp_path):
            result = _run(agent.load_session(session_id="test-disk-modes"))

        assert result is not None
        assert result.modes is not None


class TestBuildModesState:
    """Tests for _build_modes_state()."""

    def test_returns_none_without_acp(self):
        """Returns None when ACP schema not importable."""
        agent = GptmeAgent()
        # If acp.schema isn't available, should return None gracefully
        result = agent._build_modes_state("test-session")
        # Can be None or a proper SessionModeState depending on install
        if result is not None:
            assert result.current_mode_id == "default"

    @pytest.mark.skipif(not _import_acp(), reason="acp package not installed")
    def test_default_mode(self):
        """Default mode should be 'default'."""
        agent = GptmeAgent()
        result = agent._build_modes_state("test-session")
        assert result is not None
        assert result.current_mode_id == "default"
        assert len(result.available_modes) == 2

    @pytest.mark.skipif(not _import_acp(), reason="acp package not installed")
    def test_auto_mode(self):
        """Should reflect auto mode when set."""
        agent = GptmeAgent()
        agent._session_modes["test-session"] = "auto"
        result = agent._build_modes_state("test-session")
        assert result is not None
        assert result.current_mode_id == "auto"

    @pytest.mark.skipif(not _import_acp(), reason="acp package not installed")
    def test_mode_ids(self):
        """Available modes should have correct IDs."""
        agent = GptmeAgent()
        result = agent._build_modes_state("test-session")
        assert result is not None
        mode_ids = {m.id for m in result.available_modes}
        assert mode_ids == {"default", "auto"}


class TestBuildModelsState:
    """Tests for _build_models_state()."""

    @pytest.mark.skipif(
        not _import_acp() or not _acp_has_session_model_state(),
        reason="acp package not installed or SessionModelState removed (>=0.11.0)",
    )
    def test_returns_models(self):
        """Should return available models from registry."""
        agent = GptmeAgent()
        result = agent._build_models_state("anthropic/claude-sonnet-4-6")
        assert result is not None
        assert len(result.available_models) > 0
        assert result.current_model_id == "anthropic/claude-sonnet-4-6"

    @pytest.mark.skipif(
        not _import_acp() or not _acp_has_session_model_state(),
        reason="acp package not installed or SessionModelState removed (>=0.11.0)",
    )
    def test_uses_global_model_as_fallback(self):
        """Falls back to global model when session model is None."""
        agent = GptmeAgent()
        agent._model = "anthropic/claude-opus-4-6"
        result = agent._build_models_state(None)
        assert result is not None
        assert result.current_model_id == "anthropic/claude-opus-4-6"

    @pytest.mark.skipif(
        not _import_acp() or not _acp_has_session_model_state(),
        reason="acp package not installed or SessionModelState removed (>=0.11.0)",
    )
    def test_models_have_required_fields(self):
        """Each model should have model_id and name."""
        agent = GptmeAgent()
        result = agent._build_models_state(None)
        assert result is not None
        for model in result.available_models:
            assert model.model_id
            assert model.name

    @pytest.mark.skipif(
        not _import_acp() or not _acp_has_session_model_state(),
        reason="acp package not installed or SessionModelState removed (>=0.11.0)",
    )
    def test_excludes_deprecated_models(self):
        """Deprecated models should not appear in available list."""
        agent = GptmeAgent()
        result = agent._build_models_state(None)
        assert result is not None
        # Deprecated models have deprecated=True in MODELS dict
        # Just verify we get some models (non-empty) and the count is reasonable
        assert len(result.available_models) > 5


class TestSetSessionMode:
    """Tests for set_session_mode()."""

    def test_set_valid_mode(self):
        """Should store valid mode."""
        agent = GptmeAgent()
        _run(agent.set_session_mode(mode_id="auto", session_id="s1"))
        assert agent._session_modes["s1"] == "auto"

    def test_set_default_mode(self):
        """Should store default mode."""
        agent = GptmeAgent()
        _run(agent.set_session_mode(mode_id="default", session_id="s1"))
        assert agent._session_modes["s1"] == "default"

    def test_ignore_invalid_mode(self):
        """Should ignore unknown mode IDs."""
        agent = GptmeAgent()
        _run(agent.set_session_mode(mode_id="turbo", session_id="s1"))
        assert "s1" not in agent._session_modes

    def test_mode_switch(self):
        """Should allow switching between modes."""
        agent = GptmeAgent()
        _run(agent.set_session_mode(mode_id="auto", session_id="s1"))
        assert agent._session_modes["s1"] == "auto"
        _run(agent.set_session_mode(mode_id="default", session_id="s1"))
        assert agent._session_modes["s1"] == "default"


class TestAutoModePermission:
    """Tests for auto-approve in auto mode."""

    def test_auto_mode_skips_permission(self):
        """In auto mode, tool permission should be auto-granted."""
        agent = GptmeAgent()
        agent._conn = MagicMock()  # Has connection but should skip
        agent._session_modes["s1"] = "auto"

        tool_call = ToolCall(
            tool_call_id="tc1",
            title="echo hello",
            kind=ToolKind.EXECUTE,
        )
        result = _run(agent._request_tool_permission("s1", tool_call))
        assert result is True
        # Should NOT have called request_permission on connection
        agent._conn.request_permission.assert_not_called()

    def test_default_mode_requests_permission(self):
        """In default mode, tool permission should be requested."""
        agent = GptmeAgent()
        agent._conn = AsyncMock()
        agent._conn.request_permission = AsyncMock(
            return_value=_mock_permission_response("allow-once")
        )
        agent._session_modes["s1"] = "default"

        tool_call = ToolCall(
            tool_call_id="tc1",
            title="echo hello",
            kind=ToolKind.EXECUTE,
        )
        result = _run(agent._request_tool_permission("s1", tool_call))
        assert result is True
        agent._conn.request_permission.assert_called_once()


class TestCwdSessionId:
    """Tests for _cwd_session_id deterministic session ID derivation."""

    def test_imports(self):
        """_cwd_session_id should be importable from acp.agent."""
        from gptme.acp.agent import _cwd_session_id

        assert callable(_cwd_session_id)

    def test_returns_acp_prefix(self, tmp_path):
        """Session ID should start with 'acp-'."""
        from gptme.acp.agent import _cwd_session_id

        sid = _cwd_session_id(str(tmp_path))
        assert sid.startswith("acp-")

    def test_deterministic(self, tmp_path):
        """Same path should always produce the same session ID."""
        from gptme.acp.agent import _cwd_session_id

        sid1 = _cwd_session_id(str(tmp_path))
        sid2 = _cwd_session_id(str(tmp_path))
        assert sid1 == sid2

    def test_different_paths_different_ids(self, tmp_path):
        """Different paths should produce different session IDs."""
        from gptme.acp.agent import _cwd_session_id

        path_a = tmp_path / "project-a"
        path_a.mkdir()
        path_b = tmp_path / "project-b"
        path_b.mkdir()

        sid_a = _cwd_session_id(str(path_a))
        sid_b = _cwd_session_id(str(path_b))
        assert sid_a != sid_b

    def test_resolves_path(self, tmp_path):
        """Should resolve path before hashing (handles trailing slashes, symlinks)."""
        from gptme.acp.agent import _cwd_session_id

        # Path with trailing slash should resolve to same as without
        sid_plain = _cwd_session_id(str(tmp_path))
        sid_slash = _cwd_session_id(str(tmp_path) + "/")
        assert sid_plain == sid_slash

    def test_hash_length(self, tmp_path):
        """Hash portion should be exactly 8 hex characters."""
        from gptme.acp.agent import _cwd_session_id

        sid = _cwd_session_id(str(tmp_path))
        # Format: "acp-<hash8>"
        assert len(sid) == len("acp-") + 8
        hash_part = sid[len("acp-") :]
        assert all(c in "0123456789abcdef" for c in hash_part)


class TestPromptTurnLoop:
    """Regression tests for complete ACP prompt-turn execution."""

    def test_prompt_records_trace_before_first_persisted_message(self, monkeypatch):
        """ACP task contexts must record their model before LogManager.append()."""
        if not _import_acp():
            pytest.skip("acp not installed")

        import gptme.acp.agent as agent_mod

        agent = GptmeAgent()
        agent._conn = MagicMock()
        agent._conn.session_update = AsyncMock()
        agent._session_commands_advertised.add("s1")
        agent._session_models["s1"] = "openai/gpt-4o-mini"

        log = _make_mock_log()
        log.log = []
        log.workspace = None
        agent._registry.create("s1", log=log)

        trace = MagicMock()
        record = MagicMock(return_value=trace)
        monkeypatch.setattr(agent_mod, "record_runtime_selection", record)

        def append(message):
            assert record.call_count == 1

        log.append.side_effect = append
        monkeypatch.setattr(
            "gptme.util.content.is_message_command", lambda content: True
        )
        monkeypatch.setattr(agent, "_handle_slash_command", AsyncMock())

        _run(
            agent.prompt(
                prompt=[{"type": "text", "text": "/help"}],
                session_id="s1",
            )
        )

        record.assert_called_once_with("openai/gpt-4o-mini", "acp_runtime")

    def test_prompt_continues_after_tool_result(self, monkeypatch):
        """A tool call must be followed by another model step in the same turn."""
        if not _import_acp():
            pytest.skip("acp not installed")

        from gptme.message import Message

        agent = GptmeAgent()
        agent._conn = MagicMock()
        agent._conn.session_update = AsyncMock()
        agent._session_commands_advertised.add("s1")
        agent._session_models["s1"] = "openai/gpt-4o-mini"

        log = _make_mock_log()
        log.log = []
        log.workspace = None
        log.logdir = None
        agent._registry.create("s1", log=log)

        calls: list[list[Message]] = []

        def fake_step(log, **kwargs):
            calls.append(list(log))
            if len(calls) == 1:
                return iter(
                    [
                        Message(
                            "assistant",
                            "```save scratch.txt\nhello\n```",
                        ),
                        Message("system", "Saved to scratch.txt"),
                    ]
                )
            return iter([Message("assistant", "Done")])

        import importlib

        chat_module = importlib.import_module("gptme.chat")
        monkeypatch.setattr(chat_module, "step", fake_step)

        result = _run(
            agent.prompt(
                prompt=[{"type": "text", "text": "create scratch.txt"}],
                session_id="s1",
            )
        )

        assert result.stop_reason == "end_turn"
        assert len(calls) == 2
        assert [message.content for message in calls[1][-2:]] == [
            "```save scratch.txt\nhello\n```",
            "Saved to scratch.txt",
        ]
        assert [call.args[0].content for call in log.append.call_args_list] == [
            "create scratch.txt",
            "```save scratch.txt\nhello\n```",
            "Saved to scratch.txt",
            "Done",
        ]

    def test_prompt_respects_max_steps(self, monkeypatch):
        """ACP prompt loops must honor the same step cap as interactive chat."""
        if not _import_acp():
            pytest.skip("acp not installed")

        import importlib

        from gptme.message import Message

        agent = GptmeAgent()
        agent._conn = MagicMock()
        agent._conn.session_update = AsyncMock()
        agent._session_commands_advertised.add("s1")
        agent._session_models["s1"] = "openai/gpt-4o-mini"

        log = _make_mock_log()
        log.log = []
        log.workspace = None
        log.logdir = None
        agent._registry.create("s1", log=log)

        calls = 0

        def fake_step(log, **kwargs):
            nonlocal calls
            calls += 1
            return iter(
                [
                    Message("assistant", "```save scratch.txt\nhello\n```"),
                    Message("system", "Saved to scratch.txt"),
                ]
            )

        chat_module = importlib.import_module("gptme.chat")
        monkeypatch.setattr(chat_module, "step", fake_step)
        monkeypatch.setenv("GPTME_MAX_STEPS", "2")

        result = _run(
            agent.prompt(
                prompt=[{"type": "text", "text": "create scratch.txt"}],
                session_id="s1",
            )
        )

        assert result.stop_reason == "max_turn_requests"
        assert calls == 2

    def test_prompt_forwards_gptme_max_tokens_to_chat_step(self, monkeypatch):
        """ACP extension kwargs['gptme'] must reach chat.step as max_tokens."""
        if not _import_acp():
            pytest.skip("acp not installed")

        import importlib

        from gptme.message import Message

        agent = GptmeAgent()
        agent._conn = MagicMock()
        agent._conn.session_update = AsyncMock()
        agent._session_commands_advertised.add("s1")
        agent._session_models["s1"] = "openai/gpt-4o-mini"

        log = _make_mock_log()
        log.log = []
        log.workspace = None
        log.logdir = None
        agent._registry.create("s1", log=log)

        seen: dict[str, int | None] = {}

        def fake_step(log, **kwargs):
            seen["max_tokens"] = kwargs.get("max_tokens")
            return iter([Message("assistant", "ok")])

        chat_module = importlib.import_module("gptme.chat")
        monkeypatch.setattr(chat_module, "step", fake_step)

        result = _run(
            agent.prompt(
                prompt=[{"type": "text", "text": "hello"}],
                session_id="s1",
                gptme={"max_tokens": 64},
            )
        )

        assert result.stop_reason == "end_turn"
        assert seen["max_tokens"] == 64


def _make_prompt_agent():
    """An agent with one registered session "s1" ready for prompt()."""
    agent = GptmeAgent()
    agent._conn = MagicMock()
    agent._conn.session_update = AsyncMock()
    agent._session_commands_advertised.add("s1")
    agent._session_models["s1"] = "openai/gpt-4o-mini"
    log = _make_mock_log()
    log.log = []
    log.workspace = None
    log.logdir = None
    agent._registry.create("s1", log=log)
    return agent, log


async def _wait_until(predicate, task, timeout=5.0):
    """Wait for predicate(); fail instead of hanging if prompt() ends first."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if task.done():
            raise AssertionError(f"prompt() ended early: {task.result()!r}")
        if time.monotonic() > deadline:
            task.cancel()
            raise AssertionError("prompt() did not reach the expected point")
        await asyncio.sleep(0.01)


class TestPromptCancel:
    """session/cancel must stop an in-flight prompt turn (#5170)."""

    def test_cancel_stops_tool_loop_and_answers_cancelled(self, monkeypatch):
        """A tool loop that would re-fire forever stops on cancel."""
        if not _import_acp():
            pytest.skip("acp not installed")

        import importlib

        from gptme.message import Message

        agent, log = _make_prompt_agent()
        steps = 0

        def fake_step(log, on_token=None, **kwargs):
            nonlocal steps
            steps += 1
            for _ in range(5):
                on_token("streamed text ")
                time.sleep(0.01)
            return iter(
                [
                    Message("assistant", "```shell\necho X\n```"),
                    Message("system", "X"),
                ]
            )

        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)
        # Bounds the loop if cancel is ignored, so the test fails instead of hanging.
        monkeypatch.setenv("GPTME_MAX_STEPS", "100")

        async def scenario():
            task = asyncio.create_task(
                agent.prompt(prompt=[{"type": "text", "text": "loop"}], session_id="s1")
            )
            await _wait_until(lambda: steps >= 3, task)
            started = time.monotonic()
            await agent.cancel(session_id="s1")
            result = await asyncio.wait_for(task, timeout=3.0)
            elapsed = time.monotonic() - started
            steps_at_return = steps
            updates_at_return = agent._conn.session_update.call_count
            await asyncio.sleep(0.3)
            return result, elapsed, steps_at_return, updates_at_return

        result, elapsed, steps_at_return, updates_at_return = _run(scenario())

        assert result.stop_reason == "cancelled"
        assert elapsed < _CANCEL_GRACE_S + 0.5
        # No further generation or session/update after the turn answered.
        assert steps == steps_at_return
        assert agent._conn.session_update.call_count == updates_at_return
        assert "s1" not in agent._active_turns
        # Completed steps are persisted; the interrupted one is dropped.
        persisted = [call.args[0].content for call in log.append.call_args_list]
        assert persisted[0] == "loop"
        assert len(persisted) % 2 == 1

    def test_cancel_answers_promptly_while_worker_is_blocked(self, monkeypatch):
        """A worker stuck in a tool must not hold session/prompt open."""
        if not _import_acp():
            pytest.skip("acp not installed")

        import importlib

        from gptme.message import Message

        monkeypatch.setattr("gptme.acp.agent._CANCEL_GRACE_S", 0.2)
        agent, log = _make_prompt_agent()
        started_tool = threading.Event()
        release_tool = threading.Event()
        steps = 0

        def fake_step(log, **kwargs):
            nonlocal steps
            steps += 1
            if steps == 2:
                started_tool.set()
                release_tool.wait(timeout=5.0)
            return iter(
                [
                    Message("assistant", f"```shell\necho {steps}\n```"),
                    Message("system", f"out {steps}"),
                ]
            )

        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)
        monkeypatch.setenv("GPTME_MAX_STEPS", "5")

        async def scenario():
            task = asyncio.create_task(
                agent.prompt(
                    prompt=[{"type": "text", "text": "block"}], session_id="s1"
                )
            )
            await _wait_until(started_tool.is_set, task)
            started = time.monotonic()
            await agent.cancel(session_id="s1")
            result = await asyncio.wait_for(task, timeout=2.0)
            elapsed = time.monotonic() - started
            at_return = [c.args[0].content for c in log.append.call_args_list]
            release_tool.set()
            await asyncio.sleep(0.3)
            return result, elapsed, at_return

        try:
            result, elapsed, at_return = _run(scenario())
        finally:
            release_tool.set()

        assert result.stop_reason == "cancelled"
        assert elapsed < 1.0
        # The released worker stops at its next checkpoint, not another step.
        assert steps == 2
        # The step completed before the cancel is persisted with the answer...
        assert at_return == ["block", "```shell\necho 1\n```", "out 1"]
        # ...and the step that finished after it is appended once it stops, so
        # no executed tool goes unrecorded in the conversation.
        persisted = [c.args[0].content for c in log.append.call_args_list]
        assert persisted == at_return + ["```shell\necho 2\n```", "out 2"]

    @pytest.mark.parametrize("late", [False, True])
    def test_cancel_between_tools_preserves_executed_output(
        self, monkeypatch, tmp_path, late
    ):
        """The real tool loop must retain output yielded before its next checkpoint."""
        from gptme.message import Message
        from gptme.tools.base import ToolSpec

        assistant = "```effect\nfirst\n```\n\n```effect\nsecond\n```"
        agent = TestPromptToolCallOutcomes._setup(monkeypatch, [assistant])
        from gptme.hooks.registry import HookRegistry

        agent._hook_registry = HookRegistry()
        log = agent._registry.get("s1").log
        entered = threading.Event()
        release = threading.Event()
        marker = tmp_path / "marker"
        ran = []

        def execute(code, args, kwargs):
            ran.append(code.strip())
            marker.write_text(code.strip())
            entered.set()
            assert release.wait(5.0)
            return Message("system", "effect completed")

        spec = ToolSpec(name="effect", desc="", execute=execute, block_types=["effect"])
        monkeypatch.setattr("gptme.tools.get_tool", {"effect": spec}.get)
        monkeypatch.setattr("gptme.tools.get_tool_for_langtag", {"effect": spec}.get)
        monkeypatch.setattr("gptme.acp.agent._CANCEL_GRACE_S", 0.05 if late else 1.0)

        async def scenario():
            task = asyncio.create_task(
                agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1")
            )
            try:
                await _wait_until(entered.is_set, task)
                await agent.cancel(session_id="s1")
                if late:
                    result = await asyncio.wait_for(task, 2.0)
                    assert [c.args[0].content for c in log.append.call_args_list] == [
                        "go",
                        assistant,
                    ]
                    release.set()
                else:
                    release.set()
                    result = await asyncio.wait_for(task, 2.0)
                deadline = time.monotonic() + 3.0
                while (
                    len(log.append.call_args_list) < 3 and time.monotonic() < deadline
                ):
                    await asyncio.sleep(0.01)
                return result
            finally:
                release.set()

        result = _run(scenario())
        assert result.stop_reason == "cancelled"
        assert marker.read_text() == "first"
        assert ran == ["first"]
        persisted = [c.args[0] for c in log.append.call_args_list]
        assert [m.content for m in persisted] == ["go", assistant, "effect completed"]
        assert persisted[1].metadata["tool_format"] == "markdown"
        assert "tool_ms" not in persisted[1].metadata.get("timings", {})
        assert persisted[2].metadata["timings"]["tool_ms"] >= 0

    @pytest.mark.parametrize("blocked_part", ["worker", "cleanup"])
    def test_reload_waits_for_cancelled_session_writers(
        self, monkeypatch, tmp_path, blocked_part
    ):
        """Reload cannot race an old writer or cleanup, and succeeds once both end."""
        if not _import_acp():
            pytest.skip("acp not installed")
        import importlib

        from gptme.logmanager import LogManager
        from gptme.message import Message

        agent, _ = _make_prompt_agent()
        log = LogManager.load(tmp_path / "s1", create=True, lock=False)
        agent._registry.remove("s1")
        agent._registry.create("s1", log=log)
        monkeypatch.setattr("gptme.acp.agent.get_logs_dir", lambda: tmp_path)
        monkeypatch.setattr("gptme.acp.agent._cwd_session_id", lambda cwd: "s1")
        monkeypatch.setattr("gptme.acp.agent._CANCEL_GRACE_S", 0.05)
        entered = threading.Event()
        release = threading.Event()
        closing = asyncio.Event()
        release_close = asyncio.Event()

        def fake_step(log, **kwargs):
            entered.set()
            assert release.wait(5.0)
            yield Message("assistant", "old result")

        async def close(clients):
            closing.set()
            if blocked_part == "cleanup":
                await asyncio.wait_for(release_close.wait(), 5.0)

        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)
        monkeypatch.setattr(agent, "_close_mcp_clients", close)

        async def scenario():
            task = asyncio.create_task(
                agent.prompt(
                    prompt=[{"type": "text", "text": "old prompt"}], session_id="s1"
                )
            )
            cancel_task = None
            try:
                await _wait_until(entered.is_set, task)
                cancel_task = asyncio.create_task(agent.cancel(session_id="s1"))
                await asyncio.wait_for(closing.wait(), 2.0)
                if blocked_part == "cleanup":
                    release.set()
                assert (await asyncio.wait_for(task, 2.0)).stop_reason == "cancelled"
                assert await agent.load_session(session_id="s1") is None
                with pytest.raises(RuntimeError, match="still finishing"):
                    await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
                assert agent._registry.get("s1") is None
                release.set()
                release_close.set()
                await asyncio.wait_for(cancel_task, 2.0)
                deadline = time.monotonic() + 3.0
                while not any(m.content == "old result" for m in log.log):
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.01)
                assert await agent.load_session(session_id="s1") is not None
                restored = agent._registry.get("s1").log
                restored.append(Message("user", "new prompt"))
                reread = LogManager.load(tmp_path / "s1", lock=False)
                assert [m.content for m in reread.log] == [
                    "old prompt",
                    "old result",
                    "new prompt",
                ]
            finally:
                release.set()
                release_close.set()
                if cancel_task is not None:
                    await asyncio.wait_for(cancel_task, 2.0)

        _run(scenario())

    def test_cancel_during_tool_report_keeps_tool_from_starting(self, monkeypatch):
        """A cancel arriving while the tool call is being reported wins."""
        if not _import_acp():
            pytest.skip("acp not installed")

        import importlib

        from gptme.message import Message

        agent, log = _make_prompt_agent()
        ran: list[str] = []

        async def session_update(**kwargs):
            if isinstance(kwargs.get("update"), dict):
                # The client is slow to take the tool_call report; Stop lands.
                agent._active_turns["s1"].cancel()

        agent._conn.session_update = AsyncMock(side_effect=session_update)

        def fake_step(log, on_tool_execution=None, **kwargs):
            on_tool_execution(0, ToolUse("read", [], "x.txt"))
            ran.append("read")
            return iter([Message("assistant", "```read x.txt\n```")])

        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)

        async def scenario():
            return await asyncio.wait_for(
                agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1"),
                timeout=3.0,
            )

        result = _run(scenario())

        assert result.stop_reason == "cancelled"
        assert ran == []

    def test_cancel_during_final_sends_answers_cancelled(self, monkeypatch):
        """The turn stays cancellable until prompt() answers."""
        if not _import_acp():
            pytest.skip("acp not installed")

        import importlib

        from gptme.message import Message

        agent, log = _make_prompt_agent()
        final_sends = 0

        async def session_update(**kwargs):
            nonlocal final_sends
            if kwargs.get("source") == "gptme":
                final_sends += 1
                # session/cancel arrives while the first final update is sent.
                await agent.cancel(session_id="s1")

        agent._conn.session_update = AsyncMock(side_effect=session_update)

        def fake_step(log, **kwargs):
            return iter(
                [
                    Message("assistant", "done"),
                    Message("system", "note A"),
                    Message("system", "note B"),
                ]
            )

        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)

        async def scenario():
            return await asyncio.wait_for(
                agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1"),
                timeout=3.0,
            )

        result = _run(scenario())

        assert result.stop_reason == "cancelled"
        # No updates after the cancel, but every message is still persisted.
        assert final_sends == 1
        persisted = [c.args[0].content for c in log.append.call_args_list]
        assert persisted == ["go", "done", "note A", "note B"]
        assert "s1" not in agent._active_turns

    def test_prompt_during_cancel_cleanup_does_not_start_a_turn(self, monkeypatch):
        """A prompt sent right after "cancelled" must not run on closing tools."""
        if not _import_acp():
            pytest.skip("acp not installed")

        import importlib

        from gptme.message import Message

        monkeypatch.setattr("gptme.acp.agent._CANCEL_GRACE_S", 0.2)
        agent, log = _make_prompt_agent()
        closing = threading.Event()
        mcp_client = MagicMock()

        def slow_close() -> None:
            closing.set()
            time.sleep(0.5)

        mcp_client.close = slow_close
        agent._session_mcp_clients["s1"] = {"srv": mcp_client}
        steps = 0

        def fake_step(log, on_token=None, **kwargs):
            nonlocal steps
            steps += 1
            on_token("t")
            time.sleep(0.02)
            return iter([Message("assistant", "```shell\necho X\n```")])

        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)
        monkeypatch.setenv("GPTME_MAX_STEPS", "200")

        async def scenario():
            task = asyncio.create_task(
                agent.prompt(prompt=[{"type": "text", "text": "a"}], session_id="s1")
            )
            await _wait_until(lambda: steps >= 1, task)
            cancel_task = asyncio.create_task(agent.cancel(session_id="s1"))
            first = await asyncio.wait_for(task, timeout=3.0)
            # The MCP client is still closing when the client sends again.
            assert closing.is_set() and not cancel_task.done()
            steps_before = steps
            second = await asyncio.wait_for(
                agent.prompt(prompt=[{"type": "text", "text": "b"}], session_id="s1"),
                timeout=3.0,
            )
            await asyncio.wait_for(cancel_task, timeout=3.0)
            return first, second, steps_before

        first, second, steps_before = _run(scenario())

        assert first.stop_reason == "cancelled"
        assert second.stop_reason == "cancelled"
        # The second prompt found the session gone: no turn ran on it.
        assert steps == steps_before
        assert "s1" not in agent._active_turns

    def test_cancelled_permission_answer_does_not_recreate_policy(self):
        """An answer already queued when Stop lands cannot restore allow-always."""
        if not _import_acp():
            pytest.skip("acp not installed")
        agent, _ = _make_prompt_agent()

        async def scenario():
            turn = _AcpTurn(
                agent=agent, session_id="s1", loop=asyncio.get_running_loop()
            )
            agent._active_turns["s1"] = turn

            async def permission(**kwargs):
                await agent.cancel(session_id="s1")
                return _mock_permission_response("allow-always")

            agent._conn.request_permission = AsyncMock(side_effect=permission)
            call = ToolCall("call", "Shell", ToolKind.EXECUTE)
            assert not await agent._request_tool_permission("s1", call)
            assert "s1" not in agent._permission_policies

        _run(scenario())

    def test_cancel_returns_while_final_update_is_blocked(self, monkeypatch):
        """A started transport write may finish, but cannot hold the prompt open."""
        if not _import_acp():
            pytest.skip("acp not installed")
        import importlib

        from gptme.message import Message

        agent, log = _make_prompt_agent()
        sending = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def update(**kwargs):
            sending.set()
            await release.wait()
            finished.set()

        def fake_step(log, on_token=None, **kwargs):
            on_token("short")
            yield Message("assistant", "short")

        agent._conn.session_update = AsyncMock(side_effect=update)
        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)

        async def scenario():
            task = asyncio.create_task(
                agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1")
            )
            try:
                await asyncio.wait_for(sending.wait(), 2.0)
                await agent.cancel(session_id="s1")
                assert (
                    await asyncio.wait_for(asyncio.shield(task), 0.5)
                ).stop_reason == "cancelled"
                assert [c.args[0].content for c in log.append.call_args_list] == [
                    "go",
                    "short",
                ]
                assert not finished.is_set()
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await asyncio.wait_for(finished.wait(), 1.0)

        _run(scenario())

    @pytest.mark.parametrize("update_kind", ["tool_report", "text_flush"])
    def test_update_queued_before_cancel_is_not_sent(self, monkeypatch, update_kind):
        """Stop runs between the worker queueing an update and the loop starting it."""
        if not _import_acp():
            pytest.skip("acp not installed")
        import importlib

        agent, _ = _make_prompt_agent()
        queued = threading.Event()
        submit = asyncio.run_coroutine_threadsafe

        def tracking_submit(coro, loop):
            future = submit(coro, loop)
            queued.set()
            return future

        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", tracking_submit)

        def fake_step(log, on_token=None, on_tool_execution=None, **kwargs):
            turn = agent._active_turns["s1"]
            blocking = threading.Event()

            def stop_once_queued():
                # Holds the loop, so the update cannot start before Stop.
                blocking.set()
                queued.wait(timeout=2.0)
                turn.cancel()
                agent._cleanup_session("s1")

            turn.loop.call_soon_threadsafe(stop_once_queued)
            assert blocking.wait(timeout=2.0)
            if update_kind == "tool_report":
                on_tool_execution(0, ToolUse("read", [], "x.txt"))
            else:
                time.sleep(0.11)  # past FLUSH_INTERVAL, so this token flushes
                on_token("x" * 60)
            return iter([])

        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)

        async def scenario():
            return await asyncio.wait_for(
                agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1"),
                timeout=3.0,
            )

        result = _run(scenario())

        assert queued.is_set()
        assert result.stop_reason == "cancelled"
        agent._conn.session_update.assert_not_called()
        assert "s1" not in agent._tool_calls

    def test_final_send_not_started_before_cancel_is_not_sent(self, monkeypatch):
        """Stop runs between prompt() scheduling a final send and the send starting."""
        if not _import_acp():
            pytest.skip("acp not installed")
        import importlib

        from gptme.message import Message

        agent, _ = _make_prompt_agent()
        create_task = agent._create_background_task
        stopped: list[bool] = []

        def create_then_stop(coro):
            task = create_task(coro)
            if turn := agent._active_turns.get("s1"):
                stopped.append(True)
                turn.cancel()
            return task

        monkeypatch.setattr(agent, "_create_background_task", create_then_stop)

        def fake_step(log, on_token=None, **kwargs):
            on_token("short")
            yield Message("assistant", "short")

        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)

        async def scenario():
            result = await asyncio.wait_for(
                agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1"),
                timeout=3.0,
            )
            await asyncio.sleep(0.05)  # let the scheduled send run
            return result

        result = _run(scenario())

        assert stopped
        assert result.stop_reason == "cancelled"
        agent._conn.session_update.assert_not_called()
        assert not agent._draining_sessions

    def test_permission_send_not_started_before_cancel_is_not_sent(self, monkeypatch):
        """The same ordering for the confirmation path's report and request."""
        if not _import_acp():
            pytest.skip("acp not installed")
        agent, _ = _make_prompt_agent()

        async def scenario():
            loop = asyncio.get_running_loop()
            turn = _AcpTurn(
                agent=agent,
                session_id="s1",
                loop=loop,
                cancel_waiter=loop.create_future(),
            )
            agent._active_turns["s1"] = turn
            create_task = asyncio.create_task

            def create_then_stop(coro, **kwargs):
                loop.call_soon(turn.cancel)  # queued ahead of the new task
                return create_task(coro, **kwargs)

            monkeypatch.setattr(asyncio, "create_task", create_then_stop)
            result = await loop.run_in_executor(
                None, agent._confirm_tool_use, turn, ToolUse("shell", [], "echo x")
            )
            assert result.action == ConfirmAction.SKIP

        _run(scenario())

        agent._conn.session_update.assert_not_called()
        agent._conn.request_permission.assert_not_called()

    def test_cancel_during_step_pre_hook_starts_no_generation(self, monkeypatch):
        """A hook that outlasts Stop must not be followed by a provider request."""
        if not _import_acp():
            pytest.skip("acp not installed")
        import importlib

        from gptme.hooks import HookType

        agent, _ = _make_prompt_agent()
        generations: list[str] = []

        def trigger_hook(hook_type, **kwargs):
            if hook_type == HookType.STEP_PRE:
                turn = agent._active_turns["s1"]
                asyncio.run_coroutine_threadsafe(
                    agent.cancel(session_id="s1"), turn.loop
                ).result(timeout=2.0)
            return iter([])

        def fake_step(log, **kwargs):
            generations.append("started")
            return iter([])

        monkeypatch.setattr(
            importlib.import_module("gptme.hooks.registry"),
            "trigger_hook",
            trigger_hook,
        )
        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)

        async def scenario():
            return await asyncio.wait_for(
                agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1"),
                timeout=3.0,
            )

        result = _run(scenario())

        assert result.stop_reason == "cancelled"
        assert generations == []

    def test_cancel_during_generation_pre_hook_sends_no_request(self, monkeypatch):
        """reply() runs its hooks after the step checkpoint; Stop can land there."""
        if not _import_acp():
            pytest.skip("acp not installed")

        from gptme.hooks import HookType, register_hook, unregister_hook
        from gptme.llm import reply
        from gptme.message import Message

        monkeypatch.setattr("gptme.acp.agent.init", lambda **kwargs: None)
        provider = MagicMock()
        monkeypatch.setattr("gptme.llm._reply_after_hooks", provider)
        agent = GptmeAgent()
        turn = _AcpTurn(agent=agent, session_id="s1", loop=MagicMock())

        def slow_hook(messages, **kwargs):
            turn.cancelled.set()  # session/cancel arrives while this hook runs
            yield from ()

        async def scenario():
            await agent.initialize(protocol_version=1)
            register_hook("test_slow_hook", HookType.GENERATION_PRE, slow_hook)
            token = _acp_turn.set(turn)
            try:
                with pytest.raises(_TurnCancelled):
                    reply([Message("user", "go")], "openai/gpt-4o-mini")
            finally:
                _acp_turn.reset(token)

        try:
            _run(scenario())
        finally:
            unregister_hook("test_slow_hook", HookType.GENERATION_PRE)
            unregister_hook("acp_cancel", HookType.GENERATION_PRE)
            unregister_hook("acp_permission", HookType.TOOL_CONFIRM)

        provider.assert_not_called()

    def test_final_send_failing_as_cancel_lands_answers_cancelled(self, monkeypatch):
        """A cancelled turn ignores the failed write and still saves every message."""
        if not _import_acp():
            pytest.skip("acp not installed")
        import importlib

        from gptme.message import Message

        agent, log = _make_prompt_agent()
        sends = 0

        async def update(**kwargs):
            nonlocal sends
            sends += 1
            if sends > 1:
                await asyncio.Event().wait()  # any retry would hang the prompt
            agent._active_turns["s1"].cancel()
            raise ConnectionError("client went away")

        def fake_step(log, **kwargs):
            return iter(
                [
                    Message("assistant", "done"),
                    Message("system", "note A"),
                    Message("system", "note B"),
                ]
            )

        agent._conn.session_update = AsyncMock(side_effect=update)
        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)

        async def scenario():
            return await asyncio.wait_for(
                agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1"),
                timeout=3.0,
            )

        result = _run(scenario())

        assert result.stop_reason == "cancelled"
        assert sends == 1
        persisted = [c.args[0].content for c in log.append.call_args_list]
        assert persisted == ["go", "done", "note A", "note B"]

    def test_cancel_returns_while_error_report_is_blocked(self, monkeypatch):
        """Stop also releases a prompt that is reporting a failed turn."""
        if not _import_acp():
            pytest.skip("acp not installed")
        import importlib

        agent, _ = _make_prompt_agent()
        sending = asyncio.Event()
        release = asyncio.Event()

        async def update(**kwargs):
            sending.set()
            await release.wait()

        def fake_step(log, **kwargs):
            raise RuntimeError("provider failed")

        agent._conn.session_update = AsyncMock(side_effect=update)
        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)

        async def scenario():
            task = asyncio.create_task(
                agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1")
            )
            try:
                await asyncio.wait_for(sending.wait(), 2.0)
                await agent.cancel(session_id="s1")
                result = await asyncio.wait_for(asyncio.shield(task), 0.5)
                assert result.stop_reason == "cancelled"
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

        _run(scenario())

    def test_cancel_wakes_unanswered_permission(self, monkeypatch):
        """Stop releases the worker without a client permission answer."""
        if not _import_acp():
            pytest.skip("acp not installed")
        import importlib

        from gptme.message import Message

        agent, _ = _make_prompt_agent()
        asked = asyncio.Event()
        request_cancelled = asyncio.Event()
        ran = []

        async def permission(session_id, tool_call):
            asked.set()
            try:
                await asyncio.Future()
            finally:
                request_cancelled.set()

        def fake_step(log, **kwargs):
            turn = agent._active_turns["s1"]
            result = agent._confirm_tool_use(turn, ToolUse("shell", [], "echo X"))
            if result.action == ConfirmAction.CONFIRM:
                ran.append("shell")
            yield Message("assistant", "stopped")

        monkeypatch.setattr(agent, "_request_tool_permission", permission)
        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)
        # Bound the old implementation's wait too, so failure cannot strand a thread.
        monkeypatch.setattr("gptme.acp.agent._PERMISSION_TIMEOUT_S", 2.0)
        monkeypatch.setattr("gptme.acp.agent._CANCEL_GRACE_S", 0.05)

        async def scenario():
            task = asyncio.create_task(
                agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1")
            )
            await asyncio.wait_for(asked.wait(), 2.0)
            await agent.cancel(session_id="s1")
            assert (await asyncio.wait_for(task, 1.0)).stop_reason == "cancelled"
            await asyncio.wait_for(request_cancelled.wait(), 0.5)
            assert not agent._session_is_finishing("s1")

        _run(scenario())
        assert ran == []

    def test_new_session_rechecks_cancel_after_old_clients_close(
        self, monkeypatch, tmp_path
    ):
        """Stop during session/new's MCP close cannot register a second writer."""
        if not _import_acp():
            pytest.skip("acp not installed")
        import importlib

        from gptme.message import Message

        agent, _ = _make_prompt_agent()
        monkeypatch.setattr("gptme.acp.agent.get_logs_dir", lambda: tmp_path)
        monkeypatch.setattr("gptme.acp.agent._cwd_session_id", lambda cwd: "s1")
        monkeypatch.setattr("gptme.acp.agent._CANCEL_GRACE_S", 0.05)
        entered = threading.Event()
        release = threading.Event()
        closing = threading.Event()
        release_close = threading.Event()
        old_client = MagicMock()

        def close():
            closing.set()
            assert release_close.wait(5.0)

        old_client.close.side_effect = close
        agent._session_mcp_clients["s1"] = {"old": old_client}
        # No _session_tools entry: force the client-replacement path.

        def fake_step(log, **kwargs):
            entered.set()
            assert release.wait(5.0)
            yield Message("assistant", "old result")

        monkeypatch.setattr(importlib.import_module("gptme.chat"), "step", fake_step)

        async def scenario():
            task = asyncio.create_task(
                agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1")
            )
            reopen = None
            try:
                await _wait_until(entered.is_set, task)
                reopen = asyncio.create_task(
                    agent.new_session(cwd=str(tmp_path), mcp_servers=[])
                )
                await _wait_until(closing.is_set, reopen)
                await agent.cancel(session_id="s1")
                assert (await asyncio.wait_for(task, 1.0)).stop_reason == "cancelled"
                release_close.set()
                with pytest.raises(RuntimeError, match="still finishing"):
                    await asyncio.wait_for(reopen, 1.0)
                assert agent._registry.get("s1") is None
                assert "s1" not in agent._session_mcp_clients
            finally:
                release.set()
                release_close.set()
                if reopen is not None:
                    await asyncio.gather(reopen, return_exceptions=True)
                await asyncio.gather(task, return_exceptions=True)

        _run(scenario())

    def test_confirm_after_cancel_skips_the_call(self):
        """A call reaching confirmation after cancel must not run."""
        agent = GptmeAgent()
        loop = MagicMock()
        turn = _AcpTurn(agent=agent, session_id="s1", loop=loop)
        turn.cancel()

        result = agent._confirm_tool_use(turn, ToolUse("shell", [], "echo X"))

        assert result.action == ConfirmAction.SKIP
        # Nothing is sent to the client for a cancelled turn.
        loop.call_soon_threadsafe.assert_not_called()

    def test_cancel_without_active_turn_still_cleans_up(self):
        agent = GptmeAgent()
        agent._session_models["idle"] = "m"
        _run(agent.cancel(session_id="idle"))
        assert "idle" not in agent._session_models


class TestPromptErrorHandling:
    """Regression tests for prompt() error-path robustness."""

    def test_prompt_preserves_original_import_error(self, monkeypatch):
        """Early import failure should not be shadowed by unbound batch_buffer."""
        if not _import_acp():
            pytest.skip("acp not installed")

        agent = GptmeAgent()
        agent._conn = MagicMock()
        agent._conn.session_update = AsyncMock()
        agent._session_commands_advertised.add("s1")

        # Minimal in-memory session with log so prompt() can proceed to chat import.
        log = _make_mock_log()
        agent._registry.create("s1", log=log)

        original_import = builtins.__import__

        def _failing_import(name, globals=None, locals=None, fromlist=(), level=0):
            # Match both absolute (name="gptme.chat") and relative (name="chat", level>=1)
            # forms of `from [..]chat import step` since CPython uses the relative form
            # when the import statement itself is a relative import.
            is_chat_step = "step" in (fromlist or ()) and (
                name == "gptme.chat" or (name == "chat" and level >= 1)
            )
            if is_chat_step:
                raise ImportError("boom chat import")
            return original_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", _failing_import)

        result = _run(
            agent.prompt(
                prompt=[{"type": "text", "text": "hello"}],
                session_id="s1",
            )
        )

        assert result.stop_reason == "cancelled"
        assert agent._conn.session_update.await_count >= 1
        error_call = agent._conn.session_update.await_args_list[-1]
        update = error_call.kwargs.get("update")
        assert "boom chat import" in str(update)


class TestHostSuppliedMcpServers:
    """Tests for MCP servers supplied by the ACP host at session creation."""

    def test_descriptor_conversion_and_validation(self):
        if not _import_acp():
            pytest.skip("acp not installed")

        from acp.schema import (
            EnvVariable,
            HttpHeader,
            HttpMcpServer,
            McpServerStdio,
            SseMcpServer,
        )

        from gptme.acp.agent import _mcp_server_configs

        configs = _mcp_server_configs(
            [
                McpServerStdio(
                    name="notebook",
                    command="/usr/bin/python3",
                    args=["server.py"],
                    env=[EnvVariable(name="TOKEN", value="secret")],
                ),
                HttpMcpServer(
                    name="artifacts",
                    url="https://example.test/mcp",
                    headers=[HttpHeader(name="Authorization", value="Bearer test")],
                    type="http",
                ),
            ]
        )

        assert configs[0].name == "notebook"
        assert configs[0].command == "/usr/bin/python3"
        assert configs[0].args == ["server.py"]
        assert configs[0].env == {"TOKEN": "secret"}
        assert configs[1].name == "artifacts"
        assert configs[1].url == "https://example.test/mcp"
        assert configs[1].headers == {"Authorization": "Bearer test"}

        duplicate = McpServerStdio(
            name="notebook", command="/usr/bin/python3", args=[], env=[]
        )
        with pytest.raises(ValueError, match="Duplicate ACP MCP server name"):
            _mcp_server_configs([duplicate, duplicate])
        with pytest.raises(ValueError, match="SSE transport"):
            _mcp_server_configs(
                [
                    SseMcpServer(
                        name="events",
                        url="https://example.test/sse",
                        headers=[],
                        type="sse",
                    )
                ]
            )
        with pytest.raises(ValueError, match="ACP transport"):
            # Dict form avoids AcpMcpServer field-name churn (id vs server_id
            # across SDK versions) while still exercising the type=="acp" reject.
            _mcp_server_configs([{"name": "proxied", "type": "acp"}])
        with pytest.raises(ValueError, match="Malformed ACP MCP server descriptor"):
            _mcp_server_configs([{"name": "broken"}])

    def test_new_session_adds_host_tools_and_cleanup_is_scoped(self, tmp_path):
        if not _import_acp():
            pytest.skip("acp not installed")

        from acp.schema import McpServerStdio

        from gptme.message import Message
        from gptme.tools.base import ToolSpec

        def execute_stub(*_):
            yield Message("system", "ok")

        base_tool = ToolSpec(name="read", desc="Read", execute=execute_stub)
        host_tool = ToolSpec(
            name="notebook.echo", desc="Echo", execute=execute_stub, is_mcp=True
        )
        host_client = MagicMock()
        descriptor = McpServerStdio(
            name="notebook", command="/bin/echo", args=[], env=[]
        )
        agent = GptmeAgent()
        agent._tools = [base_tool]

        def fake_create_mcp_tools(config, *, servers, clients, strict):
            assert strict is True
            assert [server.name for server in servers] == ["notebook"]
            clients["notebook"] = host_client
            return [host_tool]

        with (
            patch("gptme.acp.agent.get_logs_dir", return_value=tmp_path / "logs"),
            patch("gptme.acp.agent.get_prompt", return_value=[]) as get_prompt,
            patch("gptme.acp.agent.ChatConfig"),
            patch(
                "gptme.acp.agent.create_mcp_tools",
                side_effect=fake_create_mcp_tools,
            ),
        ):
            result = _run(
                agent.new_session(cwd=str(tmp_path), mcp_servers=[descriptor])
            )

        session_id = result.session_id
        assert agent._session_tools[session_id] == [base_tool, host_tool]
        assert agent._session_mcp_clients[session_id] == {"notebook": host_client}
        assert get_prompt.call_args.kwargs["tools"] == [base_tool, host_tool]

        clients = agent._cleanup_session(session_id)
        for client in clients:
            client.close()

        host_client.close.assert_called_once_with()
        assert session_id not in agent._session_tools
        assert session_id not in agent._session_mcp_clients

    def test_new_session_strict_failure_closes_partial_clients(self, tmp_path):
        """new_session must close leftover host clients if strict setup raises."""
        if not _import_acp():
            pytest.skip("acp not installed")

        from acp.schema import McpServerStdio

        leftover = MagicMock()

        def fake_create_mcp_tools(config, *, servers, clients, strict):
            clients["ok"] = leftover
            raise RuntimeError("Failed to connect to MCP server 'bad': boom")

        agent = GptmeAgent()
        with (
            patch("gptme.acp.agent.get_logs_dir", return_value=tmp_path / "logs"),
            patch("gptme.acp.agent.get_prompt", return_value=[]),
            patch("gptme.acp.agent.ChatConfig"),
            patch(
                "gptme.acp.agent.create_mcp_tools",
                side_effect=fake_create_mcp_tools,
            ),
            pytest.raises(RuntimeError, match="Failed to connect"),
        ):
            _run(
                agent.new_session(
                    cwd=str(tmp_path),
                    mcp_servers=[
                        McpServerStdio(name="ok", command="/bin/echo", args=[], env=[])
                    ],
                )
            )

        leftover.close.assert_called_once_with()
        assert agent._session_mcp_clients == {}

    def test_host_tool_execute_uses_the_session_client(self):
        from gptme.config import Config
        from gptme.message import Message
        from gptme.tools.mcp_adapter import create_mcp_execute_function

        client = MagicMock()
        client.call_tool.return_value = "session result"
        execute = create_mcp_execute_function(
            "echo", "notebook", Config(), clients={"notebook": client}
        )

        with patch(
            "gptme.tools.mcp_adapter.execute_with_confirmation",
            side_effect=lambda content, args, kwargs, execute_fn, **unused: execute_fn(
                content
            ),
        ):
            result = execute('{"value": "hello"}', None, None)
            assert not isinstance(result, Message)
            messages = list(result)

        assert [message.content for message in messages] == ["session result"]
        client.call_tool.assert_called_once_with("echo", {"value": "hello"})

    def test_shutdown_closes_all_host_clients(self):
        agent = GptmeAgent()
        first = MagicMock()
        second = MagicMock()
        agent._session_mcp_clients = {
            "first": {"one": first},
            "second": {"two": second},
        }

        _run(agent.shutdown())

        first.close.assert_called_once_with()
        second.close.assert_called_once_with()
        assert agent._session_mcp_clients == {}

    def test_two_sessions_keep_host_tools_isolated(self, tmp_path):
        if not _import_acp():
            pytest.skip("acp not installed")

        from acp.schema import McpServerStdio

        from gptme.message import Message
        from gptme.tools.base import ToolSpec

        def execute_stub(*_):
            yield Message("system", "ok")

        agent = GptmeAgent()
        agent._tools = []

        def fake_create_mcp_tools(config, *, servers, clients, strict):
            server = servers[0]
            clients[server.name] = MagicMock()
            return [
                ToolSpec(
                    name=f"{server.name}.echo",
                    desc="Echo",
                    execute=execute_stub,
                    is_mcp=True,
                )
            ]

        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        with (
            patch("gptme.acp.agent.get_logs_dir", return_value=tmp_path / "logs"),
            patch("gptme.acp.agent.get_prompt", return_value=[]),
            patch("gptme.acp.agent.ChatConfig"),
            patch(
                "gptme.acp.agent.create_mcp_tools",
                side_effect=fake_create_mcp_tools,
            ),
        ):
            first_result = _run(
                agent.new_session(
                    cwd=str(first),
                    mcp_servers=[
                        McpServerStdio(
                            name="first", command="/bin/echo", args=[], env=[]
                        )
                    ],
                )
            )
            second_result = _run(
                agent.new_session(
                    cwd=str(second),
                    mcp_servers=[
                        McpServerStdio(
                            name="second", command="/bin/echo", args=[], env=[]
                        )
                    ],
                )
            )

        assert [
            tool.name for tool in agent._session_tools[first_result.session_id]
        ] == ["first.echo"]
        assert [
            tool.name for tool in agent._session_tools[second_result.session_id]
        ] == ["second.echo"]
        assert (
            agent._session_mcp_clients[first_result.session_id]
            is not agent._session_mcp_clients[second_result.session_id]
        )


class TestNewSessionResume:
    """Tests for session resume logic in new_session()."""

    def test_session_id_deterministic_from_cwd(self, tmp_path):
        """new_session with cwd produces the expected deterministic session ID."""
        if not _import_acp():
            pytest.skip("acp not installed")

        from unittest.mock import patch

        from gptme.acp.agent import _cwd_session_id

        cwd = str(tmp_path)
        expected_sid = _cwd_session_id(cwd)

        agent = GptmeAgent()

        with (
            patch("gptme.acp.agent.get_logs_dir", return_value=tmp_path),
            patch("gptme.acp.agent.get_prompt", return_value=[]),
            patch("gptme.acp.agent.get_tools", return_value=[]),
            patch("gptme.acp.agent.ChatConfig"),
        ):
            result = _run(agent.new_session(cwd=cwd, mcp_servers=[]))

        assert result.session_id == expected_sid

    def test_session_id_reused_in_memory(self, tmp_path):
        """When same CWD is passed twice, the same in-memory session is reused."""
        if not _import_acp():
            pytest.skip("acp not installed")

        import json
        from unittest.mock import patch

        from gptme.acp.agent import _cwd_session_id

        cwd = str(tmp_path / "my-project")
        (tmp_path / "my-project").mkdir()
        expected_sid = _cwd_session_id(cwd)

        # Create a minimal log dir so new_session can resume from disk
        logdir = tmp_path / expected_sid
        logdir.mkdir()
        logfile = logdir / "conversation.jsonl"
        entry = {
            "role": "system",
            "content": "hello",
            "timestamp": "2025-01-01T00:00:00+00:00",
        }
        logfile.write_text(json.dumps(entry) + "\n")

        agent = GptmeAgent()

        with (
            patch("gptme.acp.agent.get_logs_dir", return_value=tmp_path),
            patch("gptme.acp.agent.get_prompt", return_value=[]),
            patch("gptme.acp.agent.get_tools", return_value=[]),
            patch("gptme.acp.agent.ChatConfig"),
        ):
            result1 = _run(agent.new_session(cwd=cwd, mcp_servers=[]))
            result2 = _run(agent.new_session(cwd=cwd, mcp_servers=[]))

        # Both calls should return the same session ID
        assert result1.session_id == result2.session_id == expected_sid

    def test_registry_log_updated_when_resumed_from_disk_with_null_log(self, tmp_path):
        """When session exists in registry with log=None but file exists on disk,
        new_session() should update the registry's log reference (resumed=True path)."""
        if not _import_acp():
            pytest.skip("acp not installed")

        import json
        from unittest.mock import patch

        from gptme.acp.agent import _cwd_session_id

        cwd = str(tmp_path / "project")
        (tmp_path / "project").mkdir()
        sid = _cwd_session_id(cwd)

        # Create log on disk
        logdir = tmp_path / sid
        logdir.mkdir()
        logfile = logdir / "conversation.jsonl"
        entry = {
            "role": "system",
            "content": "hello",
            "timestamp": "2025-01-01T00:00:00+00:00",
        }
        logfile.write_text(json.dumps(entry) + "\n")

        agent = GptmeAgent()
        # Pre-populate registry with log=None (simulates race/partial init)
        agent._registry.create(sid, log=None, cwd=cwd)

        with (
            patch("gptme.acp.agent.get_logs_dir", return_value=tmp_path),
            patch("gptme.acp.agent.get_prompt", return_value=[]),
            patch("gptme.acp.agent.get_tools", return_value=[]),
            patch("gptme.acp.agent.ChatConfig"),
        ):
            _run(agent.new_session(cwd=cwd, mcp_servers=[]))

        # Registry entry should now have a valid log reference
        session = agent._registry.get(sid)
        assert session is not None
        assert session.log is not None

    def test_send_session_open_notifications_resumed_flag(self):
        """_send_session_open_notifications includes 'Resumed session' when resumed=True."""
        if not _import_acp():
            pytest.skip("acp not installed")

        agent = _make_agent_with_conn()
        _run(
            agent._send_session_open_notifications(
                "session_1", "anthropic/claude-haiku-4-5", "/tmp", resumed=True
            )
        )
        assert agent._conn.session_update.await_count >= 1
        call_args = agent._conn.session_update.await_args_list[0]
        update = call_args.kwargs.get("update") or call_args.args[0]
        assert "Resumed session" in str(update)

    def test_send_session_open_notifications_new_session_flag(self):
        """_send_session_open_notifications includes 'New session' when resumed=False."""
        if not _import_acp():
            pytest.skip("acp not installed")

        agent = _make_agent_with_conn()
        _run(
            agent._send_session_open_notifications(
                "session_1", "anthropic/claude-haiku-4-5", "/tmp", resumed=False
            )
        )
        call_args = agent._conn.session_update.await_args_list[0]
        update = call_args.kwargs.get("update") or call_args.args[0]
        assert "New session" in str(update)


class TestPromptToolCallOutcomes:
    """Each ACP tool call is reported, and settled from its own result.

    These run the real chat.step -> execute_msg -> ToolUse.execute path; only
    the model reply and the tool registry are faked.
    """

    @staticmethod
    def _tool_events(agent) -> list[tuple[str, str]]:
        """(event, status) pairs of the tool-call session_updates, in send order."""
        events = []
        for call in agent._conn.session_update.call_args_list:
            update = call.kwargs["update"]
            if not isinstance(update, dict):
                continue
            kind = update.get("sessionUpdate")
            if kind in ("tool_call", "tool_call_update"):
                events.append((update["toolCallId"], update["status"]))
        return events

    @staticmethod
    def _setup(monkeypatch, replies: list[str | BaseException]):
        if not _import_acp():
            pytest.skip("acp not installed")

        from gptme.message import Message
        from gptme.tools.base import ToolSpec

        def okay(code, args, kwargs):
            return Message("system", f"ran {code.strip()}")

        def boom(code, args, kwargs):
            raise RuntimeError("tool exploded")

        specs = {
            "okay": ToolSpec(name="okay", desc="", execute=okay, block_types=["okay"]),
            "boom": ToolSpec(name="boom", desc="", execute=boom, block_types=["boom"]),
        }
        monkeypatch.setattr("gptme.tools.get_tool", specs.get)
        monkeypatch.setattr("gptme.tools.get_tool_for_langtag", specs.get)

        pending = iter(replies)

        def fake_reply(**kwargs):
            content = next(pending)
            if isinstance(content, BaseException):
                raise content
            return Message("assistant", content)

        import importlib

        # `gptme.chat` the attribute is the chat() function; patch the module.
        chat_module = importlib.import_module("gptme.chat")
        monkeypatch.setattr(chat_module, "_reply_with_overflow_recovery", fake_reply)
        monkeypatch.setattr(
            chat_module, "prepare_messages", lambda msgs, *args, **kwargs: msgs
        )

        agent = GptmeAgent()
        agent._conn = MagicMock()
        agent._conn.session_update = AsyncMock()
        agent._session_commands_advertised.add("s1")
        agent._session_models["s1"] = "openai/gpt-4o-mini"
        log = _make_mock_log()
        log.log = []
        log.workspace = None
        log.logdir = None
        agent._registry.create("s1", log=log)
        return agent

    def test_raising_tool_in_successful_turn_is_failed(self, monkeypatch):
        """A tool that raised is FAILED even though the turn itself ends normally."""
        agent = self._setup(
            monkeypatch,
            ["```okay\nfirst\n```\n\n```boom\nsecond\n```", "All done."],
        )

        result = _run(
            agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1")
        )

        assert result.stop_reason == "end_turn"
        events = self._tool_events(agent)
        call_ids = list(dict.fromkeys(call_id for call_id, _ in events))
        assert len(call_ids) == 2
        okay_id, boom_id = call_ids
        assert events == [
            (okay_id, "in_progress"),
            (boom_id, "in_progress"),
            (okay_id, "completed"),
            (boom_id, "failed"),
        ]

    def test_completed_call_stays_completed_when_a_later_step_raises(self, monkeypatch):
        """An exception later in the turn fails only the calls it left open."""
        agent = self._setup(
            monkeypatch,
            ["```okay\nfirst\n```", RuntimeError("provider went away")],
        )

        result = _run(
            agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1")
        )

        assert result.stop_reason == "cancelled"
        events = self._tool_events(agent)
        assert [status for _, status in events] == ["in_progress", "completed"]
        assert len({call_id for call_id, _ in events}) == 1

    @pytest.mark.parametrize(
        ("terminal_status", "tool"), [("completed", "okay"), ("failed", "boom")]
    )
    def test_prompt_retries_terminal_delivery_without_changing_outcome(
        self, monkeypatch, terminal_status, tool
    ):
        agent = self._setup(
            monkeypatch, [f"```{tool}\nrun\n```", RuntimeError("provider went away")]
        )
        delivered = []
        failed_once = False

        async def send(**kwargs):
            nonlocal failed_once
            update = kwargs["update"]
            if (
                isinstance(update, dict)
                and update.get("sessionUpdate") == "tool_call_update"
            ):
                if not failed_once:
                    failed_once = True
                    raise RuntimeError("temporary send failure")
                delivered.append(update["status"])

        agent._conn.session_update.side_effect = send
        result = _run(
            agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1")
        )

        assert result.stop_reason == "cancelled"
        assert failed_once
        assert delivered == [terminal_status]
        assert not agent._tool_calls["s1"]

    def test_call_unloaded_by_previous_call_is_not_reported(self, monkeypatch):
        """Availability is checked at execution, not at the start of the step."""
        agent = self._setup(
            monkeypatch,
            ["```okay\nunload boom\n```\n\n```boom\nnever runs\n```", "Done."],
        )
        from gptme.message import Message
        from gptme.tools import get_tool
        from gptme.tools.base import ToolSpec

        specs = {name: get_tool(name) for name in ("okay", "boom")}
        # Parsing still recognizes the code block after unload, but execution
        # must observe the current registry, as it does for MCP tool removal.
        monkeypatch.setattr("gptme.tools.get_tool", specs.get)

        def unload(code, args, kwargs):
            specs.pop("boom")
            return Message("system", "unloaded boom")

        specs["okay"] = ToolSpec(
            name="okay", desc="", execute=unload, block_types=["okay"]
        )
        _run(agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1"))

        events = self._tool_events(agent)
        assert [status for _, status in events] == ["in_progress", "completed"]
        assert len({call_id for call_id, _ in events}) == 1

    def test_call_denied_by_pre_hook_is_failed(self, monkeypatch):
        """A call a tool.execute.pre hook denied never ran, so it is FAILED."""
        agent = self._setup(monkeypatch, ["```okay\nfirst\n```", "Understood."])

        from gptme.hooks import HookType
        from gptme.hooks.types import ToolExecutePreData

        def hook(hook_type, data=None, **kwargs):
            if hook_type == HookType.TOOL_EXECUTE_PRE and isinstance(
                data, ToolExecutePreData
            ):
                data.deny("blocked by policy")
            return []

        monkeypatch.setattr("gptme.hooks.trigger_hook", hook)

        _run(agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1"))

        assert [status for _, status in self._tool_events(agent)] == [
            "in_progress",
            "failed",
        ]

    def test_tool_output_is_tool_call_content_not_agent_text(self, monkeypatch):
        """A call's output rides its tool_call_update, not a separate agent message."""
        agent = self._setup(
            monkeypatch,
            ["```okay\nfirst\n```\n\n```boom\nsecond\n```", "All done."],
        )

        _run(agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1"))

        updates = [
            c.kwargs["update"] for c in agent._conn.session_update.call_args_list
        ]
        updates = [u for u in updates if isinstance(u, dict)]
        id_by_tool = {
            u["title"].removeprefix("Executing "): u["toolCallId"]
            for u in updates
            if u.get("sessionUpdate") == "tool_call"
        }
        assert set(id_by_tool) == {"okay", "boom"}
        by_id = {
            u["toolCallId"]: u
            for u in updates
            if u.get("sessionUpdate") == "tool_call_update"
        }

        def output_of(tool: str) -> str:
            content = by_id[id_by_tool[tool]]["content"]
            assert [c["type"] for c in content] == ["content"]
            return content[0]["content"]["text"]

        # Each call carries its own output and none of the other call's.
        assert "ran first" in output_of("okay")
        assert "tool exploded" not in output_of("okay")
        assert "tool exploded" in output_of("boom")
        assert "ran first" not in output_of("boom")

        agent_text = "".join(
            str(u.get("content"))
            for u in updates
            if u.get("sessionUpdate") == "agent_message_chunk"
        )
        assert "ran first" not in agent_text
        assert "tool exploded" not in agent_text

    def test_tool_output_is_still_logged(self, monkeypatch):
        """Moving output into tool-call content must not drop it from the log."""
        agent = self._setup(monkeypatch, ["```okay\nfirst\n```", "Done."])

        _run(agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1"))

        log = agent._registry.get("s1").log
        appended = [c.args[0].content for c in log.append.call_args_list]
        assert any("ran first" in text for text in appended)


class TestPromptToolPermission:
    """In "default" mode a confirming tool asks the client before it runs."""

    @staticmethod
    def _setup(monkeypatch, replies: list[str], permission, patch_hooks=True):
        if not _import_acp():
            pytest.skip("acp not installed")

        import importlib

        from gptme.acp.agent import _acp_confirm_hook
        from gptme.hooks import HookType
        from gptme.hooks.types import Hook
        from gptme.message import Message
        from gptme.tools.base import ToolSpec
        from gptme.util.ask_execute import execute_with_confirmation

        ran: list[str] = []

        def run(code, path):
            ran.append(code)
            yield Message("system", f"ran {code.strip()}")

        def guarded(code, args, kwargs):
            # Same confirmation path as the real shell/save tools.
            return execute_with_confirmation(
                code,
                args,
                kwargs,
                execute_fn=run,
                get_path_fn=lambda *a: None,
                allow_edit=False,
            )

        spec = ToolSpec(
            name="guarded", desc="", execute=guarded, block_types=["guarded"]
        )
        monkeypatch.setattr("gptme.tools.get_tool", {"guarded": spec}.get)
        monkeypatch.setattr("gptme.tools.get_tool_for_langtag", {"guarded": spec}.get)

        pending = iter(replies)
        chat_module = importlib.import_module("gptme.chat")
        monkeypatch.setattr(
            chat_module,
            "_reply_with_overflow_recovery",
            lambda **kwargs: Message("assistant", next(pending)),
        )
        monkeypatch.setattr(
            chat_module, "prepare_messages", lambda msgs, *args, **kwargs: msgs
        )

        # initialize() registers this hook; serve it as the only TOOL_CONFIRM
        # hook so no other registered confirm hook decides instead.
        acp_hook = Hook(
            name="acp_permission",
            hook_type=HookType.TOOL_CONFIRM,
            func=_acp_confirm_hook,
            priority=100,
        )
        if patch_hooks:
            monkeypatch.setattr(
                "gptme.hooks.get_hooks",
                lambda hook_type=None: (
                    [acp_hook] if hook_type == HookType.TOOL_CONFIRM else []
                ),
            )

        agent = GptmeAgent()
        agent._conn = MagicMock()
        agent._conn.session_update = AsyncMock()
        agent._conn.request_permission = AsyncMock(side_effect=permission)
        agent._session_commands_advertised.add("s1")
        agent._session_models["s1"] = "openai/gpt-4o-mini"
        log = _make_mock_log()
        log.log = []
        log.workspace = None
        log.logdir = None
        agent._registry.create("s1", log=log)
        return agent, ran

    @staticmethod
    def _statuses(agent) -> list[str]:
        return [
            call.kwargs["update"]["status"]
            for call in agent._conn.session_update.call_args_list
            if isinstance(call.kwargs["update"], dict)
            and call.kwargs["update"].get("sessionUpdate")
            in ("tool_call", "tool_call_update")
        ]

    def _prompt(self, agent):
        return _run(
            agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1")
        )

    def test_rejected_call_does_not_run_and_is_failed(self, monkeypatch):
        agent, ran = self._setup(
            monkeypatch,
            ["```guarded\nrm -rf build\n```", "Okay, I won't."],
            lambda **kwargs: _mock_permission_response("reject-once"),
        )

        assert self._prompt(agent).stop_reason == "end_turn"

        assert ran == []
        request = agent._conn.request_permission.call_args.kwargs
        reported_id = agent._conn.session_update.call_args_list[0].kwargs["update"][
            "toolCallId"
        ]
        assert request["tool_call"] == {"toolCallId": reported_id}
        assert self._statuses(agent) == ["in_progress", "failed"]

    def test_allowed_call_runs_and_completes(self, monkeypatch):
        agent, ran = self._setup(
            monkeypatch,
            ["```guarded\nls\n```", "Done."],
            lambda **kwargs: _mock_permission_response("allow-once"),
        )

        self._prompt(agent)

        assert ran == ["ls"]
        assert agent._conn.request_permission.call_count == 1
        assert self._statuses(agent) == ["in_progress", "completed"]

    def test_repeated_direct_confirmation_names_its_own_call(self, monkeypatch):
        """Repeated command text must request permission for distinct parsed calls."""
        answers = iter(["allow-once", "reject-once"])
        agent, ran = self._setup(
            monkeypatch,
            ["```guarded\nls\n```\n\n```guarded\nls\n```", "Done."],
            lambda **kwargs: _mock_permission_response(next(answers)),
        )

        self._prompt(agent)

        assert ran == ["ls"]
        updates = [
            call.kwargs["update"]
            for call in agent._conn.session_update.call_args_list
            if isinstance(call.kwargs["update"], dict)
        ]
        reported = [
            u["toolCallId"] for u in updates if u.get("sessionUpdate") == "tool_call"
        ]
        assert len(reported) == 2
        requested = [
            call.kwargs["tool_call"]["toolCallId"]
            for call in agent._conn.request_permission.call_args_list
        ]
        assert requested == reported
        final = {
            u["toolCallId"]: u["status"]
            for u in updates
            if u.get("sessionUpdate") == "tool_call_update"
        }
        assert final == {reported[0]: "completed", reported[1]: "failed"}

    def test_repeated_helper_confirmation_names_its_own_call(self, monkeypatch):
        """Two equal helper calls in one message: each permission names its own call.

        The helper asks under its owning tool, so it is tied to the running call
        by content; rejecting the second must fail the second, not the first
        that already ran.
        """
        from gptme.constants import DECLINED_CONTENT
        from gptme.hooks import ConfirmAction, get_confirmation
        from gptme.message import Message
        from gptme.tools.base import ToolSpec, ToolUse

        answers = iter(["allow-once", "reject-once"])
        agent, ran = self._setup(
            monkeypatch,
            ["```pyish\nhelper('x')\n```\n\n```pyish\nhelper('x')\n```", "Done."],
            lambda **kwargs: _mock_permission_response(next(answers)),
        )

        def pyish(code, args, kwargs):
            confirm = get_confirmation(tool_use=ToolUse("owner", [], code))
            if confirm.action != ConfirmAction.CONFIRM:
                yield Message("system", DECLINED_CONTENT)
                return
            ran.append(code)
            yield Message("system", "ran")

        spec = ToolSpec(name="pyish", desc="", execute=pyish, block_types=["pyish"])
        monkeypatch.setattr("gptme.tools.get_tool", {"pyish": spec}.get)
        monkeypatch.setattr("gptme.tools.get_tool_for_langtag", {"pyish": spec}.get)

        self._prompt(agent)

        assert ran == ["helper('x')"]
        updates = [
            call.kwargs["update"]
            for call in agent._conn.session_update.call_args_list
            if isinstance(call.kwargs["update"], dict)
        ]
        reported = [
            u["toolCallId"] for u in updates if u.get("sessionUpdate") == "tool_call"
        ]
        assert len(reported) == 2
        requested = [
            call.kwargs["tool_call"]["toolCallId"]
            for call in agent._conn.request_permission.call_args_list
        ]
        assert requested == reported
        final = {
            u["toolCallId"]: u["status"]
            for u in updates
            if u.get("sessionUpdate") == "tool_call_update"
        }
        assert final == {reported[0]: "completed", reported[1]: "failed"}

    @pytest.mark.parametrize(
        ("reply", "tool_format"),
        [
            ("```pyish\nhelper('x')\n```", "markdown"),
            ("```pyish\n\nhelper('x')  \n\n```", "markdown"),
            ('@pyish(call_1): {"code": "  helper(\'x\')\\n"}', "tool"),
        ],
        ids=["markdown", "padded-markdown", "native-tool-call"],
    )
    def test_helper_confirmation_reuses_the_running_call(
        self, monkeypatch, reply, tool_format
    ):
        """A helper confirming under its owning tool must settle the original call.

        execute_python asks as e.g. ``vision`` for ``view_image(...)`` while the
        reported call is ``ipython``; a rejection must fail that call, not a new one.
        It confirms the stripped code, and native calls report it as a kwarg.
        """
        from gptme.constants import DECLINED_CONTENT
        from gptme.hooks import ConfirmAction, get_confirmation
        from gptme.message import Message
        from gptme.tools.base import ToolSpec, ToolUse

        agent, ran = self._setup(
            monkeypatch,
            [reply, "Okay."],
            lambda **kwargs: _mock_permission_response("reject-once"),
        )

        def pyish(code, args, kwargs):
            code = (code if code is not None else kwargs["code"]).strip()
            confirm = get_confirmation(tool_use=ToolUse("owner", [], code))
            if confirm.action != ConfirmAction.CONFIRM:
                yield Message("system", DECLINED_CONTENT)
                return
            ran.append(code)
            yield Message("system", "ran")

        spec = ToolSpec(name="pyish", desc="", execute=pyish, block_types=["pyish"])
        monkeypatch.setattr("gptme.tools.get_tool", {"pyish": spec}.get)
        monkeypatch.setattr("gptme.tools.get_tool_for_langtag", {"pyish": spec}.get)
        monkeypatch.setattr("gptme.tools.base.get_tool_format", lambda: tool_format)

        self._prompt(agent)

        assert ran == []
        request = agent._conn.request_permission.call_args.kwargs
        reported_id = agent._conn.session_update.call_args_list[0].kwargs["update"][
            "toolCallId"
        ]
        assert request["tool_call"] == {"toolCallId": reported_id}
        assert self._statuses(agent) == ["in_progress", "failed"]

    def test_timed_out_permission_is_cancelled_without_caching_policy(
        self, monkeypatch
    ):
        """Cancelling run_coroutine_threadsafe's future cancels its asyncio task."""
        cancelled = asyncio.Event()

        async def permission(**kwargs):
            try:
                await asyncio.sleep(60)
                return _mock_permission_response("allow-always")
            except asyncio.CancelledError:
                cancelled.set()
                raise

        agent, ran = self._setup(
            monkeypatch,
            ["```guarded\nls\n```", "Okay."],
            permission,
        )
        monkeypatch.setattr("gptme.acp.agent._PERMISSION_TIMEOUT_S", 0.05)

        async def serve():
            await agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1")
            await asyncio.wait_for(cancelled.wait(), timeout=1)

        _run(serve())

        assert ran == []
        assert agent._permission_policies == {}
        assert self._statuses(agent) == ["in_progress", "failed"]

    def test_auto_mode_does_not_ask(self, monkeypatch):
        agent, ran = self._setup(
            monkeypatch,
            ["```guarded\nls\n```", "Done."],
            lambda **kwargs: pytest.fail("auto mode must not ask"),
        )
        agent._session_modes["s1"] = "auto"

        self._prompt(agent)

        assert ran == ["ls"]
        assert self._statuses(agent) == ["in_progress", "completed"]

    def test_initialize_registers_the_confirm_hook(self, monkeypatch):
        """initialize() must install the hook: init(interactive=False) adds none."""
        if not _import_acp():
            pytest.skip("acp not installed")

        from gptme.hooks import HookType, get_hooks, unregister_hook

        monkeypatch.setattr("gptme.acp.agent.init", lambda **kwargs: None)
        unregister_hook("acp_permission", HookType.TOOL_CONFIRM)
        try:
            _run(GptmeAgent().initialize(protocol_version=1))
            names = [h.name for h in get_hooks(HookType.TOOL_CONFIRM)]
            assert "acp_permission" in names
        finally:
            unregister_hook("acp_permission", HookType.TOOL_CONFIRM)

    def test_hook_from_initialize_reaches_a_prompt_in_another_task(self, monkeypatch):
        """ACP runs each request in its own task; the registry is a ContextVar.

        Hooks registered by initialize() must still fire during a later prompt.
        """
        import contextvars

        agent, ran = self._setup(
            monkeypatch,
            ["```guarded\nrm -rf build\n```", "Okay."],
            lambda **kwargs: _mock_permission_response("reject-once"),
            patch_hooks=False,
        )
        monkeypatch.setattr("gptme.acp.agent.init", lambda **kwargs: None)

        async def serve():
            # Two requests, two tasks, as acp's task supervisor runs them.
            await asyncio.create_task(agent.initialize(protocol_version=1))
            await asyncio.create_task(
                agent.prompt(prompt=[{"type": "text", "text": "go"}], session_id="s1")
            )

        # A fresh context: no hook registry exists before the first request.
        contextvars.Context().run(asyncio.run, serve())

        assert ran == []
        assert agent._conn.request_permission.call_count == 1
