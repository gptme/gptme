"""Unit tests for subagent module pure-logic functions.

Tests the refactored subagent package (hooks, types, batch) without
requiring API keys or running actual LLM calls.
"""

import queue
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gptme.tools.subagent.batch import BatchJob
from gptme.tools.subagent.hooks import (
    _get_complete_instruction,
    _subagent_completion_hook,
    notify_completion,
)
from gptme.tools.subagent.types import (
    ReturnType,
    Subagent,
    _completion_queue,
)

# ---------------------------------------------------------------------------
# ReturnType tests
# ---------------------------------------------------------------------------


class TestReturnType:
    def test_default_result_is_none(self):
        rt = ReturnType("running")
        assert rt.status == "running"
        assert rt.result is None

    def test_success_with_result(self):
        rt = ReturnType("success", "task done")
        assert rt.status == "success"
        assert rt.result == "task done"

    def test_failure_with_result(self):
        rt = ReturnType("failure", "something broke")
        assert rt.status == "failure"
        assert rt.result == "something broke"

    def test_frozen(self):
        rt = ReturnType("running")
        with pytest.raises(AttributeError):
            rt.status = "success"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _get_complete_instruction tests
# ---------------------------------------------------------------------------


class TestGetCompleteInstruction:
    def test_default_target(self):
        instruction = _get_complete_instruction()
        assert "orchestrator" in instruction
        assert "```complete" in instruction

    def test_custom_target(self):
        instruction = _get_complete_instruction("user")
        assert "user" in instruction
        assert "orchestrator" not in instruction

    def test_contains_complete_tool_block(self):
        instruction = _get_complete_instruction()
        assert "```complete" in instruction
        assert "Your complete answer here." in instruction


# ---------------------------------------------------------------------------
# notify_completion + _subagent_completion_hook tests
# ---------------------------------------------------------------------------


class TestCompletionNotifications:
    def setup_method(self):
        """Drain the global completion queue before each test."""
        while not _completion_queue.empty():
            try:
                _completion_queue.get_nowait()
            except queue.Empty:
                break

    def test_notify_adds_to_queue(self):
        notify_completion("agent-1", "success", "done")
        assert not _completion_queue.empty()
        agent_id, status, summary = _completion_queue.get_nowait()
        assert agent_id == "agent-1"
        assert status == "success"
        assert summary == "done"

    def test_hook_yields_success_message(self):
        notify_completion("agent-2", "success", "all good")
        manager = MagicMock()
        messages = list(
            _subagent_completion_hook(manager, interactive=False, prompt_queue=None)
        )
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "agent-2" in messages[0].content
        assert "completed" in messages[0].content

    def test_hook_yields_failure_message(self):
        notify_completion("agent-3", "failure", "crashed")
        manager = MagicMock()
        messages = list(
            _subagent_completion_hook(manager, interactive=False, prompt_queue=None)
        )
        assert len(messages) == 1
        assert "failed" in messages[0].content

    def test_hook_drains_multiple(self):
        notify_completion("a", "success", "ok")
        notify_completion("b", "failure", "bad")
        notify_completion("c", "success", "fine")
        manager = MagicMock()
        messages = list(
            _subagent_completion_hook(manager, interactive=False, prompt_queue=None)
        )
        assert len(messages) == 3

    def test_hook_yields_nothing_when_empty(self):
        manager = MagicMock()
        messages = list(
            _subagent_completion_hook(manager, interactive=False, prompt_queue=None)
        )
        assert len(messages) == 0


# ---------------------------------------------------------------------------
# Subagent.is_running tests
# ---------------------------------------------------------------------------


class TestSubagentIsRunning:
    _logdir = Path("/tmp/test-log")

    def test_no_thread_no_process_not_running(self):
        sa = Subagent(
            agent_id="test", prompt="x", thread=None, logdir=self._logdir, model=None
        )
        assert sa.is_running() is False

    def test_thread_alive_is_running(self):
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        sa = Subagent(
            agent_id="test",
            prompt="x",
            thread=mock_thread,
            logdir=self._logdir,
            model=None,
        )
        assert sa.is_running() is True

    def test_thread_dead_not_running(self):
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        sa = Subagent(
            agent_id="test",
            prompt="x",
            thread=mock_thread,
            logdir=self._logdir,
            model=None,
        )
        assert sa.is_running() is False

    def test_subprocess_running(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        sa = Subagent(
            agent_id="test",
            prompt="x",
            thread=None,
            logdir=self._logdir,
            model=None,
            execution_mode="subprocess",
            process=mock_proc,
        )
        assert sa.is_running() is True

    def test_subprocess_finished(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # exited
        sa = Subagent(
            agent_id="test",
            prompt="x",
            thread=None,
            logdir=self._logdir,
            model=None,
            execution_mode="subprocess",
            process=mock_proc,
        )
        assert sa.is_running() is False

    def test_acp_thread_alive(self):
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        sa = Subagent(
            agent_id="test",
            prompt="x",
            thread=mock_thread,
            logdir=self._logdir,
            model=None,
            execution_mode="acp",
        )
        assert sa.is_running() is True

    def test_acp_thread_dead(self):
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        sa = Subagent(
            agent_id="test",
            prompt="x",
            thread=mock_thread,
            logdir=self._logdir,
            model=None,
            execution_mode="acp",
        )
        assert sa.is_running() is False


# ---------------------------------------------------------------------------
# BatchJob tests
# ---------------------------------------------------------------------------


class TestBatchJob:
    def test_is_complete_empty(self):
        job = BatchJob(agent_ids=[])
        assert job.is_complete() is True

    def test_is_complete_pending(self):
        job = BatchJob(agent_ids=["a", "b"])
        assert job.is_complete() is False

    def test_is_complete_partial(self):
        job = BatchJob(agent_ids=["a", "b"])
        job.results["a"] = ReturnType("success", "done")
        assert job.is_complete() is False

    def test_is_complete_all_done(self):
        job = BatchJob(agent_ids=["a", "b"])
        job.results["a"] = ReturnType("success", "done")
        job.results["b"] = ReturnType("failure", "oops")
        assert job.is_complete() is True

    def test_get_completed_returns_dict(self):
        job = BatchJob(agent_ids=["a", "b"])
        job.results["a"] = ReturnType("success", "ok")
        completed = job.get_completed()
        assert "a" in completed
        assert completed["a"]["status"] == "success"
        assert completed["a"]["result"] == "ok"
        assert "b" not in completed

    def test_get_completed_empty(self):
        job = BatchJob(agent_ids=["x"])
        assert job.get_completed() == {}
