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


class TestGptmeAgentInit:
    """Tests for GptmeAgent initialization."""

    def test_create(self):
        agent = GptmeAgent()
        assert agent._conn is None
        assert agent._initialized is False
        assert agent._model == "anthropic/claude-sonnet-4-20250514"
        assert agent._tool_calls == {}
        assert agent._permission_policies == {}

    def test_on_connect(self):
        agent = GptmeAgent()
        mock_conn = MagicMock()
        agent.on_connect(mock_conn)
        assert agent._conn is mock_conn

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
            return_value={"outcome": {"optionId": "allow-once"}}
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
            return_value={"outcome": {"optionId": "allow-always"}}
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
            return_value={"outcome": {"optionId": "reject-always"}}
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
            return_value={"outcome": {"outcome": "cancelled"}}
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
            return_value={"outcome": {"optionId": "allow-once"}}
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
            return_value={"outcome": {"optionId": "allow-once"}}
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
            return_value={"outcome": {"optionId": "allow-once"}}
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
            return_value={"outcome": {"optionId": "reject-once"}}
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
