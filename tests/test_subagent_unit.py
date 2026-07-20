"""Unit tests for subagent module pure-logic functions.

Tests the refactored subagent package (hooks, types, batch) without
requiring API keys or running actual LLM calls.
"""

import importlib
import json
import queue
import threading
import time
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock

import pytest

import gptme.tools.subagent.api as subagent_api
import gptme.tools.subagent.execution as subagent_execution
import gptme.tools.subagent.types as subagent_types
from gptme.tools.complete import SessionCompleteException
from gptme.tools.subagent.api import (
    _write_cancel_op,
    subagent,
    subagent_cancel,
    subagent_steer,
)
from gptme.tools.subagent.batch import BatchJob
from gptme.tools.subagent.control import (
    CONTROL_FILENAME,
    append_control_op,
    drain_control_ops,
)
from gptme.tools.subagent.execution import _monitor_subprocess
from gptme.tools.subagent.hooks import (
    _get_complete_instruction,
    _subagent_cancel_checkpoint,
    _subagent_completion_hook,
    _subagent_control_hook,
    notify_completion,
    notify_progress,
)
from gptme.tools.subagent.types import (
    ReturnType,
    Subagent,
    _completion_queue,
    _progress_queue,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
    resolve_role_defaults,
)

# ---------------------------------------------------------------------------
# Subagent control channel tests
# ---------------------------------------------------------------------------


class TestSubagentControlChannel:
    def setup_method(self):
        with _subagent_results_lock:
            _subagent_results.clear()

    def test_control_operations_drain_in_fifo_order(self, tmp_path):
        append_control_op(tmp_path, "cancel", agent_id="first")
        append_control_op(tmp_path, "cancel", agent_id="second")

        operations = drain_control_ops(tmp_path)

        assert [(op["op"], op["agent_id"]) for op in operations] == [
            ("cancel", "first"),
            ("cancel", "second"),
        ]
        assert not (tmp_path / CONTROL_FILENAME).exists()

    def test_control_operations_skip_malformed_lines(self, tmp_path):
        control_path = tmp_path / CONTROL_FILENAME
        control_path.write_text('not json\n{"op": "cancel", "agent_id": "child"}\n')

        operations = drain_control_ops(tmp_path)

        assert operations[0]["agent_id"] == "child"

    def test_drain_control_ops_raises_on_read_error(self, tmp_path):
        control_path = tmp_path / CONTROL_FILENAME
        control_path.write_text('{"op": "cancel", "agent_id": "child"}\n')
        control_path.chmod(0o000)
        try:
            with pytest.raises(OSError, match="Permission denied"):
                drain_control_ops(tmp_path)
        finally:
            control_path.chmod(0o644)

    def test_control_hook_treats_read_error_as_cancel(self, tmp_path):
        control_path = tmp_path / CONTROL_FILENAME
        control_path.write_text('{"op": "cancel", "agent_id": "thread-agent"}\n')
        control_path.chmod(0o000)
        manager = MagicMock(logdir=tmp_path)
        try:
            with pytest.raises(SessionCompleteException, match="cancelled"):
                list(_subagent_control_hook(manager))
        finally:
            control_path.chmod(0o644)

    def test_control_hook_noops_without_control_file(self, tmp_path):
        manager = MagicMock(logdir=tmp_path)

        assert list(_subagent_control_hook(manager)) == []

    def test_control_hook_cancels_via_in_memory_event(self, tmp_path):
        """Hook must fire via cancel_event even when no control file exists.

        Regression for the Greptile P1: when append_control_op raises OSError the
        control file is never written, so without the in-memory fallback the thread
        keeps running indefinitely.

        Uses a mismatched agent_id/logdir.name to reproduce the real thread-mode
        shape (logdir = subagent-{agent_id}-{suffix}, agent_id = the bare id).
        """
        manager = MagicMock(logdir=tmp_path)
        # Deliberately differ from tmp_path.name to catch the old logdir.name lookup.
        sa = Subagent(
            agent_id="real-agent-id",
            prompt="test",
            thread=None,
            logdir=tmp_path,
            model=None,
        )
        sa.cancel_event.set()
        with _subagents_lock:
            _subagents.append(sa)
        with _subagent_results_lock:
            _subagent_results.clear()

        try:
            with pytest.raises(SessionCompleteException, match="cancelled"):
                list(_subagent_control_hook(manager))
        finally:
            with _subagents_lock:
                _subagents.remove(sa)

        # No control file should have been needed
        assert not (tmp_path / CONTROL_FILENAME).exists()

    def test_control_hook_stores_cancelled_status(self, tmp_path):
        """Hook must store 'cancelled' (not 'failure') when no prior result exists."""
        append_control_op(tmp_path, "cancel", agent_id="thread-agent")
        manager = MagicMock(logdir=tmp_path)
        with _subagent_results_lock:
            _subagent_results.clear()

        with pytest.raises(SessionCompleteException, match="cancelled"):
            list(_subagent_control_hook(manager))
        assert "cancellation requested" in manager.append.call_args.args[0].content
        with _subagent_results_lock:
            assert _subagent_results["thread-agent"].status == "cancelled"

    def test_control_hook_cancels_and_preserves_completed_result(self, tmp_path):
        """Hook must not overwrite a success result from natural completion."""
        manager = MagicMock(logdir=tmp_path)
        with _subagent_results_lock:
            _subagent_results["thread-agent"] = ReturnType("success", "done")
        append_control_op(tmp_path, "cancel", agent_id="thread-agent")

        with pytest.raises(SessionCompleteException, match="cancelled"):
            list(_subagent_control_hook(manager))
        with _subagent_results_lock:
            assert _subagent_results["thread-agent"] == ReturnType("success", "done")


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
# Progress notification tests
# ---------------------------------------------------------------------------


class TestProgressNotifications:
    def setup_method(self):
        """Drain the global queues before each test."""
        for q in (_completion_queue, _progress_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

    def test_notify_progress_adds_to_queue(self):
        notify_progress("worker-1", "Halfway done")
        assert not _progress_queue.empty()
        agent_id, message = _progress_queue.get_nowait()
        assert agent_id == "worker-1"
        assert message == "Halfway done"

    def test_hook_yields_progress_before_completion(self):
        """Progress messages are delivered before completion messages."""
        notify_progress("agent-p", "50% done")
        notify_completion("agent-p", "success", "all done")
        manager = MagicMock()
        messages = list(
            _subagent_completion_hook(manager, interactive=False, prompt_queue=None)
        )
        assert len(messages) == 2
        # Progress comes first
        assert "⏳" in messages[0].content
        assert "agent-p" in messages[0].content
        assert "50% done" in messages[0].content
        # Completion second
        assert "✅" in messages[1].content
        assert "agent-p" in messages[1].content

    def test_hook_yields_progress_message_format(self):
        notify_progress("my-agent", "Scanning files: 10/50 done")
        manager = MagicMock()
        messages = list(
            _subagent_completion_hook(manager, interactive=False, prompt_queue=None)
        )
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "⏳" in messages[0].content
        assert "my-agent" in messages[0].content
        assert "Scanning files: 10/50 done" in messages[0].content

    def test_hook_drains_multiple_progress_updates(self):
        notify_progress("agent-x", "Step 1 done")
        notify_progress("agent-x", "Step 2 done")
        notify_progress("agent-y", "Starting")
        manager = MagicMock()
        messages = list(
            _subagent_completion_hook(manager, interactive=False, prompt_queue=None)
        )
        assert len(messages) == 3
        contents = [m.content for m in messages]
        assert any("Step 1 done" in c for c in contents)
        assert any("Step 2 done" in c for c in contents)
        assert any("Starting" in c for c in contents)

    def test_progress_mention_in_complete_instruction(self):
        """_get_complete_instruction should mention the progress block."""
        instruction = _get_complete_instruction()
        assert "progress" in instruction
        assert "```progress" in instruction

    def test_progress_omitted_when_not_supported(self):
        """Subprocess-mode instructions should not advertise progress."""
        instruction = _get_complete_instruction(supports_progress=False)
        assert "progress" not in instruction
        assert "```progress" not in instruction


class TestProgressTool:
    """Tests for the progress tool execution path."""

    def setup_method(self):
        while not _progress_queue.empty():
            try:
                _progress_queue.get_nowait()
            except queue.Empty:
                break

    def test_progress_tool_with_agent_id(self):
        """When agent_id is set in thread-local, progress tool queues the update."""
        import gptme.tools.subagent.execution as exec_mod
        from gptme.tools.progress import execute_progress

        exec_mod._thread_local.agent_id = "thread-agent"
        try:
            messages = list(execute_progress("Phase 1 complete.", None, None))
        finally:
            del exec_mod._thread_local.agent_id

        assert not _progress_queue.empty()
        agent_id, message = _progress_queue.get_nowait()
        assert agent_id == "thread-agent"
        assert message == "Phase 1 complete."
        assert any("sent" in m.content for m in messages)

    def test_progress_tool_without_agent_id(self):
        """Without a thread-local agent_id (subprocess mode), tool warns but doesn't crash."""
        import gptme.tools.subagent.execution as exec_mod
        from gptme.tools.progress import execute_progress

        # Ensure no agent_id is set
        if hasattr(exec_mod._thread_local, "agent_id"):
            del exec_mod._thread_local.agent_id

        messages = list(execute_progress("Some update", None, None))

        assert _progress_queue.empty()  # Nothing queued
        assert len(messages) == 1
        assert "NOT delivered" in messages[0].content

    def test_progress_tool_empty_message(self):
        """Empty progress block yields a warning message."""
        from gptme.tools.progress import execute_progress

        messages = list(execute_progress("", None, None))
        assert len(messages) == 1
        assert "empty" in messages[0].content.lower()


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

    def test_read_log_bypasses_thread_liveness(self, tmp_path):
        """Regression: _read_log() must read from log even when thread is alive.

        run_subagent calls _read_log() (not status()) for exactly this reason:
        status() returns 'running' while the thread is alive, which would poison
        the _subagent_results cache with a wrong 'running' entry.
        """
        logdir = tmp_path / "subagent-log"
        logdir.mkdir()
        (logdir / "conversation.jsonl").write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "content": "```complete\ntask done\n```",
                    "timestamp": "2025-01-01T00:00:00+00:00",
                }
            )
            + "\n"
        )

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True  # thread still "alive"

        sa = Subagent(
            agent_id="read-log-test",
            prompt="do thing",
            thread=mock_thread,
            logdir=logdir,
            model=None,
        )

        # status() returns "running" while thread is alive — old bug path
        assert sa.status().status == "running"

        # _read_log() bypasses liveness and reads from log — fixed path
        result = sa._read_log()
        assert result.status == "success"
        assert "task done" in (result.result or "")


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

    def test_get_completed_auto_parses_output_schema(self):
        """get_completed() auto-parses results when output_schema is set.

        This is the regression test for the Greptile P1 finding:
        get_completed() on a BatchJob with output_schema should apply _parse_result()
        just like wait_all() does, so partial results have consistent parsing.
        """
        from gptme.tools.subagent.batch import BatchJob
        from gptme.tools.subagent.types import ReturnType

        class Schema:
            pass

        job = BatchJob(agent_ids=["a", "b"], output_schema=Schema)
        # Simulate a completed result with raw JSON string
        json_result = '{"x": 42}'
        job.results["a"] = ReturnType("success", json_result)

        completed = job.get_completed()
        assert "a" in completed
        assert completed["a"]["status"] == "success"
        # Auto-parsed: should be a dict, not the raw JSON string
        assert completed["a"]["result"] == {"x": 42}, (
            "get_completed() must auto-parse the result when output_schema is set; "
            f"got {completed['a']['result']!r} instead of {{'x': 42}}"
        )
        assert "b" not in completed  # Only completed agent appears

    def test_wait_any_returns_on_cancelled(self, monkeypatch):
        """wait_any() must treat 'cancelled' as a terminal status and return immediately."""
        import threading

        from gptme.tools.subagent import batch as batch_mod

        # Simulate: agent-a is cancelled, agent-b is still running
        call_counts: dict[str, int] = {}
        unblock = threading.Event()

        def mock_wait(agent_id, timeout=None, max_result_chars=0):
            call_counts[agent_id] = call_counts.get(agent_id, 0) + 1
            if agent_id == "agent-a":
                return {"status": "cancelled", "result": "Cancelled by orchestrator"}
            # agent-b blocks until the event — should never be called since agent-a
            # returns a terminal result first
            unblock.wait(timeout=timeout)
            return {"status": "success", "result": "done"}

        monkeypatch.setattr(batch_mod, "subagent_wait", mock_wait)

        job = BatchJob(agent_ids=["agent-a", "agent-b"])
        result_id, result_dict = job.wait_any(timeout=5)

        assert result_id == "agent-a"
        assert result_dict["status"] == "cancelled"
        unblock.set()  # release any background threads

    def test_wait_all_does_not_raise_on_as_completed_timeout(self, monkeypatch):
        """wait_all() must not propagate TimeoutError — stalled agents get a result dict."""
        import threading

        from gptme.tools.subagent import batch as batch_mod

        blocker = threading.Event()

        def stalling_wait(agent_id, timeout=None):
            blocker.wait(timeout=timeout)
            return {"status": "success", "result": "done"}

        monkeypatch.setattr(batch_mod, "subagent_wait", stalling_wait)

        job = BatchJob(agent_ids=["stall-1"])
        # Must not raise — short timeout forces as_completed to fire TimeoutError
        results = job.wait_all(timeout=1)

        assert "stall-1" in results
        assert results["stall-1"]["status"] in ("timeout", "failure", "success")
        blocker.set()  # unblock the background thread


# ---------------------------------------------------------------------------
# resolve_role_defaults tests
# ---------------------------------------------------------------------------


class TestResolveRoleDefaults:
    def test_none_role_returns_false_defaults(self):
        use_sub, use_iso, profile = resolve_role_defaults(None)
        assert use_sub is False
        assert use_iso is False
        assert profile is None

    def test_none_role_respects_explicit_subprocess(self):
        use_sub, use_iso, profile = resolve_role_defaults(
            None, explicit_use_subprocess=True
        )
        assert use_sub is True
        assert profile is None

    def test_none_role_respects_explicit_isolated(self):
        use_sub, use_iso, profile = resolve_role_defaults(None, explicit_isolated=True)
        assert use_iso is True

    def test_explore_role_sets_explorer_profile(self):
        use_sub, use_iso, profile = resolve_role_defaults("explore")
        assert profile == "explorer"
        assert use_sub is False
        assert use_iso is False

    def test_implement_role_sets_developer_profile(self):
        use_sub, use_iso, profile = resolve_role_defaults("implement")
        assert profile == "developer"
        assert use_sub is False
        assert use_iso is False

    def test_general_role_sets_default_profile(self):
        _, _, profile = resolve_role_defaults("general")
        assert profile == "default"

    def test_verify_role_enables_subprocess_and_isolated(self):
        use_sub, use_iso, profile = resolve_role_defaults("verify")
        assert use_sub is True
        assert use_iso is True
        assert profile == "verifier"

    def test_explicit_args_override_verify_role_defaults(self):
        use_sub, use_iso, profile = resolve_role_defaults(
            "verify",
            explicit_use_subprocess=False,
            explicit_isolated=False,
        )
        assert use_sub is False
        assert use_iso is False
        assert profile == "verifier"

    def test_explicit_subprocess_true_overrides_explore_default(self):
        use_sub, use_iso, _ = resolve_role_defaults(
            "explore", explicit_use_subprocess=True
        )
        assert use_sub is True

    def test_explicit_isolated_true_overrides_implement_default(self):
        _, use_iso, _ = resolve_role_defaults("implement", explicit_isolated=True)
        assert use_iso is True


# ---------------------------------------------------------------------------
# subagent_cancel tests
# ---------------------------------------------------------------------------


class TestSubagentCancel:
    _logdir = Path("/tmp/test-log")

    def setup_method(self):
        """Clear global subagent registry before each test."""
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()
        while not _completion_queue.empty():
            try:
                _completion_queue.get_nowait()
            except queue.Empty:
                break

    def _register(self, agent_id: str, **kwargs) -> Subagent:
        sa = Subagent(
            agent_id=agent_id,
            prompt="test",
            thread=kwargs.get("thread"),
            logdir=kwargs.get("logdir", self._logdir),
            model=None,
            process=kwargs.get("process"),
            execution_mode=kwargs.get("execution_mode", "thread"),
        )
        with _subagents_lock:
            _subagents.append(sa)
        return sa

    def test_cancel_unknown_raises(self):
        with pytest.raises(ValueError, match="not found"):
            subagent_cancel("nonexistent")

    def test_cancel_finished_subagent(self):
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        self._register("done-agent", thread=mock_thread)
        result = subagent_cancel("done-agent")
        assert "not running" in result

    def test_cancel_subprocess_terminates_process(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        mock_proc.wait.return_value = 0
        self._register(
            "proc-agent",
            process=mock_proc,
            execution_mode="subprocess",
            logdir=tmp_path,
        )
        result = subagent_cancel("proc-agent")
        mock_proc.terminate.assert_called_once()
        assert "cancelled" in result.lower()
        with _subagent_results_lock:
            assert _subagent_results["proc-agent"].status == "cancelled"
            assert "Cancelled" in (_subagent_results["proc-agent"].result or "")
        assert drain_control_ops(tmp_path)[0]["agent_id"] == "proc-agent"

    def test_cancel_subprocess_kills_on_timeout(self):
        import subprocess as _subprocess

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = [
            _subprocess.TimeoutExpired(cmd="gptme", timeout=5),
            0,
        ]
        self._register("slow-proc", process=mock_proc, execution_mode="subprocess")
        subagent_cancel("slow-proc")
        mock_proc.kill.assert_called_once()

    def test_cancel_subprocess_preserves_completed_result(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        self._register("proc-agent", process=mock_proc, execution_mode="subprocess")
        with _subagent_results_lock:
            _subagent_results["proc-agent"] = ReturnType("success", "done")

        result = subagent_cancel("proc-agent")

        assert "already finished" in result.lower()
        mock_proc.terminate.assert_not_called()
        with _subagent_results_lock:
            assert _subagent_results["proc-agent"] == ReturnType("success", "done")

    def test_subprocess_monitor_preserves_cancelled_result(self):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.returncode = -15
        sa = self._register(
            "proc-agent",
            process=mock_proc,
            execution_mode="subprocess",
        )
        with _subagent_results_lock:
            _subagent_results["proc-agent"] = ReturnType(
                "cancelled", "Cancelled by orchestrator"
            )

        _monitor_subprocess(sa)

        with _subagent_results_lock:
            assert _subagent_results["proc-agent"].status == "cancelled"
            assert _subagent_results["proc-agent"].result == "Cancelled by orchestrator"
        assert _completion_queue.empty()

    def test_subprocess_monitor_preserves_clarification_status(self, tmp_path):
        logdir = tmp_path / "proc-log"
        logdir.mkdir()
        (logdir / "conversation.jsonl").write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "content": "```clarify\nWhich format should I use?\n```",
                    "timestamp": "2025-01-01T00:00:00+00:00",
                }
            )
            + "\n"
        )
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0
        sa = self._register(
            "proc-clarify",
            process=mock_proc,
            execution_mode="subprocess",
            logdir=logdir,
        )

        _monitor_subprocess(sa)

        with _subagent_results_lock:
            result = _subagent_results["proc-clarify"]
        assert result.status == "clarification_needed"
        assert result.result == "Which format should I use?"
        agent_id, status, summary = _completion_queue.get_nowait()
        assert agent_id == "proc-clarify"
        assert status == "clarification_needed"
        assert "Which format" in summary

    def test_subprocess_monitor_preserves_token_counts(self, tmp_path):
        logdir = tmp_path / "proc-token-log"
        logdir.mkdir()
        (logdir / "conversation.jsonl").write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "content": "```complete\nDone.\n```",
                    "timestamp": "2025-01-01T00:00:00+00:00",
                    "metadata": {"usage": {"input_tokens": 123, "output_tokens": 45}},
                }
            )
            + "\n"
        )
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0
        sa = self._register(
            "proc-token",
            process=mock_proc,
            execution_mode="subprocess",
            logdir=logdir,
        )

        _monitor_subprocess(sa)

        with _subagent_results_lock:
            result = _subagent_results["proc-token"]
        assert result.status == "success"
        assert result.input_tokens == 123
        assert result.output_tokens == 45

    def test_cancel_thread_marks_result(self):
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        self._register("thread-agent", thread=mock_thread)
        result = subagent_cancel("thread-agent")
        assert "cancelled" in result.lower()
        with _subagent_results_lock:
            assert _subagent_results["thread-agent"].status == "cancelled"

    def test_cancel_thread_writes_control_operation(self, tmp_path):
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        self._register("thread-agent", thread=mock_thread, logdir=tmp_path)

        subagent_cancel("thread-agent")

        assert drain_control_ops(tmp_path)[0]["agent_id"] == "thread-agent"

    def test_cancel_thread_result_set_before_control_file(self, tmp_path):
        """Result must be 'cancelled' in cache before control file is written.

        Regression test for the race where the STEP_PRE hook could observe the
        cancel op before set_subagent_result_if_absent and store 'failure'.
        """
        control_written_after_result: list[bool] = []

        original_append = __import__(
            "gptme.tools.subagent.control", fromlist=["append_control_op"]
        ).append_control_op

        def spy_append(logdir, op, **kwargs):
            # At the moment the control file is written, check if result is set
            with _subagent_results_lock:
                already_set = "thread-agent" in _subagent_results
            control_written_after_result.append(already_set)
            original_append(logdir, op, **kwargs)

        import gptme.tools.subagent.api as _sapi

        original = _sapi.append_control_op
        _sapi.append_control_op = spy_append
        try:
            mock_thread = MagicMock(spec=threading.Thread)
            mock_thread.is_alive.return_value = True
            self._register("thread-agent", thread=mock_thread, logdir=tmp_path)
            subagent_cancel("thread-agent")
        finally:
            _sapi.append_control_op = original

        # The result must already be in the cache when the control file is written
        assert control_written_after_result == [True], (
            "Control file was written before result was set — race condition present"
        )
        with _subagent_results_lock:
            assert _subagent_results["thread-agent"].status == "cancelled"

    def test_cancel_thread_sets_cancel_event_on_ioerror(self, tmp_path):
        """When control-file write fails, cancel_event must be set as fallback signal."""
        import gptme.tools.subagent.api as _sapi

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        sa = self._register("thread-agent", thread=mock_thread, logdir=tmp_path)

        original = _sapi.append_control_op

        def failing_append(logdir, op, **kwargs):
            raise OSError("disk full")

        _sapi.append_control_op = failing_append
        try:
            result = subagent_cancel("thread-agent")
        finally:
            _sapi.append_control_op = original

        assert "cancelled" in result.lower()
        assert sa.cancel_event.is_set(), (
            "cancel_event must be set when control-file write fails"
        )
        with _subagent_results_lock:
            assert _subagent_results["thread-agent"].status == "cancelled"

    def test_thread_completion_skips_notify_when_cancel_wins_race(
        self, monkeypatch, tmp_path
    ):
        cli_main = importlib.import_module("gptme.cli.main")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")
        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)
        monkeypatch.setattr(
            subagent_api._exec, "_create_subagent_thread", lambda **kwargs: None
        )
        monkeypatch.setattr(subagent_api._exec, "_cleanup_isolation", lambda sa: None)

        notify_calls: list[tuple[str, str, str]] = []

        def fake_notify_completion(agent_id: str, status: str, summary: str) -> None:
            notify_calls.append((agent_id, status, summary))

        def fake_set_subagent_result_if_absent(
            agent_id: str, result: ReturnType
        ) -> bool:
            with _subagent_results_lock:
                _subagent_results[agent_id] = ReturnType(
                    "cancelled", "Cancelled by orchestrator"
                )
            return False

        monkeypatch.setattr(subagent_api, "notify_completion", fake_notify_completion)
        monkeypatch.setattr(
            subagent_api,
            "set_subagent_result_if_absent",
            fake_set_subagent_result_if_absent,
        )

        subagent("thread-agent", "do the thing")

        with _subagents_lock:
            sa = next(s for s in _subagents if s.agent_id == "thread-agent")
        assert sa.thread is not None
        sa.thread.join(timeout=1)
        assert not sa.thread.is_alive()
        assert notify_calls == []
        with _subagent_results_lock:
            assert _subagent_results["thread-agent"] == ReturnType(
                "cancelled", "Cancelled by orchestrator"
            )

    def test_thread_exception_cleans_isolation_when_cancel_wins_race(
        self, monkeypatch, tmp_path
    ):
        cli_main = importlib.import_module("gptme.cli.main")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")
        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)

        def boom(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(subagent_api._exec, "_create_subagent_thread", boom)

        cleanup_calls: list[str] = []

        def fake_cleanup(sa: Subagent) -> None:
            cleanup_calls.append(sa.agent_id)

        def fake_set_subagent_result_if_absent(
            agent_id: str, result: ReturnType
        ) -> bool:
            with _subagent_results_lock:
                _subagent_results[agent_id] = ReturnType(
                    "cancelled", "Cancelled by orchestrator"
                )
            return False

        monkeypatch.setattr(subagent_api._exec, "_cleanup_isolation", fake_cleanup)
        monkeypatch.setattr(
            subagent_api,
            "set_subagent_result_if_absent",
            fake_set_subagent_result_if_absent,
        )

        subagent("thread-agent", "do the thing", isolated=True)

        with _subagents_lock:
            sa = next(s for s in _subagents if s.agent_id == "thread-agent")
        assert sa.thread is not None
        sa.thread.join(timeout=1)
        assert not sa.thread.is_alive()
        assert cleanup_calls == ["thread-agent"]
        with _subagent_results_lock:
            assert _subagent_results["thread-agent"] == ReturnType(
                "cancelled", "Cancelled by orchestrator"
            )

    def test_subprocess_launch_failure_cleans_isolation_when_cancel_wins_race(
        self, monkeypatch, tmp_path
    ):
        cli_main = importlib.import_module("gptme.cli.main")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")
        git_worktree = importlib.import_module("gptme.util.git_worktree")
        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)
        monkeypatch.setattr(git_worktree, "get_git_root", lambda _: None)

        def boom(**kwargs):
            raise OSError("boom")

        monkeypatch.setattr(subagent_api._exec, "_run_subagent_subprocess", boom)

        cleanup_calls: list[str] = []
        notify_calls: list[tuple[str, str, str]] = []

        def fake_cleanup(sa: Subagent) -> None:
            cleanup_calls.append(sa.agent_id)

        def fake_notify_completion(agent_id: str, status: str, summary: str) -> None:
            notify_calls.append((agent_id, status, summary))

        def fake_set_subagent_result_if_absent(
            agent_id: str, result: ReturnType
        ) -> bool:
            with _subagent_results_lock:
                _subagent_results[agent_id] = ReturnType(
                    "cancelled", "Cancelled by orchestrator"
                )
            return False

        monkeypatch.setattr(subagent_api._exec, "_cleanup_isolation", fake_cleanup)
        monkeypatch.setattr(subagent_api, "notify_completion", fake_notify_completion)
        monkeypatch.setattr(
            subagent_api,
            "set_subagent_result_if_absent",
            fake_set_subagent_result_if_absent,
        )

        subagent("proc-agent", "do the thing", use_subprocess=True, isolated=True)

        with _subagents_lock:
            sa = next(s for s in _subagents if s.agent_id == "proc-agent")
        assert sa.thread is not None
        sa.thread.join(timeout=1)
        assert not sa.thread.is_alive()
        assert cleanup_calls == ["proc-agent"]
        assert notify_calls == []
        with _subagent_results_lock:
            assert _subagent_results["proc-agent"] == ReturnType(
                "cancelled", "Cancelled by orchestrator"
            )

    def test_cancelled_queued_subprocess_does_not_launch_after_slot_frees(
        self, monkeypatch, tmp_path
    ):
        cli_main = importlib.import_module("gptme.cli.main")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")
        git_worktree = importlib.import_module("gptme.util.git_worktree")
        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)
        monkeypatch.setattr(git_worktree, "get_git_root", lambda _: None)

        sem = threading.BoundedSemaphore(1)
        assert sem.acquire(timeout=0)
        monkeypatch.setattr(subagent_api, "get_slot_sem", lambda: sem)

        launch_mock = MagicMock(return_value=MagicMock())
        cleanup_calls: list[str] = []

        monkeypatch.setattr(subagent_api._exec, "_run_subagent_subprocess", launch_mock)
        monkeypatch.setattr(subagent_api._exec, "_monitor_subprocess", lambda sa: None)
        monkeypatch.setattr(
            subagent_api._exec,
            "_cleanup_isolation",
            lambda sa: cleanup_calls.append(sa.agent_id),
        )

        subagent("proc-agent", "do the thing", use_subprocess=True, isolated=True)

        with _subagents_lock:
            sa = next(s for s in _subagents if s.agent_id == "proc-agent")
        assert sa.thread is not None

        result = subagent_cancel("proc-agent")
        assert "marked as cancelled" in result.lower()

        sem.release()
        sa.thread.join(timeout=1)
        assert not sa.thread.is_alive()

        launch_mock.assert_not_called()
        assert cleanup_calls == ["proc-agent"]
        with _subagent_results_lock:
            assert _subagent_results["proc-agent"] == ReturnType(
                "cancelled", "Cancelled by orchestrator"
            )

    def test_cancelled_queued_thread_does_not_launch_after_slot_frees(
        self, monkeypatch, tmp_path
    ):
        cli_main = importlib.import_module("gptme.cli.main")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")
        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)

        sem = threading.BoundedSemaphore(1)
        assert sem.acquire(timeout=0)
        monkeypatch.setattr(subagent_api, "get_slot_sem", lambda: sem)

        launch_mock = MagicMock()
        cleanup_calls: list[str] = []

        monkeypatch.setattr(subagent_api._exec, "_create_subagent_thread", launch_mock)
        monkeypatch.setattr(
            subagent_api._exec,
            "_cleanup_isolation",
            lambda sa: cleanup_calls.append(sa.agent_id),
        )

        subagent("thread-agent", "do the thing", isolated=True)

        with _subagents_lock:
            sa = next(s for s in _subagents if s.agent_id == "thread-agent")
        assert sa.thread is not None

        result = subagent_cancel("thread-agent")
        assert "marked as cancelled" in result.lower()

        sem.release()
        sa.thread.join(timeout=1)
        assert not sa.thread.is_alive()

        launch_mock.assert_not_called()
        assert cleanup_calls == ["thread-agent"]
        with _subagent_results_lock:
            assert _subagent_results["thread-agent"] == ReturnType(
                "cancelled", "Cancelled by orchestrator"
            )

    def test_cancelled_queued_planner_subprocess_does_not_launch_after_slot_frees(
        self, monkeypatch, tmp_path
    ):
        cli_main = importlib.import_module("gptme.cli.main")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")
        git_worktree = importlib.import_module("gptme.util.git_worktree")
        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)
        monkeypatch.setattr(git_worktree, "get_git_root", lambda _: None)

        sem = threading.BoundedSemaphore(1)
        assert sem.acquire(timeout=0)
        monkeypatch.setattr(subagent_execution, "get_slot_sem", lambda: sem)

        launch_mock = MagicMock(return_value=MagicMock())
        cleanup_calls: list[str] = []

        monkeypatch.setattr(subagent_execution, "_run_subagent_subprocess", launch_mock)
        monkeypatch.setattr(subagent_execution, "_monitor_subprocess", lambda sa: None)
        monkeypatch.setattr(
            subagent_execution,
            "_cleanup_isolation",
            lambda sa: cleanup_calls.append(sa.agent_id),
        )

        subagent(
            "planner-agent",
            "context",
            mode="planner",
            subtasks=[{"id": "verify", "description": "Verify it", "role": "verify"}],
        )

        with _subagents_lock:
            sa = next(s for s in _subagents if s.agent_id == "planner-agent-verify")
        assert sa.thread is not None

        result = subagent_cancel("planner-agent-verify")
        assert "marked as cancelled" in result.lower()

        sem.release()
        sa.thread.join(timeout=1)
        assert not sa.thread.is_alive()

        launch_mock.assert_not_called()
        assert cleanup_calls == ["planner-agent-verify"]
        with _subagent_results_lock:
            assert _subagent_results["planner-agent-verify"] == ReturnType(
                "cancelled", "Cancelled by orchestrator"
            )

    def test_cancelled_queued_planner_thread_does_not_launch_after_slot_frees(
        self, monkeypatch, tmp_path
    ):
        cli_main = importlib.import_module("gptme.cli.main")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")
        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)

        sem = threading.BoundedSemaphore(1)
        assert sem.acquire(timeout=0)
        monkeypatch.setattr(subagent_execution, "get_slot_sem", lambda: sem)

        launch_mock = MagicMock()
        cleanup_calls: list[str] = []

        monkeypatch.setattr(subagent_execution, "_create_subagent_thread", launch_mock)
        monkeypatch.setattr(
            subagent_execution,
            "_cleanup_isolation",
            lambda sa: cleanup_calls.append(sa.agent_id),
        )

        subagent(
            "planner-agent",
            "context",
            mode="planner",
            subtasks=[
                {"id": "implement", "description": "Implement it", "role": "implement"}
            ],
        )

        with _subagents_lock:
            sa = next(s for s in _subagents if s.agent_id == "planner-agent-implement")
        assert sa.thread is not None

        result = subagent_cancel("planner-agent-implement")
        assert "marked as cancelled" in result.lower()

        sem.release()
        sa.thread.join(timeout=1)
        assert not sa.thread.is_alive()

        launch_mock.assert_not_called()
        assert cleanup_calls == ["planner-agent-implement"]
        with _subagent_results_lock:
            assert _subagent_results["planner-agent-implement"] == ReturnType(
                "cancelled", "Cancelled by orchestrator"
            )

    def test_planner_thread_cleanup_failure_still_releases_semaphore(
        self, monkeypatch, tmp_path
    ):
        cli_main = importlib.import_module("gptme.cli.main")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")
        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)

        sem = threading.BoundedSemaphore(1)
        monkeypatch.setattr(subagent_execution, "get_slot_sem", lambda: sem)
        monkeypatch.setattr(
            subagent_execution, "_create_subagent_thread", lambda **kwargs: None
        )

        cleanup_calls: list[str] = []

        def fail_cleanup(sa):
            cleanup_calls.append(sa.agent_id)
            raise RuntimeError("cleanup boom")

        monkeypatch.setattr(subagent_execution, "_cleanup_isolation", fail_cleanup)

        subagent(
            "planner-agent",
            "context",
            mode="planner",
            subtasks=[
                {"id": "implement", "description": "Implement it", "role": "implement"}
            ],
        )

        with _subagents_lock:
            sa = next(s for s in _subagents if s.agent_id == "planner-agent-implement")
        assert sa.thread is not None

        sa.thread.join(timeout=1)
        assert not sa.thread.is_alive()
        assert cleanup_calls == ["planner-agent-implement"]
        assert sem.acquire(timeout=0.1)
        sem.release()


# ---------------------------------------------------------------------------
# Clarification mechanism tests
# ---------------------------------------------------------------------------


class TestClarifyBlock:
    """Tests for the subagent clarification mechanism.

    Subagents can use a ``clarify`` code block (analogous to ``complete``) to
    signal that they need more information.  _read_log() detects the block and
    returns status="clarification_needed"; the hook delivers a ❓ notification;
    subagent_reply() re-spawns with the original prompt + Q&A appended.
    """

    def setup_method(self):
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()

    def _make_subagent(self, tmp_path: Path, content: str) -> Subagent:
        logdir = tmp_path / "subagent-log"
        logdir.mkdir()
        (logdir / "conversation.jsonl").write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "content": content,
                    "timestamp": "2025-01-01T00:00:00+00:00",
                }
            )
            + "\n"
        )
        return Subagent(
            agent_id="clarify-test",
            prompt="original task",
            thread=None,
            logdir=logdir,
            model=None,
        )

    def test_read_log_detects_clarify_block(self, tmp_path):
        sa = self._make_subagent(
            tmp_path, "```clarify\nWhich output format? JSON or CSV?\n```"
        )
        result = sa._read_log()
        assert result.status == "clarification_needed"
        assert "Which output format? JSON or CSV?" in (result.result or "")

    def test_read_log_clarify_takes_priority_over_failure(self, tmp_path):
        # A clarify block should be detected even if the session didn't also complete
        sa = self._make_subagent(
            tmp_path,
            "I'm not sure how to proceed.\n```clarify\nWhat is the target directory?\n```",
        )
        result = sa._read_log()
        assert result.status == "clarification_needed"
        assert "target directory" in (result.result or "")

    def test_read_log_empty_clarify_block_handled(self, tmp_path):
        sa = self._make_subagent(tmp_path, "```clarify\n\n```")
        result = sa._read_log()
        assert result.status == "clarification_needed"
        assert result.result is not None

    def test_complete_block_still_returns_success(self, tmp_path):
        # Clarify detection must not interfere with normal complete blocks
        sa = self._make_subagent(tmp_path, "```complete\ntask done\n```")
        result = sa._read_log()
        assert result.status == "success"
        assert "task done" in (result.result or "")

    def test_hook_yields_clarification_message(self):
        """The completion hook delivers a ❓ notification for clarification_needed."""
        # Drain queue first
        while not _completion_queue.empty():
            try:
                _completion_queue.get_nowait()
            except queue.Empty:
                break

        notify_completion("agent-q", "clarification_needed", "What format?")
        manager = MagicMock()
        messages = list(
            _subagent_completion_hook(manager, interactive=False, prompt_queue=None)
        )
        assert len(messages) == 1
        msg = messages[0]
        assert msg.role == "system"
        assert "❓" in msg.content
        assert "agent-q" in msg.content
        assert "What format?" in msg.content
        assert "subagent_reply" in msg.content

    def test_complete_instruction_mentions_clarify_block(self):
        """_get_complete_instruction() must tell subagents about the clarify option."""
        instruction = _get_complete_instruction()
        assert "```clarify" in instruction

    def test_subagent_reply_rejects_missing_agent(self):
        from gptme.tools.subagent.api import subagent_reply

        with pytest.raises(ValueError, match="not found"):
            subagent_reply("nonexistent-agent", "answer")

    def test_subagent_reply_rejects_running_agent(self, tmp_path, monkeypatch):
        from gptme.tools.subagent.api import subagent_reply

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        sa = Subagent(
            agent_id="running-agent",
            prompt="do stuff",
            thread=mock_thread,
            logdir=tmp_path / "log",
            model=None,
        )
        with _subagents_lock:
            _subagents.append(sa)
        try:
            with pytest.raises(ValueError, match="still running"):
                subagent_reply("running-agent", "answer")
        finally:
            with _subagents_lock:
                _subagents.remove(sa)

    def test_subagent_reply_rejects_non_clarification_status(self, tmp_path):
        """subagent_reply() must reject agents that did not ask for clarification."""
        from gptme.tools.subagent.api import subagent_reply

        logdir = tmp_path / "log-done"
        logdir.mkdir()
        (logdir / "conversation.jsonl").write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "content": "```complete\ndone\n```",
                    "timestamp": "2025-01-01T00:00:00+00:00",
                }
            )
            + "\n"
        )
        sa = Subagent(
            agent_id="done-agent",
            prompt="task",
            thread=None,
            logdir=logdir,
            model=None,
        )
        with _subagents_lock:
            _subagents.append(sa)
        try:
            with pytest.raises(ValueError, match="clarification_needed"):
                subagent_reply("done-agent", "answer")
        finally:
            with _subagents_lock:
                _subagents.remove(sa)

    def test_subagent_reply_replaces_registry_entry_and_preserves_spawn_params(
        self, tmp_path, monkeypatch
    ):
        from gptme.tools.subagent.api import subagent_reply

        class DummySchema:
            pass

        sa = Subagent(
            agent_id="clarify-agent",
            prompt="original task",
            thread=None,
            logdir=tmp_path / "old-log",
            model="openai/gpt-4o-mini",
            context_mode="selective",
            context_include=["workspace", "tools"],
            profile="custom-reviewer",
            output_schema=DummySchema,
            use_acp=True,
            execution_mode="acp",
            acp_command="claude-code-acp",
            isolated=True,
            timeout=42,
            role="verify",
        )
        with _subagents_lock:
            _subagents.append(sa)
        with _subagent_results_lock:
            _subagent_results["clarify-agent"] = ReturnType(
                "clarification_needed", "Which format should I use?"
            )

        captured: dict = {}

        def fake_subagent(**kwargs):
            captured.update(kwargs)
            with _subagents_lock:
                assert not any(s.agent_id == "clarify-agent" for s in _subagents)
                _subagents.append(
                    Subagent(
                        agent_id=kwargs["agent_id"],
                        prompt=kwargs["prompt"],
                        thread=None,
                        logdir=tmp_path / "new-log",
                        model=kwargs["model"],
                        context_mode=kwargs["context_mode"],
                        context_include=kwargs["context_include"],
                        profile=kwargs["profile"],
                        output_schema=kwargs["output_schema"],
                        use_acp=kwargs["use_acp"],
                        execution_mode="acp" if kwargs["use_acp"] else "thread",
                        acp_command=kwargs["acp_command"],
                        isolated=kwargs["isolated"],
                        timeout=kwargs["timeout"],
                        role=kwargs["role"],
                        context_turns=kwargs.get("context_turns"),
                    )
                )

        monkeypatch.setattr(subagent_api, "subagent", fake_subagent)

        subagent_reply("clarify-agent", "Use JSON.")

        assert captured == {
            "agent_id": "clarify-agent",
            "prompt": "original task\n\n[Clarification from previous attempt]\nQ: Which format should I use?\nA: Use JSON.",
            "model": "openai/gpt-4o-mini",
            "context_mode": "selective",
            "context_include": ["workspace", "tools"],
            "output_schema": DummySchema,
            "use_subprocess": False,
            "use_acp": True,
            "acp_command": "claude-code-acp",
            "profile": "custom-reviewer",
            "workdir": None,
            "isolated": True,
            "timeout": 42,
            "role": "verify",
            "redact_secrets": True,
            "context_window": None,
            "max_time": None,
            "context_turns": None,
        }
        with _subagent_results_lock:
            assert "clarify-agent" not in _subagent_results
        with _subagents_lock:
            matching = [s for s in _subagents if s.agent_id == "clarify-agent"]
        assert len(matching) == 1
        assert matching[0].prompt == captured["prompt"]
        assert matching[0].role == "verify"
        assert matching[0].context_include == ["workspace", "tools"]
        assert matching[0].profile == "custom-reviewer"
        assert matching[0].execution_mode == "acp"

    def test_subagent_reply_rejects_excessive_clarifications(self, tmp_path):
        """subagent_reply() must reject after too many clarification rounds."""
        from gptme.tools.subagent.api import subagent_reply

        # Construct a prompt that already has 5 clarification rounds in it
        prompt_with_many_rounds = "original task\n\n" + "\n\n".join(
            f"[Clarification from previous attempt]\nQ: Q{i}\nA: A{i}" for i in range(5)
        )
        sa = Subagent(
            agent_id="loop-agent",
            prompt=prompt_with_many_rounds,
            thread=None,
            logdir=tmp_path / "loop-log",
            model=None,
        )
        with _subagents_lock:
            _subagents.append(sa)
        with _subagent_results_lock:
            _subagent_results["loop-agent"] = ReturnType(
                "clarification_needed", "Another question?"
            )
        try:
            with pytest.raises(ValueError, match="limit"):
                subagent_reply("loop-agent", "answer")
        finally:
            with _subagents_lock:
                _subagents[:] = [s for s in _subagents if s.agent_id != "loop-agent"]
            with _subagent_results_lock:
                _subagent_results.pop("loop-agent", None)

    def test_subagent_reply_restores_state_on_spawn_failure(
        self, tmp_path, monkeypatch
    ):
        """If subagent() raises during re-spawn, the original state is restored."""
        from gptme.tools.subagent.api import subagent_reply

        sa = Subagent(
            agent_id="atomic-agent",
            prompt="original task",
            thread=None,
            logdir=tmp_path / "log",
            model=None,
        )
        original_result = ReturnType("clarification_needed", "What format?")
        with _subagents_lock:
            _subagents.append(sa)
        with _subagent_results_lock:
            _subagent_results["atomic-agent"] = original_result

        def failing_subagent(**kwargs):
            raise RuntimeError("spawn failed")

        monkeypatch.setattr(subagent_api, "subagent", failing_subagent)

        with pytest.raises(RuntimeError, match="spawn failed"):
            subagent_reply("atomic-agent", "JSON")

        # Both the registry entry and the result must be restored
        with _subagents_lock:
            matching = [s for s in _subagents if s.agent_id == "atomic-agent"]
        assert len(matching) == 1, (
            "Subagent entry should be restored after spawn failure"
        )
        with _subagent_results_lock:
            assert _subagent_results.get("atomic-agent") == original_result
        # cleanup
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "atomic-agent"]
        with _subagent_results_lock:
            _subagent_results.pop("atomic-agent", None)

    def test_subagent_reply_preserves_workdir(self, tmp_path, monkeypatch):
        """subagent_reply() must forward the original workdir to the re-spawned subagent."""

        from gptme.tools.subagent.api import subagent_reply

        workspace_dir = tmp_path / "project"
        workspace_dir.mkdir()

        sa = Subagent(
            agent_id="workdir-agent",
            prompt="original task",
            thread=None,
            logdir=tmp_path / "log",
            model=None,
            workdir=workspace_dir,
        )
        with _subagents_lock:
            _subagents.append(sa)
        with _subagent_results_lock:
            _subagent_results["workdir-agent"] = ReturnType(
                "clarification_needed", "Which file should I edit?"
            )

        captured: dict = {}

        def fake_subagent(**kwargs):
            captured.update(kwargs)
            with _subagents_lock:
                _subagents.append(
                    Subagent(
                        agent_id=kwargs["agent_id"],
                        prompt=kwargs["prompt"],
                        thread=None,
                        logdir=tmp_path / "new-log",
                        model=None,
                        workdir=kwargs.get("workdir"),
                    )
                )

        monkeypatch.setattr(subagent_api, "subagent", fake_subagent)

        subagent_reply("workdir-agent", "Edit config.py")

        assert captured.get("workdir") == workspace_dir, (
            "subagent_reply must forward the original workdir to the re-spawned subagent; "
            f"expected {workspace_dir!r}, got {captured.get('workdir')!r}"
        )
        # cleanup
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "workdir-agent"]
        with _subagent_results_lock:
            _subagent_results.pop("workdir-agent", None)


# ---------------------------------------------------------------------------
# Secret redaction tests (gptme/tools/subagent/context.py)
# ---------------------------------------------------------------------------


class TestRedactSecretsFromText:
    """Tests for the redact_secrets_from_text utility."""

    def test_redacts_api_key_equals(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        result = redact_secrets_from_text("OPENAI_API_KEY=sk-proj-abc123\n")
        assert "sk-proj-abc123" not in result
        assert "[REDACTED]" in result
        assert "OPENAI_API_KEY" in result

    def test_redacts_github_token(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        result = redact_secrets_from_text("GITHUB_TOKEN=ghp_xyzXYZ987654\n")
        assert "ghp_xyzXYZ987654" not in result
        assert "[REDACTED]" in result

    def test_redacts_password(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        result = redact_secrets_from_text("PASSWORD=hunter2\n")
        assert "hunter2" not in result
        assert "[REDACTED]" in result

    def test_redacts_export_statement(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        result = redact_secrets_from_text("export API_KEY=my-secret-key\n")
        assert "my-secret-key" not in result
        assert "[REDACTED]" in result
        assert "export" in result
        assert "API_KEY" in result

    def test_preserves_non_secret_lines(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        content = "PROJECT_NAME=myproject\nDEBUG=true\nLOG_LEVEL=info\n"
        result = redact_secrets_from_text(content)
        assert result == content

    def test_redacts_only_secret_lines_in_multiline(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        content = "HOST=localhost\nAPI_KEY=supersecret\nPORT=8080\n"
        result = redact_secrets_from_text(content)
        assert "supersecret" not in result
        assert "HOST=localhost" in result
        assert "PORT=8080" in result
        assert "API_KEY" in result

    def test_redacts_access_key(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        result = redact_secrets_from_text("ACCESS_KEY=AKIAIOSFODNN7EXAMPLE\n")
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED]" in result

    def test_redacts_private_key(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        result = redact_secrets_from_text("PRIVATE_KEY=abc123privatekey\n")
        assert "abc123privatekey" not in result
        assert "[REDACTED]" in result

    def test_handles_empty_string(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        assert redact_secrets_from_text("") == ""

    def test_handles_no_secrets(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        content = "# This is just a comment\nSome text here\n"
        assert redact_secrets_from_text(content) == content


class TestRedactSecretsFromMessages:
    """Tests for the redact_secrets_from_messages utility."""

    def test_redacts_system_message_content(self):
        from gptme.message import Message
        from gptme.tools.subagent.context import redact_secrets_from_messages

        msgs = [Message("system", "Config:\nAPI_KEY=supersecret\nHOST=localhost\n")]
        result = redact_secrets_from_messages(msgs)
        assert len(result) == 1
        assert "supersecret" not in result[0].content
        assert "[REDACTED]" in result[0].content
        assert "HOST=localhost" in result[0].content

    def test_preserves_message_role(self):
        from gptme.message import Message
        from gptme.tools.subagent.context import redact_secrets_from_messages

        msgs = [
            Message("system", "API_KEY=secret\n"),
            Message("user", "do a thing"),
        ]
        result = redact_secrets_from_messages(msgs)
        assert result[0].role == "system"
        assert result[1].role == "user"

    def test_returns_new_message_objects(self):
        from gptme.message import Message
        from gptme.tools.subagent.context import redact_secrets_from_messages

        original = Message("system", "API_KEY=secret\n")
        result = redact_secrets_from_messages([original])
        assert result[0] is not original
        # Original is unchanged
        assert "secret" in original.content

    def test_handles_empty_list(self):
        from gptme.tools.subagent.context import redact_secrets_from_messages

        assert redact_secrets_from_messages([]) == []

    def test_handles_messages_with_no_secrets(self):
        from gptme.message import Message
        from gptme.tools.subagent.context import redact_secrets_from_messages

        msgs = [Message("system", "# Agent instructions\nDo good work.\n")]
        result = redact_secrets_from_messages(msgs)
        assert result[0].content == msgs[0].content


class TestRedactSecretsColonStyle:
    """Tests for YAML/TOML colon-style secret redaction."""

    def test_redacts_yaml_api_key(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        result = redact_secrets_from_text("openai_api_key: sk-proj-abc\n")
        assert "sk-proj-abc" not in result
        assert "[REDACTED]" in result
        assert "openai_api_key" in result

    def test_redacts_yaml_github_token(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        result = redact_secrets_from_text("github_token: ghp_xyzXYZ987\n")
        assert "ghp_xyzXYZ987" not in result
        assert "[REDACTED]" in result
        assert "github_token" in result

    def test_redacts_yaml_password(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        result = redact_secrets_from_text("password: hunter2\n")
        assert "hunter2" not in result
        assert "[REDACTED]" in result

    def test_redacts_indented_yaml_token(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        result = redact_secrets_from_text("  api_key: my-secret-value\n")
        assert "my-secret-value" not in result
        assert "[REDACTED]" in result
        assert "api_key" in result

    def test_preserves_non_secret_yaml_keys(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        content = "host: localhost\nport: 8080\nlog_level: info\n"
        result = redact_secrets_from_text(content)
        assert result == content

    def test_preserves_trailing_newline_for_last_line_without_newline(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        # Last line without trailing newline: no newline should be added
        result = redact_secrets_from_text("API_KEY=secret")
        assert not result.endswith("\n")
        assert "[REDACTED]" in result

    def test_colon_style_in_multiline(self):
        from gptme.tools.subagent.context import redact_secrets_from_text

        content = "host: localhost\ngithub_token: ghp_abc123\nport: 5432\n"
        result = redact_secrets_from_text(content)
        assert "ghp_abc123" not in result
        assert "[REDACTED]" in result
        assert "host: localhost" in result
        assert "port: 5432" in result


class TestRedactSecretsNonThreadWarning:
    """Tests for redact_secrets=True debug log when used in non-thread modes."""

    def test_logs_when_redact_secrets_in_subprocess_mode(
        self, monkeypatch, tmp_path, caplog
    ):
        """redact_secrets=True with use_subprocess=True logs a debug message (not a warning,
        since redact_secrets=True is now the default and should not be noisy)."""
        import importlib
        import logging

        cli_main = importlib.import_module("gptme.cli.main")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")
        git_worktree = importlib.import_module("gptme.util.git_worktree")

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)
        monkeypatch.setattr(git_worktree, "get_git_root", lambda _: None)
        monkeypatch.setattr(
            subagent_api._exec,
            "_run_subagent_subprocess",
            MagicMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(subagent_api._exec, "_monitor_subprocess", lambda sa: None)

        with caplog.at_level(logging.DEBUG, logger="gptme.tools.subagent.api"):
            subagent(
                "warn-proc-agent",
                "do the thing",
                use_subprocess=True,
                redact_secrets=True,
            )

        with _subagents_lock:
            sa = next((s for s in _subagents if s.agent_id == "warn-proc-agent"), None)
        if sa and sa.thread:
            sa.thread.join(timeout=2)

        assert any(
            "redact_secrets=True" in record.message and "subprocess" in record.message
            for record in caplog.records
        ), f"Expected debug log not found in: {[r.message for r in caplog.records]}"
        # Must NOT be a warning — the default True should not pollute users' logs
        assert not any(
            "redact_secrets=True" in record.message
            and record.levelno >= logging.WARNING
            for record in caplog.records
        ), "redact_secrets no-op message should be DEBUG, not WARNING"


class TestRedactSecretsThreadExecution:
    """Tests that redact_secrets_from_messages is called in thread-mode execution."""

    def test_redact_secrets_calls_redaction_on_initial_msgs(
        self, monkeypatch, tmp_path
    ):
        """_create_subagent_thread with redact_secrets=True calls redact_secrets_from_messages."""
        import importlib

        from gptme.message import Message

        gptme_chat = importlib.import_module("gptme.chat")
        gptme_executor = importlib.import_module("gptme.executor")
        gptme_llm_models = importlib.import_module("gptme.llm.models")
        gptme_profiles = importlib.import_module("gptme.profiles")
        gptme_prompts = importlib.import_module("gptme.prompts")
        hooks_mod = importlib.import_module("gptme.tools.subagent.hooks")
        context_mod = importlib.import_module("gptme.tools.subagent.context")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")

        test_msgs = [Message("system", "API_KEY=supersecret\nHOST=localhost\n")]
        redacted_msgs = [Message("system", "API_KEY=[REDACTED]\nHOST=localhost\n")]
        redact_called_with: list = []

        def mock_redact(msgs):
            redact_called_with.extend(msgs)
            return redacted_msgs

        monkeypatch.setattr(gptme_chat, "chat", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            gptme_executor,
            "prepare_execution_environment",
            lambda **kwargs: None,
        )
        monkeypatch.setattr(gptme_llm_models, "set_default_model", lambda *args: None)
        monkeypatch.setattr(gptme_profiles, "get_profile", lambda _: None)
        monkeypatch.setattr(
            gptme_prompts, "get_prompt", lambda *args, **kwargs: list(test_msgs)
        )
        monkeypatch.setattr(
            hooks_mod,
            "_get_complete_instruction",
            lambda *args, **kwargs: "done",
        )
        monkeypatch.setattr(context_mod, "redact_secrets_from_messages", mock_redact)
        monkeypatch.setattr(
            exec_mod, "_ensure_subagent_signal_tools_loaded", lambda: None
        )
        monkeypatch.setattr(exec_mod, "get_tools", lambda: [])

        exec_mod._create_subagent_thread(
            prompt="do the thing",
            logdir=tmp_path / "logdir",
            model=None,
            context_mode="full",
            context_include=None,
            workspace=tmp_path,
            redact_secrets=True,
        )

        assert redact_called_with, "redact_secrets_from_messages was not called"
        assert any("supersecret" in msg.content for msg in redact_called_with)


class TestPlannerRedactSecrets:
    """Tests that _run_planner forwards redact_secrets to thread-mode executors."""

    def test_planner_forwards_redact_secrets_to_thread_executors(
        self, monkeypatch, tmp_path
    ):
        """_run_planner with redact_secrets=True passes it to _create_subagent_thread."""
        import importlib

        cli_main = importlib.import_module("gptme.cli.main")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")
        types_mod = importlib.import_module("gptme.tools.subagent.types")

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)

        captured_kwargs: list[dict] = []
        called_event = threading.Event()

        def fake_create_subagent_thread(**kwargs):
            captured_kwargs.append(kwargs)
            called_event.set()

        monkeypatch.setattr(
            exec_mod, "_create_subagent_thread", fake_create_subagent_thread
        )

        exec_mod._run_planner(
            agent_id="planner-test",
            prompt="orchestrate this",
            subtasks=[{"id": "task1", "description": "do part 1"}],
            execution_mode="sequential",
            redact_secrets=True,
        )

        assert called_event.wait(timeout=2.0), (
            "_create_subagent_thread was never called"
        )
        assert captured_kwargs[0].get("redact_secrets") is True, (
            "redact_secrets not forwarded to _create_subagent_thread"
        )

        with types_mod._subagents_lock:
            types_mod._subagents[:] = [
                s
                for s in types_mod._subagents
                if not s.agent_id.startswith("planner-test")
            ]

    def test_planner_logs_redact_secrets_in_subprocess_executor(
        self, monkeypatch, tmp_path, caplog
    ):
        """_run_planner with redact_secrets=True emits a debug log (not warning)
        when an executor uses subprocess, since redact_secrets=True is now the default."""
        import importlib
        import logging

        cli_main = importlib.import_module("gptme.cli.main")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")
        types_mod = importlib.import_module("gptme.tools.subagent.types")
        git_worktree = importlib.import_module("gptme.util.git_worktree")

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(git_worktree, "get_git_root", lambda _: None)
        subprocess_called = threading.Event()

        def subprocess_with_event(*args, **kwargs):
            subprocess_called.set()
            return MagicMock()

        monkeypatch.setattr(exec_mod, "_run_subagent_subprocess", subprocess_with_event)
        monkeypatch.setattr(exec_mod, "_monitor_subprocess", lambda sa: None)

        with caplog.at_level(logging.DEBUG, logger="gptme.tools.subagent.execution"):
            exec_mod._run_planner(
                agent_id="planner-warn",
                prompt="orchestrate",
                subtasks=[
                    {"id": "verify1", "description": "verify it", "role": "verify"}
                ],
                execution_mode="sequential",
                redact_secrets=True,
            )

        assert subprocess_called.wait(timeout=2.0), (
            "_run_subagent_subprocess was never called"
        )

        assert any(
            "redact_secrets=True" in record.message and "subprocess" in record.message
            for record in caplog.records
        ), f"Expected debug log not found in: {[r.message for r in caplog.records]}"
        # Must NOT be a warning — default True should not pollute users' logs
        assert not any(
            "redact_secrets=True" in record.message
            and record.levelno >= logging.WARNING
            for record in caplog.records
        ), "redact_secrets no-op message should be DEBUG, not WARNING"

        with types_mod._subagents_lock:
            types_mod._subagents[:] = [
                s
                for s in types_mod._subagents
                if not s.agent_id.startswith("planner-warn")
            ]


class TestContextWindow:
    """Tests for context_window parameter in _create_subagent_thread."""

    def test_context_window_zero_uses_minimal_context(self, monkeypatch, tmp_path):
        """context_window=0 skips workspace files and uses only agent identity + tools."""
        import importlib

        gptme_chat = importlib.import_module("gptme.chat")
        gptme_executor = importlib.import_module("gptme.executor")
        gptme_llm_models = importlib.import_module("gptme.llm.models")
        gptme_profiles = importlib.import_module("gptme.profiles")
        gptme_prompts = importlib.import_module("gptme.prompts")
        hooks_mod = importlib.import_module("gptme.tools.subagent.hooks")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")

        from gptme.message import Message

        workspace_msgs = [
            Message("system", "# Agent\nI am gptme."),
            Message("system", "# Tools\nHere are the tools."),
            Message("system", "WORKSPACE_SECRET=super_secret_value\nfile content here"),
        ]
        minimal_msgs = [
            Message("system", "# Agent\nI am gptme."),
            Message("system", "# Tools\nHere are the tools."),
        ]
        chat_initial_msgs: list = []

        def mock_chat(prompt_msgs, initial_msgs, **kwargs):
            chat_initial_msgs.extend(initial_msgs)

        def mock_get_prompt(*args, **kwargs):
            return list(workspace_msgs)

        def mock_prompt_gptme(*args, **kwargs):
            return iter([minimal_msgs[0]])

        def mock_prompt_tools(*args, **kwargs):
            return iter([minimal_msgs[1]])

        monkeypatch.setattr(gptme_chat, "chat", mock_chat)
        monkeypatch.setattr(
            gptme_executor, "prepare_execution_environment", lambda **kwargs: None
        )
        monkeypatch.setattr(gptme_llm_models, "set_default_model", lambda *args: None)
        monkeypatch.setattr(gptme_profiles, "get_profile", lambda _: None)
        monkeypatch.setattr(gptme_prompts, "get_prompt", mock_get_prompt)
        monkeypatch.setattr(gptme_prompts, "prompt_gptme", mock_prompt_gptme)
        monkeypatch.setattr(gptme_prompts, "prompt_tools", mock_prompt_tools)
        monkeypatch.setattr(
            hooks_mod, "_get_complete_instruction", lambda *args, **kwargs: "done"
        )
        monkeypatch.setattr(
            exec_mod, "_ensure_subagent_signal_tools_loaded", lambda: None
        )
        monkeypatch.setattr(exec_mod, "get_tools", lambda: [])

        exec_mod._create_subagent_thread(
            prompt="do the thing",
            logdir=tmp_path / "logdir",
            model=None,
            context_mode="full",
            context_include=None,
            workspace=tmp_path,
            redact_secrets=False,
            context_window=0,
        )

        # Should NOT include the workspace secret file
        contents = [m.content for m in chat_initial_msgs]
        assert not any("WORKSPACE_SECRET" in c for c in contents), (
            "context_window=0 should exclude workspace files"
        )

    def test_context_window_none_uses_full_context(self, monkeypatch, tmp_path):
        """context_window=None (default) uses full workspace context."""
        import importlib

        gptme_chat = importlib.import_module("gptme.chat")
        gptme_executor = importlib.import_module("gptme.executor")
        gptme_llm_models = importlib.import_module("gptme.llm.models")
        gptme_profiles = importlib.import_module("gptme.profiles")
        gptme_prompts = importlib.import_module("gptme.prompts")
        hooks_mod = importlib.import_module("gptme.tools.subagent.hooks")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")

        from gptme.message import Message

        workspace_msgs = [
            Message("system", "# Agent\nI am gptme."),
            Message("system", "WORKSPACE_SECRET=super_secret_value"),
        ]
        chat_initial_msgs: list = []

        monkeypatch.setattr(
            gptme_chat, "chat", lambda pm, im, **kw: chat_initial_msgs.extend(im)
        )
        monkeypatch.setattr(
            gptme_executor, "prepare_execution_environment", lambda **kwargs: None
        )
        monkeypatch.setattr(gptme_llm_models, "set_default_model", lambda *args: None)
        monkeypatch.setattr(gptme_profiles, "get_profile", lambda _: None)
        monkeypatch.setattr(
            gptme_prompts, "get_prompt", lambda *args, **kwargs: list(workspace_msgs)
        )
        monkeypatch.setattr(
            hooks_mod, "_get_complete_instruction", lambda *args, **kwargs: "done"
        )
        monkeypatch.setattr(
            exec_mod, "_ensure_subagent_signal_tools_loaded", lambda: None
        )
        monkeypatch.setattr(exec_mod, "get_tools", lambda: [])

        exec_mod._create_subagent_thread(
            prompt="do the thing",
            logdir=tmp_path / "logdir",
            model=None,
            context_mode="full",
            context_include=None,
            workspace=tmp_path,
            redact_secrets=False,
            context_window=None,
        )

        contents = [m.content for m in chat_initial_msgs]
        assert any("WORKSPACE_SECRET" in c for c in contents), (
            "context_window=None should include all workspace context"
        )

    def test_context_window_positive_truncates_messages(self, monkeypatch, tmp_path):
        """context_window=N limits workspace context to at most N messages.

        Agent-identity and tools messages do NOT count against the window —
        only the workspace context messages after them do.

        This test reflects the real get_prompt() structure: core messages
        (agent identity + tools) are COMBINED into a single message by
        _join_messages(), so n_base == 1, not 2. The mock uses workspace=None
        to distinguish the "count base messages" call from the "get full
        context" call, just as the real implementation does.
        """
        import importlib

        gptme_chat = importlib.import_module("gptme.chat")
        gptme_executor = importlib.import_module("gptme.executor")
        gptme_llm_models = importlib.import_module("gptme.llm.models")
        gptme_profiles = importlib.import_module("gptme.profiles")
        gptme_prompts = importlib.import_module("gptme.prompts")
        hooks_mod = importlib.import_module("gptme.tools.subagent.hooks")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")

        from gptme.message import Message

        # Reflect real get_prompt() structure:
        # - Core (gptme identity + tools) are COMBINED into a single message.
        # - Workspace files appear as separate messages after the combined core.
        core_combined_msg = Message(
            "system", "# Agent\nI am gptme.\n\n# Tools\nHere are the tools."
        )
        workspace_msgs = [Message("system", f"file {i} content") for i in range(10)]
        # Full workspace prompt: 1 combined core + 10 workspace files
        full_prompt_msgs = [core_combined_msg] + workspace_msgs
        # No-workspace prompt: just the combined core (used to compute n_base)
        base_only_msgs = [core_combined_msg]
        chat_initial_msgs: list = []

        def mock_get_prompt(*args, workspace=None, **kwargs):
            # Mimic real behavior: no workspace → only core; with workspace → core + files
            if workspace is None:
                return list(base_only_msgs)
            return list(full_prompt_msgs)

        monkeypatch.setattr(
            gptme_chat, "chat", lambda pm, im, **kw: chat_initial_msgs.extend(im)
        )
        monkeypatch.setattr(
            gptme_executor, "prepare_execution_environment", lambda **kwargs: None
        )
        monkeypatch.setattr(gptme_llm_models, "set_default_model", lambda *args: None)
        monkeypatch.setattr(gptme_profiles, "get_profile", lambda _: None)
        monkeypatch.setattr(gptme_prompts, "get_prompt", mock_get_prompt)
        monkeypatch.setattr(
            hooks_mod, "_get_complete_instruction", lambda *args, **kwargs: "done"
        )
        monkeypatch.setattr(
            exec_mod, "_ensure_subagent_signal_tools_loaded", lambda: None
        )
        monkeypatch.setattr(exec_mod, "get_tools", lambda: [])

        exec_mod._create_subagent_thread(
            prompt="do the thing",
            logdir=tmp_path / "logdir",
            model=None,
            context_mode="full",
            context_include=None,
            workspace=tmp_path,
            redact_secrets=False,
            context_window=3,
        )

        # The combined core message is always present
        assert core_combined_msg in chat_initial_msgs, "core message must be present"

        # context_window=3 → at most 3 workspace messages (those with "file" in content)
        ws_msgs_in_result = [m for m in chat_initial_msgs if "file" in m.content]
        assert len(ws_msgs_in_result) == 3, (
            f"context_window=3 should yield exactly 3 workspace messages, "
            f"got {len(ws_msgs_in_result)}"
        )


class TestPlannerForwardsContextWindow:
    """Tests that _run_planner forwards context_window to thread-mode executors."""

    def test_planner_forwards_context_window_to_thread_executors(
        self, monkeypatch, tmp_path
    ):
        """_run_planner with context_window=0 passes it to _create_subagent_thread."""
        import importlib

        cli_main = importlib.import_module("gptme.cli.main")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")
        types_mod = importlib.import_module("gptme.tools.subagent.types")

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)

        captured_kwargs: list[dict] = []
        called_event = threading.Event()

        def fake_create_subagent_thread(**kwargs):
            captured_kwargs.append(kwargs)
            called_event.set()

        monkeypatch.setattr(
            exec_mod, "_create_subagent_thread", fake_create_subagent_thread
        )
        monkeypatch.setattr(
            exec_mod, "get_slot_sem", lambda: __import__("threading").Semaphore(10)
        )

        subtasks = [{"id": "t1", "description": "do something"}]
        exec_mod._run_planner(
            agent_id="planner-cw",
            prompt="context",
            subtasks=subtasks,
            execution_mode="sequential",
            context_window=0,
        )

        called_event.wait(timeout=5)
        assert captured_kwargs, "_create_subagent_thread was never called"
        assert captured_kwargs[0].get("context_window") == 0, (
            f"Expected context_window=0, got {captured_kwargs[0].get('context_window')}"
        )

        with types_mod._subagents_lock:
            types_mod._subagents[:] = [
                s
                for s in types_mod._subagents
                if not s.agent_id.startswith("planner-cw")
            ]

    def test_subagent_planner_mode_forwards_redact_secrets_false(
        self, monkeypatch, tmp_path
    ):
        """subagent(mode='planner') forwards redact_secrets=False to _run_planner."""
        import importlib

        cli_main = importlib.import_module("gptme.cli.main")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")
        llm_models = importlib.import_module("gptme.llm.models")

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)

        captured: dict = {}

        def fake_run_planner(*args, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(exec_mod, "_run_planner", fake_run_planner)

        from gptme.tools.subagent.api import subagent
        from gptme.tools.subagent.types import SubtaskDef

        subtasks: list[SubtaskDef] = [{"id": "t1", "description": "check output"}]
        subagent(
            agent_id="planner-rs-test",
            prompt="verify something",
            mode="planner",
            subtasks=subtasks,
            redact_secrets=False,
            context_window=0,
        )

        assert captured.get("redact_secrets") is False, (
            "redact_secrets=False should be forwarded to _run_planner"
        )
        assert captured.get("context_window") == 0, (
            "context_window=0 should be forwarded to _run_planner"
        )


class TestContextWindowValidation:
    """Tests that invalid context_window values are rejected at the API boundary."""

    def test_negative_context_window_raises(self, monkeypatch, tmp_path):
        """context_window=-1 (or any negative value) should raise ValueError immediately."""
        import importlib

        llm_models = importlib.import_module("gptme.llm.models")
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)

        from gptme.tools.subagent.api import subagent

        with pytest.raises(ValueError, match="context_window"):
            subagent("agent", "do something", context_window=-1)

    def test_context_window_zero_is_valid(self, monkeypatch, tmp_path):
        """context_window=0 must not raise — it is the minimal-context mode.

        This validates the synchronous guard in subagent() (line 159) accepts
        context_window=0. Forwarding to _create_subagent_thread happens in a
        daemon thread and requires thread synchronization to test — that is
        covered at the integration level.
        """
        import importlib

        cli_main = importlib.import_module("gptme.cli.main")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")
        llm_models = importlib.import_module("gptme.llm.models")

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(
            exec_mod, "get_slot_sem", lambda: __import__("threading").Semaphore(10)
        )

        from gptme.tools.subagent.api import subagent
        from gptme.tools.subagent.types import _subagents, _subagents_lock

        subagent("isolation-test", "do something", context_window=0)

        # Clean up registered subagent
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "isolation-test"]


class TestWorkdir:
    """Tests for the workdir parameter of subagent()."""

    def _spawn(self, monkeypatch, tmp_path, **kwargs):
        """Helper: spawn a subagent with mocked deps and return the Subagent record."""
        import importlib

        cli_main = importlib.import_module("gptme.cli.main")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)
        monkeypatch.setattr(exec_mod, "_create_subagent_thread", lambda **kw: None)
        monkeypatch.setattr(exec_mod, "_cleanup_isolation", lambda sa: None)

        from gptme.tools.subagent.api import subagent
        from gptme.tools.subagent.types import _subagents, _subagents_lock

        agent_id = kwargs.pop("agent_id", "workdir-test")
        subagent(agent_id, "do something", **kwargs)

        with _subagents_lock:
            sa = next((s for s in _subagents if s.agent_id == agent_id), None)

        # cleanup
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != agent_id]

        return sa

    def test_workdir_nonexistent_raises(self, monkeypatch, tmp_path):
        """Passing a non-existent workdir raises ValueError immediately."""
        import importlib

        llm_models = importlib.import_module("gptme.llm.models")
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)

        from gptme.tools.subagent.api import subagent

        with pytest.raises(ValueError, match="workdir does not exist"):
            subagent("agent", "do something", workdir="/nonexistent/path/xyz")

    def test_workdir_file_raises(self, monkeypatch, tmp_path):
        """Passing a file (not a directory) as workdir raises ValueError immediately."""
        import importlib

        llm_models = importlib.import_module("gptme.llm.models")
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)

        from gptme.tools.subagent.api import subagent

        file_path = tmp_path / "not_a_dir.txt"
        file_path.write_text("i am a file")

        with pytest.raises(ValueError, match="workdir is not a directory"):
            subagent("agent", "do something", workdir=str(file_path))

    def _spawn_and_wait(self, monkeypatch, tmp_path, agent_id, exec_calls, **kwargs):
        """Spawn a subagent, capture workspace kwarg, wait for thread to finish."""
        import importlib

        cli_main = importlib.import_module("gptme.cli.main")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)

        def capture_thread(**kw):
            exec_calls.append(kw.get("workspace"))

        monkeypatch.setattr(exec_mod, "_create_subagent_thread", capture_thread)
        monkeypatch.setattr(exec_mod, "_cleanup_isolation", lambda sa: None)

        from gptme.tools.subagent.api import subagent
        from gptme.tools.subagent.types import _subagents, _subagents_lock

        subagent(agent_id, "do something", **kwargs)

        # Wait for the daemon thread to finish so exec_calls is populated
        with _subagents_lock:
            sa = next((s for s in _subagents if s.agent_id == agent_id), None)
        if sa and sa.thread:
            sa.thread.join(timeout=5)

        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != agent_id]

    def test_workdir_none_uses_cwd(self, monkeypatch, tmp_path):
        """When workdir=None the subagent is launched in Path.cwd()."""
        from pathlib import Path

        exec_calls: list[Path | None] = []
        self._spawn_and_wait(
            monkeypatch, tmp_path, "cwd-test", exec_calls, workdir=None
        )

        assert len(exec_calls) == 1
        assert exec_calls[0] == Path.cwd()

    def test_workdir_explicit_path_is_used(self, monkeypatch, tmp_path):
        """workdir=<path> is resolved and passed as the workspace to the thread."""
        from pathlib import Path

        workspace_dir = tmp_path / "myproject"
        workspace_dir.mkdir()

        exec_calls: list[Path | None] = []
        self._spawn_and_wait(
            monkeypatch, tmp_path, "workdir-explicit", exec_calls, workdir=workspace_dir
        )

        assert len(exec_calls) == 1
        assert exec_calls[0] == workspace_dir.resolve()

    def test_workdir_string_is_resolved_to_path(self, monkeypatch, tmp_path):
        """A workdir passed as a string is converted to a resolved Path."""
        from pathlib import Path

        workspace_dir = tmp_path / "strproject"
        workspace_dir.mkdir()

        exec_calls: list[Path | None] = []
        self._spawn_and_wait(
            monkeypatch, tmp_path, "workdir-str", exec_calls, workdir=str(workspace_dir)
        )

        assert len(exec_calls) == 1
        assert isinstance(exec_calls[0], Path)
        assert exec_calls[0] == workspace_dir.resolve()

    def test_workdir_validated_in_planner_mode(self, monkeypatch, tmp_path):
        """workdir is validated before the planner branch — a bad path raises."""
        import importlib

        exec_mod = importlib.import_module("gptme.tools.subagent.execution")
        llm_models = importlib.import_module("gptme.llm.models")

        monkeypatch.setattr(exec_mod, "_run_planner", lambda *args, **kw: None)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)

        from gptme.tools.subagent.api import subagent

        with pytest.raises(ValueError, match="workdir does not exist"):
            subagent(
                "agent",
                "do planner thing",
                mode="planner",
                subtasks=[{"id": "test-1", "description": "test subtask"}],
                workdir="/nonexistent",
            )

    def test_workdir_forwarded_to_planner(self, monkeypatch, tmp_path):
        """A valid workdir is resolved and forwarded to _run_planner."""
        import importlib

        exec_mod = importlib.import_module("gptme.tools.subagent.execution")
        llm_models = importlib.import_module("gptme.llm.models")

        captured: dict = {}
        monkeypatch.setattr(
            exec_mod, "_run_planner", lambda *args, **kw: captured.update(kw)
        )
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)

        from gptme.tools.subagent.api import subagent

        workspace_dir = tmp_path / "planner-workspace"
        workspace_dir.mkdir()

        subagent(
            "agent",
            "do planner thing",
            mode="planner",
            subtasks=[{"id": "test-1", "description": "test subtask"}],
            workdir=str(workspace_dir),
        )

        assert captured.get("workdir") == workspace_dir.resolve()

    def test_workdir_overridden_when_isolated(self, monkeypatch, tmp_path):
        """When isolated=True, workdir is overridden by the isolated workspace (tempdir or worktree)."""
        import importlib

        cli_main = importlib.import_module("gptme.cli.main")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")
        git_worktree = importlib.import_module("gptme.util.git_worktree")

        workspace_dir = tmp_path / "myproject"
        workspace_dir.mkdir()

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)

        # Mock get_git_root to return None → falls back to temp dir
        monkeypatch.setattr(git_worktree, "get_git_root", lambda _: None)

        exec_calls: list[Path | None] = []

        def capture_thread(**kw):
            exec_calls.append(kw.get("workspace"))

        monkeypatch.setattr(exec_mod, "_create_subagent_thread", capture_thread)
        monkeypatch.setattr(exec_mod, "_cleanup_isolation", lambda sa: None)

        from gptme.tools.subagent.api import subagent
        from gptme.tools.subagent.types import _subagents, _subagents_lock

        subagent("isolated-test", "do something", workdir=workspace_dir, isolated=True)

        with _subagents_lock:
            sa = next((s for s in _subagents if s.agent_id == "isolated-test"), None)
        if sa and sa.thread:
            sa.thread.join(timeout=5)

        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "isolated-test"]

        assert len(exec_calls) == 1
        # The workspace should NOT be the workdir — it's overridden by isolation
        assert exec_calls[0] != workspace_dir.resolve()
        # It should be a temp dir
        assert "subagent-isolated-test" in str(exec_calls[0])


# ---------------------------------------------------------------------------
# max_time watchdog tests
# ---------------------------------------------------------------------------


class TestMaxTimeWatchdog:
    """Tests for max_time auto-cancel watchdog in subagent()."""

    def setup_method(self):
        from gptme.tools.subagent.types import (
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()
        while not _completion_queue.empty():
            try:
                _completion_queue.get_nowait()
            except queue.Empty:
                break

    def test_timeout_status_is_valid(self):
        """ReturnType accepts 'timeout' as a valid status."""
        rt = ReturnType("timeout", "Auto-cancelled after 5.0s")
        assert rt.status == "timeout"
        assert "5.0s" in (rt.result or "")

    def test_timeout_subagent_noop_when_already_finished(self, tmp_path):
        """_timeout_subagent() is a no-op when the subagent already has a result."""
        from gptme.tools.subagent.api import _timeout_subagent
        from gptme.tools.subagent.types import (
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        # Register a finished subagent
        finished_thread = threading.Thread(target=lambda: None)
        finished_thread.start()
        finished_thread.join()  # Thread is done → is_running() returns False

        sa = Subagent(
            agent_id="done-agent",
            prompt="test",
            thread=finished_thread,
            logdir=tmp_path,
            model=None,
            execution_mode="thread",
        )
        with _subagents_lock:
            _subagents.append(sa)

        # Pre-seed a success result
        with _subagent_results_lock:
            _subagent_results["done-agent"] = ReturnType("success", "all done")

        # Watchdog should not overwrite the success result
        _timeout_subagent("done-agent", 5.0)

        with _subagent_results_lock:
            result = _subagent_results.get("done-agent")
        assert result is not None
        assert result.status == "success", (
            "timeout must not overwrite an already-finished result"
        )

    def test_timeout_subagent_noop_when_not_found(self):
        """_timeout_subagent() is a no-op when the agent_id is unknown."""
        from gptme.tools.subagent.api import _timeout_subagent

        # Should not raise
        _timeout_subagent("nonexistent-agent", 5.0)

    def test_timeout_subagent_cancels_running_thread(self, tmp_path):
        """_timeout_subagent() sets timeout result for a running thread-mode subagent."""
        from gptme.tools.subagent.api import _timeout_subagent
        from gptme.tools.subagent.types import (
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        barrier = threading.Barrier(2)
        stop = threading.Event()

        def slow_fn():
            barrier.wait()  # Signal we're running
            stop.wait(60)  # Would run forever without a cancel

        t = threading.Thread(target=slow_fn, daemon=True)
        t.start()
        barrier.wait()  # Ensure thread is alive before proceeding

        try:
            sa = Subagent(
                agent_id="running-thread",
                prompt="test",
                thread=t,
                logdir=tmp_path,
                model=None,
                execution_mode="thread",
            )
            with _subagents_lock:
                _subagents.append(sa)

            _timeout_subagent("running-thread", 0.5)

            with _subagent_results_lock:
                result = _subagent_results.get("running-thread")

            assert result is not None
            assert result.status == "timeout"
            assert "0.5s" in (result.result or "")

            # Completion notification should be queued
            notifications = []
            while not _completion_queue.empty():
                try:
                    notifications.append(_completion_queue.get_nowait())
                except queue.Empty:
                    break
            assert any(
                n[0] == "running-thread" and n[1] == "timeout" for n in notifications
            )
        finally:
            stop.set()
            t.join(timeout=1)

    def test_subagent_wait_returns_cached_timeout_while_thread_is_still_alive(
        self, tmp_path
    ):
        """subagent_wait() must surface a watchdog timeout even before the thread exits."""
        from gptme.tools.subagent.api import subagent_wait
        from gptme.tools.subagent.types import (
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        barrier = threading.Barrier(2)
        stop = threading.Event()

        def slow_fn():
            barrier.wait()
            stop.wait(60)

        t = threading.Thread(target=slow_fn, daemon=True)
        t.start()
        barrier.wait()

        try:
            sa = Subagent(
                agent_id="wait-timeout-thread",
                prompt="test",
                thread=t,
                logdir=tmp_path,
                model=None,
                execution_mode="thread",
            )
            with _subagents_lock:
                _subagents.append(sa)
            with _subagent_results_lock:
                _subagent_results["wait-timeout-thread"] = ReturnType(
                    "timeout", "Auto-cancelled after 0.5s (max_time exceeded)"
                )

            result = subagent_wait("wait-timeout-thread", timeout=0)

            assert result["status"] == "timeout"
            assert "max_time exceeded" in (result["result"] or "")
        finally:
            stop.set()
            t.join(timeout=1)

    def test_subagent_wait_polls_cache_after_join_timeout(self, tmp_path):
        """subagent_wait() handles join returning just before watchdog writes."""
        from gptme.tools.subagent.api import subagent_wait
        from gptme.tools.subagent.types import (
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        writer_threads: list[threading.Thread] = []

        def write_timeout_after_join():
            import time

            time.sleep(0.01)
            with _subagent_results_lock:
                _subagent_results["wait-race-thread"] = ReturnType(
                    "timeout", "Auto-cancelled after 0.5s (max_time exceeded)"
                )

        def join_returns_before_watchdog_write(timeout=None):
            writer = threading.Thread(target=write_timeout_after_join)
            writer.start()
            writer_threads.append(writer)

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.join.side_effect = join_returns_before_watchdog_write
        mock_thread.is_alive.return_value = True

        sa = Subagent(
            agent_id="wait-race-thread",
            prompt="test",
            thread=mock_thread,
            logdir=tmp_path,
            model=None,
            execution_mode="thread",
        )
        with _subagents_lock:
            _subagents.append(sa)

        try:
            result = subagent_wait("wait-race-thread", timeout=0)
        finally:
            for writer in writer_threads:
                writer.join(timeout=1)

        assert result["status"] == "timeout"
        assert "max_time exceeded" in (result["result"] or "")

    def test_completion_hook_timeout_message(self):
        """_subagent_completion_hook yields a ⏱️ message for timeout status."""
        notify_completion("hook-timeout-agent", "timeout", "Timed out after 10s")

        messages = list(
            _subagent_completion_hook(
                manager=MagicMock(),
                interactive=False,
                prompt_queue=MagicMock(),
            )
        )
        timeout_msgs = [
            m
            for m in messages
            if "hook-timeout-agent" in m.content and "⏱️" in m.content
        ]
        assert len(timeout_msgs) == 1
        assert "Timed out after 10s" in timeout_msgs[0].content

    def test_subagent_launches_watchdog_when_max_time_set(self, monkeypatch, tmp_path):
        """subagent() launches a threading.Timer when max_time is given."""
        import importlib

        cli_main = importlib.import_module("gptme.cli.main")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)
        monkeypatch.setattr(exec_mod, "_create_subagent_thread", lambda **kw: None)
        monkeypatch.setattr(exec_mod, "_cleanup_isolation", lambda sa: None)

        timers_started: list[float] = []

        class CapturingTimer:
            """Non-starting mock — records creation args without launching a real thread."""

            def __init__(self, interval, function, args=None, kwargs=None):
                self.interval = interval
                self.function = function
                self.args = args or ()
                self.kwargs = kwargs or {}
                self.daemon = True
                timers_started.append(interval)

            def start(self):
                pass  # Don't start a real thread — test only verifies Timer was called

            def cancel(self):
                pass

        monkeypatch.setattr(threading, "Timer", CapturingTimer)

        from gptme.tools.subagent.api import subagent
        from gptme.tools.subagent.types import _subagents, _subagents_lock

        subagent("watchdog-test", "do something", max_time=42.0)

        # Wait for the thread to complete
        with _subagents_lock:
            sa = next((s for s in _subagents if s.agent_id == "watchdog-test"), None)
        if sa and sa.thread:
            sa.thread.join(timeout=5)
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "watchdog-test"]

        assert 42.0 in timers_started, f"Expected 42.0s timer; got {timers_started}"

    def test_subagent_no_watchdog_when_max_time_none(self, monkeypatch, tmp_path):
        """subagent() does NOT launch a timer when max_time=None (default)."""
        import importlib

        cli_main = importlib.import_module("gptme.cli.main")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")
        llm_models = importlib.import_module("gptme.llm.models")
        profiles = importlib.import_module("gptme.profiles")

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)
        monkeypatch.setattr(exec_mod, "_create_subagent_thread", lambda **kw: None)
        monkeypatch.setattr(exec_mod, "_cleanup_isolation", lambda sa: None)

        timers_started: list[float] = []

        class CapturingTimer:
            """Non-starting mock — records creation args without launching a real thread."""

            def __init__(self, interval, function, args=None, kwargs=None):
                self.interval = interval
                self.function = function
                self.args = args or ()
                self.kwargs = kwargs or {}
                self.daemon = True
                timers_started.append(interval)

            def start(self):
                pass  # Don't start a real thread — test only verifies Timer was called

            def cancel(self):
                pass

        monkeypatch.setattr(threading, "Timer", CapturingTimer)

        from gptme.tools.subagent.api import subagent
        from gptme.tools.subagent.types import _subagents, _subagents_lock

        subagent("no-watchdog-test", "do something")  # max_time=None (default)

        with _subagents_lock:
            sa = next((s for s in _subagents if s.agent_id == "no-watchdog-test"), None)
        if sa and sa.thread:
            sa.thread.join(timeout=5)
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "no-watchdog-test"]

        assert len(timers_started) == 0, f"No timer expected; got {timers_started}"


# ---------------------------------------------------------------------------
# context_turns + parent context forwarding tests
# ---------------------------------------------------------------------------


class TestParentContextForwarding:
    """Tests for the context_turns parameter and parent message injection."""

    def test_build_parent_context_message_format(self):
        """_build_parent_context_message produces a correctly structured system message."""
        from gptme.message import Message
        from gptme.tools.subagent.execution import _build_parent_context_message

        msgs = [
            Message("user", "What is 1+1?"),
            Message("assistant", "It is 2."),
        ]
        result = _build_parent_context_message(msgs)
        assert result.role == "system"
        assert "Parent Conversation Context" in result.content
        assert "What is 1+1?" in result.content
        assert "It is 2." in result.content
        assert "User:" in result.content
        assert "Assistant:" in result.content

    def test_build_parent_context_message_guidance(self):
        """Parent context message includes guidance not to duplicate parent work."""
        from gptme.message import Message
        from gptme.tools.subagent.execution import _build_parent_context_message

        msgs = [Message("user", "hello")]
        result = _build_parent_context_message(msgs)
        assert "Focus on your own task" in result.content

    def test_context_turns_validation_zero(self):
        """context_turns=0 raises ValueError."""
        with pytest.raises(ValueError, match="context_turns must be None"):
            subagent("test-turns-zero", "task", context_turns=0)

    def test_context_turns_validation_negative(self):
        """context_turns=-1 raises ValueError."""
        with pytest.raises(ValueError, match="context_turns must be None"):
            subagent("test-turns-neg", "task", context_turns=-1)

    def test_context_turns_no_active_log_warns(self, monkeypatch, caplog):
        """context_turns with no active LogManager logs a warning and spawns without parent context."""
        import logging

        import gptme.tools.subagent.execution as exec_mod
        from gptme.logmanager import LogManager

        # No active log in this context
        monkeypatch.setattr(LogManager, "get_current_log", staticmethod(lambda: None))

        captured_parent_msgs = []

        def mock_create_thread(**kw):
            captured_parent_msgs.append(kw.get("parent_messages"))

        monkeypatch.setattr(exec_mod, "_create_subagent_thread", mock_create_thread)

        with caplog.at_level(logging.WARNING, logger="gptme.tools.subagent.api"):
            subagent("test-no-log", "do something", context_turns=3)

        # Wait briefly for the daemon thread to call mock_create_thread
        import time

        for _ in range(20):
            if captured_parent_msgs:
                break
            time.sleep(0.05)

        # The spawn should still happen
        assert len(captured_parent_msgs) == 1
        # But parent_messages should be None (no log found)
        assert captured_parent_msgs[0] is None
        assert any("context_turns" in r.message for r in caplog.records)

        # Cleanup
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "test-no-log"]

    def test_context_turns_slices_log(self, monkeypatch):
        """context_turns=2 forwards the last 2 turns (from 2nd-to-last user msg onward)."""
        import gptme.tools.subagent.execution as exec_mod
        from gptme.logmanager import Log, LogManager
        from gptme.message import Message

        # Build a log with 3 realistic user+assistant turns
        msgs = [
            Message("user", "turn 1 user"),
            Message("assistant", "turn 1 assistant"),
            Message("user", "turn 2 user"),
            Message("assistant", "turn 2 assistant"),
            Message("user", "turn 3 user"),
            Message("assistant", "turn 3 assistant"),
        ]
        mock_log = MagicMock(spec=LogManager)
        mock_log.log = Log(msgs)

        monkeypatch.setattr(
            LogManager, "get_current_log", staticmethod(lambda: mock_log)
        )

        captured: list = []

        def mock_create_thread(**kw):
            captured.append(kw.get("parent_messages"))

        monkeypatch.setattr(exec_mod, "_create_subagent_thread", mock_create_thread)

        subagent("test-slice", "do something", context_turns=2)

        import time

        for _ in range(20):
            if captured:
                break
            time.sleep(0.05)

        assert len(captured) == 1
        # context_turns=2 → starts from 2nd-to-last user message (index 2)
        assert captured[0] is not None
        assert len(captured[0]) == 4
        assert captured[0][0].content == "turn 2 user"
        assert captured[0][-1].content == "turn 3 assistant"

        # Cleanup
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "test-slice"]

    def test_context_turns_slices_log_with_tool_results(self, monkeypatch):
        """context_turns correctly includes tool-result system msgs within a turn."""
        import gptme.tools.subagent.execution as exec_mod
        from gptme.logmanager import Log, LogManager
        from gptme.message import Message

        # Log with 2 turns; turn 1 has a tool-result system message in the middle
        msgs = [
            Message("user", "turn 1 user"),
            Message("assistant", "calling tool"),
            Message("system", "[tool result]"),
            Message("assistant", "turn 1 final"),
            Message("user", "turn 2 user"),
            Message("assistant", "turn 2 assistant"),
        ]
        mock_log = MagicMock(spec=LogManager)
        mock_log.log = Log(msgs)

        monkeypatch.setattr(
            LogManager, "get_current_log", staticmethod(lambda: mock_log)
        )

        captured: list = []

        def mock_create_thread(**kw):
            captured.append(kw.get("parent_messages"))

        monkeypatch.setattr(exec_mod, "_create_subagent_thread", mock_create_thread)

        subagent("test-slice-tool", "do something", context_turns=2)

        import time

        for _ in range(20):
            if captured:
                break
            time.sleep(0.05)

        assert len(captured) == 1
        # Both turns included; tool-result system message is included within turn 1
        assert captured[0] is not None
        assert len(captured[0]) == 6
        assert captured[0][0].content == "turn 1 user"
        assert captured[0][-1].content == "turn 2 assistant"

        # Cleanup
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "test-slice-tool"]

    def test_context_turns_none_passes_none(self, monkeypatch):
        """context_turns=None (default) passes parent_messages=None to thread."""
        import gptme.tools.subagent.execution as exec_mod

        captured: list = []

        def mock_create_thread(**kw):
            captured.append(kw.get("parent_messages"))

        monkeypatch.setattr(exec_mod, "_create_subagent_thread", mock_create_thread)

        subagent("test-none-turns", "do something")

        import time

        for _ in range(20):
            if captured:
                break
            time.sleep(0.05)

        assert len(captured) == 1
        assert captured[0] is None

        # Cleanup
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "test-none-turns"]

    def test_context_turns_fallback_skips_leading_system_messages(self, monkeypatch):
        """When context_turns exceeds available turns, fallback starts at first user msg.

        A real gptme log opens with system bootstrap messages (identity, workspace
        context). When context_turns > available user turns the fallback must start at
        user_indices[0], not at 0, so those setup messages are never forwarded.
        """
        import gptme.tools.subagent.execution as exec_mod
        from gptme.logmanager import Log, LogManager
        from gptme.message import Message

        # Log with leading system messages before the first user message
        msgs = [
            Message("system", "[agent identity]"),
            Message("system", "[workspace context]"),
            Message("user", "first user task"),
            Message("assistant", "first response"),
        ]
        mock_log = MagicMock(spec=LogManager)
        mock_log.log = Log(msgs)

        monkeypatch.setattr(
            LogManager, "get_current_log", staticmethod(lambda: mock_log)
        )

        captured: list = []

        def mock_create_thread(**kw):
            captured.append(kw.get("parent_messages"))

        monkeypatch.setattr(exec_mod, "_create_subagent_thread", mock_create_thread)

        # context_turns=5 exceeds the 1 available user turn → triggers fallback
        subagent("test-fallback-skip-system", "do something", context_turns=5)

        import time

        for _ in range(20):
            if captured:
                break
            time.sleep(0.05)

        assert len(captured) == 1
        assert captured[0] is not None
        # Must NOT include the leading system bootstrap messages
        assert captured[0][0].content == "first user task"
        assert captured[0][0].role == "user"
        assert len(captured[0]) == 2  # user + assistant only

        # Cleanup
        with _subagents_lock:
            _subagents[:] = [
                s for s in _subagents if s.agent_id != "test-fallback-skip-system"
            ]


# ---------------------------------------------------------------------------
# BatchJob.wait_all() concurrency tests
# ---------------------------------------------------------------------------


class TestBatchJobWaitAll:
    """Tests for BatchJob.wait_all() concurrent behavior."""

    def setup_method(self):
        """Clear global subagent registries before each test."""
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()

    def _register_done_subagent(self, agent_id: str, result: str = "done") -> None:
        """Register a fake already-completed subagent."""
        import threading
        from unittest.mock import MagicMock

        from gptme.tools.subagent.types import Subagent, _subagent_results

        t = MagicMock(spec=threading.Thread)
        t.is_alive.return_value = False
        t.join = MagicMock()

        sa = Subagent(
            agent_id=agent_id,
            prompt="test",
            thread=t,
            logdir=Path("/tmp/fake-log"),
            model=None,
        )
        with _subagents_lock:
            _subagents.append(sa)
        with _subagent_results_lock:
            _subagent_results[agent_id] = ReturnType("success", result)

    def test_wait_all_returns_all_results(self):
        """wait_all() returns one entry per agent."""
        from gptme.tools.subagent.batch import BatchJob

        self._register_done_subagent("w1", "result-1")
        self._register_done_subagent("w2", "result-2")
        self._register_done_subagent("w3", "result-3")

        job = BatchJob(agent_ids=["w1", "w2", "w3"])
        results = job.wait_all(timeout=5)

        assert set(results.keys()) == {"w1", "w2", "w3"}
        assert results["w1"]["status"] == "success"
        assert results["w1"]["result"] == "result-1"
        assert results["w3"]["result"] == "result-3"

    def test_wait_all_concurrent_vs_sequential_order_independence(self):
        """Concurrent wait_all() returns same keys regardless of insertion order."""
        from gptme.tools.subagent.batch import BatchJob

        self._register_done_subagent("c1", "x")
        self._register_done_subagent("c2", "y")

        job1 = BatchJob(agent_ids=["c1", "c2"])
        job2 = BatchJob(agent_ids=["c2", "c1"])

        r1 = job1.wait_all(timeout=5)
        r2 = job2.wait_all(timeout=5)

        assert set(r1.keys()) == set(r2.keys()) == {"c1", "c2"}

    def test_wait_all_skips_already_collected(self):
        """wait_all() skips agent_ids whose results are already in BatchJob.results."""
        from gptme.tools.subagent.batch import BatchJob

        self._register_done_subagent("skip1", "cached")

        job = BatchJob(
            agent_ids=["skip1"],
            results={"skip1": ReturnType("success", "pre-cached")},
        )
        results = job.wait_all(timeout=5)
        assert results["skip1"]["result"] == "pre-cached"

    def test_wait_all_handles_missing_agent_gracefully(self):
        """wait_all() reports failure for an agent_id not in the registry."""
        from gptme.tools.subagent.batch import BatchJob

        job = BatchJob(agent_ids=["ghost-agent"])
        results = job.wait_all(timeout=1)

        assert "ghost-agent" in results
        assert results["ghost-agent"]["status"] == "failure"

    def test_wait_all_futures_timeout_marks_all_timed_out(self, monkeypatch):
        """wait_all() marks all agents timed-out when as_completed raises TimeoutError.

        Exercises the FuturesTimeoutError catch path in wait_all() — the edge case
        where as_completed itself times out before any future completes.
        """
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import BatchJob

        def mock_as_completed(futures, timeout=None):
            raise FuturesTimeoutError

        monkeypatch.setattr(batch_mod, "as_completed", mock_as_completed)

        self._register_done_subagent("agent-a", "not-reached")
        self._register_done_subagent("agent-b", "not-reached")

        job = BatchJob(agent_ids=["agent-a", "agent-b"])
        results = job.wait_all(timeout=30)

        assert set(results.keys()) == {"agent-a", "agent-b"}
        assert results["agent-a"]["status"] == "timeout"
        assert results["agent-b"]["status"] == "timeout"
        assert "30s" in results["agent-a"]["result"]

    def test_wait_all_maps_running_result_to_timeout(self):
        """An agent still running when subagent_wait() returns is reported as
        "timeout", not the non-terminal status="running"/result=None.

        Regression: subagent_wait() returns {"status": "running", "result": None}
        for thread/ACP agents that are still alive after the wait (they can't be
        force-killed). wait_all() previously leaked that verbatim, violating the
        documented "timeout" contract and crashing callers that index into result
        (e.g. the subagent_parallel docstring's own result['result'][:80]).
        """
        from unittest.mock import patch

        from gptme.tools.subagent.batch import BatchJob

        def fake_wait(agent_id, timeout=60, max_result_chars=2000):
            # join(timeout) expires while the thread is still alive → subagent_wait
            # falls back to status()=="running" with result=None.
            return {"status": "running", "result": None}

        job = BatchJob(agent_ids=["stuck-worker"])
        with patch("gptme.tools.subagent.batch.subagent_wait", side_effect=fake_wait):
            results = job.wait_all(timeout=2)

        r = results["stuck-worker"]
        assert r["status"] == "timeout"
        # result must be a usable string, not None — docstring consumers slice it
        assert r["result"] is not None
        assert r["result"][:80]  # must not raise TypeError


# ---------------------------------------------------------------------------
# subagent_parallel tests
# ---------------------------------------------------------------------------


class TestSubagentParallel:
    """Tests for subagent_parallel() fan-out helper."""

    def setup_method(self):
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()

    def test_empty_tasks_returns_empty_list(self):
        from gptme.tools.subagent.batch import subagent_parallel

        assert subagent_parallel([]) == []

    def test_returns_list_in_task_order(self, monkeypatch):
        """Results are returned in the same order as tasks, not completion order."""
        import gptme.tools.subagent.api as api_mod
        from gptme.tools.subagent.batch import subagent_parallel
        from gptme.tools.subagent.types import Subagent

        task_order = []

        def mock_subagent(agent_id, prompt, **kwargs):
            task_order.append(agent_id)
            # Register a fake completed subagent
            import threading

            t = MagicMock(spec=threading.Thread)
            t.is_alive.return_value = False
            t.join = MagicMock()
            sa = Subagent(
                agent_id=agent_id,
                prompt=prompt,
                thread=t,
                logdir=Path("/tmp/fake-log"),
                model=None,
            )
            with _subagents_lock:
                _subagents.append(sa)
            with _subagent_results_lock:
                _subagent_results[agent_id] = ReturnType(
                    "success", f"result-for-{agent_id}"
                )

        monkeypatch.setattr(api_mod, "subagent", mock_subagent)
        # Also patch the import inside batch.py
        import gptme.tools.subagent.batch as batch_mod

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        tasks = [("p-a", "prompt a"), ("p-b", "prompt b"), ("p-c", "prompt c")]
        results = subagent_parallel(tasks, timeout=5)

        assert len(results) == 3
        assert results[0]["result"] == "result-for-p-a"
        assert results[1]["result"] == "result-for-p-b"
        assert results[2]["result"] == "result-for-p-c"
        assert [r["status"] for r in results] == ["success", "success", "success"]

    def test_failure_result_for_missing_agent(self, monkeypatch):
        """If a subagent never registers, parallel returns failure for that slot."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_parallel

        def mock_subagent(agent_id, prompt, **kwargs):
            # Intentionally do NOT register anything
            pass

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        results = subagent_parallel([("ghost", "do work")], timeout=1)

        assert len(results) == 1
        assert results[0]["status"] == "failure"

    def test_passes_kwargs_to_each_subagent(self, monkeypatch):
        """Keyword args (model, isolated, etc.) are forwarded to every subagent."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_parallel

        captured_kwargs: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured_kwargs.append(kwargs)
            import threading

            t = MagicMock(spec=threading.Thread)
            t.is_alive.return_value = False
            t.join = MagicMock()
            sa = __import__(
                "gptme.tools.subagent.types", fromlist=["Subagent"]
            ).Subagent(
                agent_id=agent_id,
                prompt=prompt,
                thread=t,
                logdir=Path("/tmp/fake-log"),
                model=None,
            )
            with _subagents_lock:
                _subagents.append(sa)
            with _subagent_results_lock:
                _subagent_results[agent_id] = ReturnType("success", "ok")

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        subagent_parallel(
            [("k1", "p1"), ("k2", "p2")],
            model="openai/gpt-4o-mini",
            isolated=True,
            timeout=5,
        )

        assert len(captured_kwargs) == 2
        for kw in captured_kwargs:
            assert kw.get("model") == "openai/gpt-4o-mini"
            assert kw.get("isolated") is True

    def test_startup_failure_cancels_already_started_agents(self, monkeypatch):
        """If subagent() raises mid-loop, already-started agents are cancelled."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_parallel

        started: list[str] = []
        cancelled: list[str] = []
        call_count = 0

        def mock_subagent(agent_id, prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise RuntimeError("simulated ACP setup failure")
            started.append(agent_id)

        def mock_cancel(agent_id):
            cancelled.append(agent_id)
            return f"cancelled {agent_id}"

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)
        monkeypatch.setattr(batch_mod, "subagent_cancel", mock_cancel)

        with pytest.raises(RuntimeError, match="simulated ACP setup failure"):
            subagent_parallel([("s-a", "p1"), ("s-b", "p2"), ("s-c", "p3")])

        # First agent was started before the failure; it must be cancelled
        assert "s-a" in cancelled
        # Second agent failed to start, so nothing to cancel there
        assert "s-b" not in cancelled


# ---------------------------------------------------------------------------
# output_schema tests
# ---------------------------------------------------------------------------


class _SampleSchema:
    """Minimal Pydantic-like schema stub for testing schema hints."""

    @classmethod
    def model_json_schema(cls):
        return {
            "type": "object",
            "properties": {"value": {"type": "integer"}, "label": {"type": "string"}},
            "required": ["value", "label"],
        }


class TestOutputSchema:
    """Tests for output_schema handling in complete-block parsing and instructions."""

    def _make_subagent(
        self,
        tmp_path: Path,
        content: str,
        output_schema=None,
        agent_id: str = "schema-test",
    ) -> Subagent:
        logdir = tmp_path / "subagent-log"
        logdir.mkdir(exist_ok=True)
        (logdir / "conversation.jsonl").write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "content": content,
                    "timestamp": "2025-01-01T00:00:00+00:00",
                }
            )
            + "\n"
        )
        return Subagent(
            agent_id=agent_id,
            prompt="task",
            thread=None,
            logdir=logdir,
            model=None,
            output_schema=output_schema,
        )

    # -- _get_complete_instruction with output_schema --

    def test_instruction_without_schema_has_no_json_hint(self):
        instruction = _get_complete_instruction()
        assert "JSON" not in instruction
        assert "schema" not in instruction.lower()

    def test_instruction_with_schema_includes_schema_hint(self):
        instruction = _get_complete_instruction(output_schema=_SampleSchema)
        assert "JSON" in instruction
        assert '"type"' in instruction  # schema JSON is embedded
        assert "value" in instruction  # property from schema

    def test_instruction_with_schema_includes_important_warning(self):
        instruction = _get_complete_instruction(output_schema=_SampleSchema)
        assert "IMPORTANT" in instruction

    def test_instruction_without_schema_has_generic_placeholder(self):
        instruction = _get_complete_instruction()
        assert "Your complete answer here." in instruction

    def test_instruction_with_schema_replaces_generic_placeholder(self):
        instruction = _get_complete_instruction(output_schema=_SampleSchema)
        assert "Your complete answer here." not in instruction

    # -- _normalize_json_result --

    def test_normalize_json_result_no_schema_passthrough(self, tmp_path):
        sa = self._make_subagent(tmp_path, "anything", output_schema=None)
        assert sa._normalize_json_result("some text") == "some text"

    def test_normalize_json_result_valid_json_canonicalized(self, tmp_path):
        sa = self._make_subagent(tmp_path, "{}", output_schema=_SampleSchema)
        raw = '{"value":  42,  "label": "hello"}'
        result = sa._normalize_json_result(raw)
        # Must be valid JSON (parseable)
        parsed = json.loads(result)
        assert parsed["value"] == 42
        assert parsed["label"] == "hello"

    def test_normalize_json_result_invalid_json_passthrough_with_warning(
        self, tmp_path, caplog
    ):
        import logging

        sa = self._make_subagent(tmp_path, "{}", output_schema=_SampleSchema)
        with caplog.at_level(logging.WARNING, logger="gptme.tools.subagent.types"):
            result = sa._normalize_json_result("not valid json {{")
        assert result == "not valid json {{"
        assert "not valid JSON" in caplog.text

    # -- _read_log() with output_schema --

    def test_read_log_with_schema_valid_json_complete_block(self, tmp_path):
        content = '```complete\n{"value": 7, "label": "ok"}\n```'
        sa = self._make_subagent(tmp_path, content, output_schema=_SampleSchema)
        result = sa._read_log()
        assert result.status == "success"
        assert isinstance(result.result, str)
        parsed = json.loads(result.result.split("\n\nFull log:")[0])
        assert parsed == {"value": 7, "label": "ok"}

    def test_read_log_with_schema_invalid_json_still_succeeds(self, tmp_path, caplog):
        """Schema mismatch degrades gracefully — result is returned as raw text."""
        import logging

        content = "```complete\nnot json at all\n```"
        sa = self._make_subagent(
            tmp_path, content, output_schema=_SampleSchema, agent_id="bad-json"
        )
        with caplog.at_level(logging.WARNING, logger="gptme.tools.subagent.types"):
            result = sa._read_log()
        assert result.status == "success"
        assert "not json at all" in (result.result or "")
        assert "not valid JSON" in caplog.text

    def test_read_log_without_schema_plain_text_unchanged(self, tmp_path):
        content = "```complete\nhello world\n```"
        sa = self._make_subagent(tmp_path, content, output_schema=None)
        result = sa._read_log()
        assert result.status == "success"
        assert "hello world" in (result.result or "")
        # Must NOT be interpreted as JSON
        assert result.result is not None
        assert isinstance(result.result, str)
        assert result.result.startswith("hello world")

    def test_read_log_with_schema_empty_complete_tool_no_json_warning(
        self, tmp_path, caplog, monkeypatch
    ):
        import logging

        monkeypatch.setattr(
            subagent_types.ToolUse,
            "iter_from_content",
            staticmethod(
                lambda content: iter([subagent_types.ToolUse("complete", [], "")])
            ),
        )
        sa = self._make_subagent(
            tmp_path,
            "ignored when complete tool is parsed",
            output_schema=_SampleSchema,
        )
        with caplog.at_level(logging.WARNING, logger="gptme.tools.subagent.types"):
            result = sa._read_log()
        assert result.status == "success"
        assert "Task completed (no summary provided)" in (result.result or "")
        assert "not valid JSON" not in caplog.text

    def test_read_log_with_schema_empty_complete_block_fallback_no_json_warning(
        self, tmp_path, caplog, monkeypatch
    ):
        import logging

        monkeypatch.setattr(
            subagent_types.ToolUse,
            "iter_from_content",
            staticmethod(lambda content: iter(())),
        )
        sa = self._make_subagent(
            tmp_path, "```complete\n\n```", output_schema=_SampleSchema
        )
        with caplog.at_level(logging.WARNING, logger="gptme.tools.subagent.types"):
            result = sa._read_log()
        assert result.status == "success"
        assert "Task completed (no summary provided)" in (result.result or "")
        assert "not valid JSON" not in caplog.text


# ---------------------------------------------------------------------------
# _parse_result() helper + subagent_parallel/subagent_batch new parameters
# ---------------------------------------------------------------------------


class TestParseResult:
    """Tests for the _parse_result() helper in batch.py."""

    def test_passthrough_when_schema_is_none(self):
        from gptme.tools.subagent.batch import _parse_result

        d = {"status": "success", "result": "some text"}
        assert _parse_result(d, None) is d

    def test_passthrough_on_failure_status(self):
        from gptme.tools.subagent.batch import _parse_result

        d = {"status": "failure", "result": '{"value": 1}'}

        class Schema:
            @classmethod
            def model_json_schema(cls):
                return {}

        result = _parse_result(d, Schema)
        assert result["result"] == '{"value": 1}'

    def test_passthrough_when_result_is_none(self):
        from gptme.tools.subagent.batch import _parse_result

        d = {"status": "success", "result": None}

        class Schema:
            @classmethod
            def model_json_schema(cls):
                return {}

        result = _parse_result(d, Schema)
        assert result["result"] is None

    def test_plain_json_parse_without_pydantic(self):
        from gptme.tools.subagent.batch import _parse_result

        class PlainSchema:
            pass

        d = {"status": "success", "result": '{"x": 42, "y": "hello"}'}
        result = _parse_result(d, PlainSchema)
        assert result["result"] == {"x": 42, "y": "hello"}
        assert "parse_error" not in result

    def test_pydantic_model_validate_called(self):
        from gptme.tools.subagent.batch import _parse_result

        class FakePydantic:
            @classmethod
            def model_validate(cls, data):
                return cls()

            def model_dump(self):
                return {"validated": True}

        d = {"status": "success", "result": '{"validated": true}'}
        result = _parse_result(d, FakePydantic)
        assert result["result"] == {"validated": True}
        assert "parse_error" not in result

    def test_parse_error_key_on_invalid_json(self):
        from gptme.tools.subagent.batch import _parse_result

        class Schema:
            pass

        d = {"status": "success", "result": "not valid json {{"}
        result = _parse_result(d, Schema)
        assert "parse_error" in result
        assert result["result"] == "not valid json {{"

    def test_original_result_preserved_on_parse_error(self):
        from gptme.tools.subagent.batch import _parse_result

        class Schema:
            pass

        original = "bad json"
        d = {"status": "success", "result": original}
        result = _parse_result(d, Schema)
        assert result["result"] == original

    def test_already_parsed_dict_returned_as_is(self):
        """_parse_result is idempotent: already-parsed dict results pass through.

        When subagent_wait() parses output_schema automatically (for direct callers),
        then subagent_parallel() / BatchJob.wait_all() call _parse_result() a second
        time on the already-parsed dict. This must not raise TypeError or inject a
        spurious parse_error key.
        """
        from gptme.tools.subagent.batch import _parse_result

        class Schema:
            @classmethod
            def model_validate(cls, obj):
                return cls()

            def model_dump(self):
                return {"key": "value"}

        parsed_dict = {"key": "value"}
        d = {"status": "success", "result": parsed_dict}
        result = _parse_result(d, Schema)
        # Must not add parse_error, must not crash, must return result unchanged
        assert "parse_error" not in result
        assert result["result"] is parsed_dict


class TestSubagentParallelOutputSchema:
    """Tests for output_schema support in subagent_parallel()."""

    def setup_method(self):
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()

    def _register_fake_agent(
        self,
        agent_id: str,
        result_text: str,
        status: Literal[
            "running", "success", "failure", "clarification_needed", "timeout"
        ] = "success",
    ):
        import threading

        t = MagicMock(spec=threading.Thread)
        t.is_alive.return_value = False
        t.join = MagicMock()
        sa = Subagent(
            agent_id=agent_id,
            prompt="test prompt",
            thread=t,
            logdir=Path("/tmp/fake-log"),
            model=None,
        )
        with _subagents_lock:
            _subagents.append(sa)
        with _subagent_results_lock:
            _subagent_results[agent_id] = ReturnType(status, result_text)

    def test_output_schema_none_returns_raw_strings(self, monkeypatch):
        """Without output_schema, results have raw string in 'result'."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_parallel

        def mock_subagent(agent_id, prompt, **kwargs):
            self._register_fake_agent(agent_id, '{"value": 1}')

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        results = subagent_parallel([("a", "prompt")], timeout=5)
        assert results[0]["result"] == '{"value": 1}'

    def test_output_schema_parses_json_results(self, monkeypatch):
        """With output_schema set, JSON results are automatically parsed."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_parallel

        class MySchema:
            pass

        def mock_subagent(agent_id, prompt, **kwargs):
            self._register_fake_agent(agent_id, '{"score": 99}')

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        results = subagent_parallel(
            [("x", "prompt")], output_schema=MySchema, timeout=5
        )
        assert results[0]["result"] == {"score": 99}
        assert "parse_error" not in results[0]

    def test_output_schema_forwarded_to_subagent(self, monkeypatch):
        """output_schema is forwarded to each subagent() call."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_parallel

        class MySchema:
            pass

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)
            self._register_fake_agent(agent_id, "ok")

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        subagent_parallel(
            [("s1", "p1"), ("s2", "p2")], output_schema=MySchema, timeout=5
        )

        assert len(captured) == 2
        for kw in captured:
            assert kw.get("output_schema") is MySchema

    def test_output_schema_parse_error_does_not_raise(self, monkeypatch):
        """Invalid JSON in result adds parse_error but doesn't raise."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_parallel

        class MySchema:
            pass

        def mock_subagent(agent_id, prompt, **kwargs):
            self._register_fake_agent(agent_id, "not json at all")

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        results = subagent_parallel([("bad", "p")], output_schema=MySchema, timeout=5)
        assert results[0]["status"] == "success"
        assert "parse_error" in results[0]
        assert results[0]["result"] == "not json at all"

    def test_workdir_forwarded_to_subagent(self, monkeypatch, tmp_path):
        """workdir is forwarded to each subagent() call."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_parallel

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)
            self._register_fake_agent(agent_id, "ok")

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        subagent_parallel([("w1", "p1")], workdir=tmp_path, timeout=5)

        assert captured[0].get("workdir") == tmp_path

    def test_context_turns_forwarded_to_subagent(self, monkeypatch):
        """context_turns is forwarded to each subagent() call."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_parallel

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)
            self._register_fake_agent(agent_id, "ok")

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        subagent_parallel([("c1", "p1"), ("c2", "p2")], context_turns=5, timeout=5)

        assert len(captured) == 2
        for kw in captured:
            assert kw.get("context_turns") == 5


class TestSubagentBatchNewParameters:
    """Tests for new parameters in subagent_batch()."""

    def setup_method(self):
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()

    def test_model_forwarded_to_subagent(self, monkeypatch):
        """model parameter is forwarded to each subagent() call."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_batch

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        subagent_batch([("t1", "p1"), ("t2", "p2")], model="openai/gpt-4o")

        assert len(captured) == 2
        for kw in captured:
            assert kw.get("model") == "openai/gpt-4o"

    def test_profile_forwarded_to_subagent(self, monkeypatch):
        """profile parameter is forwarded to each subagent() call."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_batch

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        subagent_batch([("t1", "p1")], profile="explorer")

        assert captured[0].get("profile") == "explorer"

    def test_isolated_forwarded_to_subagent(self, monkeypatch):
        """isolated parameter is forwarded to each subagent() call."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_batch

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        subagent_batch([("t1", "p1")], isolated=True)

        assert captured[0].get("isolated") is True

    def test_output_schema_forwarded_to_subagent(self, monkeypatch):
        """output_schema is forwarded to each subagent() call."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_batch

        class Schema:
            pass

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        subagent_batch([("t1", "p1"), ("t2", "p2")], output_schema=Schema)

        assert len(captured) == 2
        for kw in captured:
            assert kw.get("output_schema") is Schema

    def test_strip_log_suffix_strips_full_log(self):
        """_strip_log_suffix removes the \\n\\nFull log: ... suffix."""
        from gptme.tools.subagent.batch import _strip_log_suffix

        text = '{"score": 99}\n\nFull log: /tmp/agent-xyz/logs'
        assert _strip_log_suffix(text) == '{"score": 99}'

    def test_strip_log_suffix_passthrough_when_no_suffix(self):
        """_strip_log_suffix returns text unchanged when no log suffix."""
        from gptme.tools.subagent.batch import _strip_log_suffix

        text = '{"score": 99}'
        assert _strip_log_suffix(text) == '{"score": 99}'

    def test_strip_log_suffix_passthrough_empty_string(self):
        """_strip_log_suffix handles empty strings."""
        from gptme.tools.subagent.batch import _strip_log_suffix

        assert _strip_log_suffix("") == ""

    def test_wait_all_auto_parses_output_schema(self, monkeypatch):
        """subagent_batch(output_schema=...) → wait_all() returns parsed dicts.

        This is the end-to-end regression test for the Greptile P1 finding:
        BatchJob.wait_all() must apply _parse_result() when output_schema is set,
        matching the auto-parse behaviour of subagent_parallel(output_schema=...).
        """
        import threading
        from pathlib import Path
        from unittest.mock import MagicMock

        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_batch
        from gptme.tools.subagent.types import (
            ReturnType,
            Subagent,
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        class Schema:
            pass

        # Stub out subagent() so no threads are launched
        monkeypatch.setattr(batch_mod, "subagent", lambda agent_id, prompt, **kw: None)

        # Pre-populate the result registry with a valid JSON string
        json_result = '{"x": 42}'
        t = MagicMock(spec=threading.Thread)
        t.is_alive.return_value = False
        t.join = MagicMock()
        sa = Subagent(
            agent_id="s1",
            prompt="test",
            thread=t,
            logdir=Path("/tmp/fake-log"),
            model=None,
        )
        with _subagents_lock:
            _subagents.append(sa)
        with _subagent_results_lock:
            _subagent_results["s1"] = ReturnType("success", json_result)

        # Call subagent_batch with output_schema and wait_all()
        job = subagent_batch([("s1", "do something")], output_schema=Schema)
        assert job.output_schema is Schema, "BatchJob must store output_schema"

        results = job.wait_all(timeout=5)

        assert "s1" in results
        assert results["s1"]["status"] == "success"
        # Auto-parsed: should be a dict, not the raw JSON string
        assert results["s1"]["result"] == {"x": 42}, (
            "wait_all() must auto-parse the result when output_schema is set; "
            f"got {results['s1']['result']!r} instead of {{'x': 42}}"
        )

    def test_wait_all_without_schema_returns_raw_strings(self, monkeypatch):
        """wait_all() returns raw strings when output_schema is not set (no regression)."""
        import threading
        from pathlib import Path
        from unittest.mock import MagicMock

        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_batch
        from gptme.tools.subagent.types import (
            ReturnType,
            Subagent,
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        monkeypatch.setattr(batch_mod, "subagent", lambda agent_id, prompt, **kw: None)

        raw = "plain string result"
        t = MagicMock(spec=threading.Thread)
        t.is_alive.return_value = False
        t.join = MagicMock()
        sa = Subagent(
            agent_id="ns1",
            prompt="test",
            thread=t,
            logdir=Path("/tmp/fake-log"),
            model=None,
        )
        with _subagents_lock:
            _subagents.append(sa)
        with _subagent_results_lock:
            _subagent_results["ns1"] = ReturnType("success", raw)

        job = subagent_batch([("ns1", "do something")])
        assert job.output_schema is None
        results = job.wait_all(timeout=5)

        assert results["ns1"]["result"] == raw

    def test_parse_result_handles_full_log_suffix(self):
        """_parse_result strips the Full log suffix before parsing JSON."""
        from gptme.tools.subagent.batch import _parse_result

        class Schema:
            pass

        d = {
            "status": "success",
            "result": '{"score": 99}\n\nFull log: /tmp/agent-xyz/logs',
        }
        result = _parse_result(d, Schema)
        assert result["result"] == {"score": 99}
        assert "parse_error" not in result

    def test_parse_result_handles_full_log_suffix_with_pydantic(self):
        """_parse_result strips Full log suffix before Pydantic validation."""
        from gptme.tools.subagent.batch import _parse_result

        class FakePydantic:
            @classmethod
            def model_validate(cls, data):
                return cls()

            def model_dump(self):
                return {"validated": True}

        d = {
            "status": "success",
            "result": '{"validated": true}\n\nFull log: /tmp/agent-xyz/logs',
        }
        result = _parse_result(d, FakePydantic)
        assert result["result"] == {"validated": True}
        assert "parse_error" not in result

    def test_workdir_forwarded_to_subagent_batch(self, monkeypatch):
        """workdir is forwarded to each subagent() call from subagent_batch()."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_batch

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            subagent_batch([("w1", "p1")], workdir=tmpdir)
            assert captured[0].get("workdir") == tmpdir

    def test_context_turns_forwarded_to_subagent_batch(self, monkeypatch):
        """context_turns is forwarded to each subagent() call from subagent_batch()."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_batch

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        subagent_batch([("c1", "p1"), ("c2", "p2")], context_turns=5)

        assert len(captured) == 2
        for kw in captured:
            assert kw.get("context_turns") == 5

    def test_context_window_forwarded_to_subagent_batch(self, monkeypatch):
        """context_window is forwarded to each subagent() call from subagent_batch()."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_batch

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        subagent_batch([("w1", "p1"), ("w2", "p2")], context_window=0)

        assert len(captured) == 2
        for kw in captured:
            assert kw.get("context_window") == 0

    def test_context_window_forwarded_to_subagent_parallel(self, monkeypatch):
        """context_window is forwarded to each subagent() call from subagent_parallel()."""
        from unittest.mock import MagicMock

        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import BatchJob, ReturnType, subagent_parallel

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)

        mock_job = MagicMock(spec=BatchJob)
        mock_job.agent_ids = ["pw1", "pw2"]
        mock_job.results = {
            "pw1": ReturnType("success", "done"),
            "pw2": ReturnType("success", "done"),
        }
        mock_job.wait_all.return_value = None

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)
        monkeypatch.setattr(batch_mod, "subagent_cancel", lambda aid: None)
        monkeypatch.setattr(batch_mod, "BatchJob", lambda agent_ids, **kw: mock_job)

        subagent_parallel([("pw1", "p1"), ("pw2", "p2")], context_window=0)

        assert len(captured) == 2
        for kw in captured:
            assert kw.get("context_window") == 0

    def test_context_window_forwarded_to_subagent_pipeline(self, monkeypatch):
        """context_window is forwarded to each subagent() call from subagent_pipeline()."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_pipeline

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)

        def mock_wait(agent_id, **kwargs):
            return {"status": "success", "result": "done"}

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)
        monkeypatch.setattr(batch_mod, "subagent_wait", mock_wait)

        subagent_pipeline(
            [("pp1", "prompt1")],
            lambda item, _: item,
            context_window=0,
        )

        assert len(captured) == 1
        assert captured[0].get("context_window") == 0

    def test_context_window_none_by_default_in_batch(self, monkeypatch):
        """context_window defaults to None in subagent_batch() (full workspace context)."""
        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_batch

        captured: list[dict] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            captured.append(kwargs)

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)

        subagent_batch([("d1", "p1")])

        assert len(captured) == 1
        assert "context_window" in captured[0]
        assert captured[0]["context_window"] is None


# ---------------------------------------------------------------------------
# Token budget tracking tests
# ---------------------------------------------------------------------------


class TestTokenBudgetTracking:
    """Tests for token budget tracking in ReturnType, Subagent._read_token_stats(), and BatchJob."""

    def test_return_type_default_token_fields_are_none(self):
        """ReturnType defaults to None for both token fields."""
        rt = ReturnType("success", "done")
        assert rt.input_tokens is None
        assert rt.output_tokens is None

    def test_return_type_stores_token_counts(self):
        """ReturnType stores input_tokens and output_tokens when provided."""
        rt = ReturnType("success", "done", input_tokens=1000, output_tokens=200)
        assert rt.input_tokens == 1000
        assert rt.output_tokens == 200

    def test_read_token_stats_with_usage_metadata(self, tmp_path):
        """_read_token_stats() reads input/output tokens from conversation.jsonl."""
        import json

        logdir = tmp_path / "test-agent"
        logdir.mkdir()
        conv_file = logdir / "conversation.jsonl"

        # Write a log with two assistant messages that have usage metadata
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {
                "role": "assistant",
                "content": "Hello!",
                "metadata": {"usage": {"input_tokens": 100, "output_tokens": 50}},
            },
            {
                "role": "assistant",
                "content": "Done.",
                "metadata": {"usage": {"input_tokens": 150, "output_tokens": 80}},
            },
        ]
        with open(conv_file, "w") as f:
            f.writelines(json.dumps(msg) + "\n" for msg in messages)

        sa = Subagent(
            agent_id="test-token-read",
            prompt="test",
            thread=None,
            logdir=logdir,
            model=None,
        )
        in_tok, out_tok = sa._read_token_stats()
        assert in_tok == 250  # 100 + 150
        assert out_tok == 130  # 50 + 80

    def test_read_token_stats_with_cache_tokens(self, tmp_path):
        """_read_token_stats() sums cache_read and cache_creation tokens into input."""
        import json

        logdir = tmp_path / "test-agent-cache"
        logdir.mkdir()
        conv_file = logdir / "conversation.jsonl"

        messages = [
            {
                "role": "assistant",
                "content": "Cached response.",
                "metadata": {
                    "usage": {
                        "input_tokens": 100,
                        "cache_read_tokens": 200,
                        "cache_creation_tokens": 50,
                        "output_tokens": 30,
                    }
                },
            },
        ]
        with open(conv_file, "w") as f:
            f.writelines(json.dumps(msg) + "\n" for msg in messages)

        sa = Subagent(
            agent_id="test-token-cache",
            prompt="test",
            thread=None,
            logdir=logdir,
            model=None,
        )
        in_tok, out_tok = sa._read_token_stats()
        assert in_tok == 350  # 100 + 200 (cache_read) + 50 (cache_creation)
        assert out_tok == 30

    def test_read_token_stats_returns_none_when_no_metadata(self, tmp_path):
        """_read_token_stats() returns (None, None) when no usage metadata is present."""
        import json

        logdir = tmp_path / "test-agent-no-meta"
        logdir.mkdir()
        conv_file = logdir / "conversation.jsonl"

        messages = [
            {"role": "system", "content": "System prompt."},
            {"role": "assistant", "content": "Response without metadata."},
        ]
        with open(conv_file, "w") as f:
            f.writelines(json.dumps(msg) + "\n" for msg in messages)

        sa = Subagent(
            agent_id="test-no-meta",
            prompt="test",
            thread=None,
            logdir=logdir,
            model=None,
        )
        in_tok, out_tok = sa._read_token_stats()
        assert in_tok is None
        assert out_tok is None

    def test_read_token_stats_preserves_zero_token_metadata(self, tmp_path):
        """_read_token_stats() returns zeroes when usage metadata is present but zero."""
        import json

        logdir = tmp_path / "test-agent-zero-usage"
        logdir.mkdir()
        conv_file = logdir / "conversation.jsonl"

        messages = [
            {
                "role": "assistant",
                "content": "Zero usage response.",
                "metadata": {"usage": {"input_tokens": 0, "output_tokens": 0}},
            },
        ]
        with open(conv_file, "w") as f:
            f.writelines(json.dumps(msg) + "\n" for msg in messages)

        sa = Subagent(
            agent_id="test-zero-usage",
            prompt="test",
            thread=None,
            logdir=logdir,
            model=None,
        )
        in_tok, out_tok = sa._read_token_stats()
        assert in_tok == 0
        assert out_tok == 0

    def test_read_token_stats_returns_none_when_logdir_missing(self, tmp_path):
        """_read_token_stats() returns (None, None) gracefully when logdir doesn't exist."""
        sa = Subagent(
            agent_id="test-no-log",
            prompt="test",
            thread=None,
            logdir=tmp_path / "nonexistent-agent",
            model=None,
        )
        in_tok, out_tok = sa._read_token_stats()
        assert in_tok is None
        assert out_tok is None

    def test_acp_completion_preserves_token_counts(self, tmp_path, monkeypatch):
        """ACP mode caches token fields read from the subagent conversation log."""
        import gptme.acp.client as acp_client

        cli_main = importlib.import_module("gptme.cli.main")
        logdir = tmp_path / "subagent-acp-token"
        monkeypatch.setattr(cli_main, "get_logdir", lambda name: logdir)

        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()
        while not _completion_queue.empty():
            try:
                _completion_queue.get_nowait()
            except queue.Empty:
                break

        class FakeUpdate:
            type = "agent_message_chunk"
            chunk = {"text": "ACP completed."}

        class FakeRunResult:
            stop_reason = "end_turn"

        class FakeAcpClient:
            def __init__(self, *args, on_update=None, **kwargs):
                self.on_update = on_update

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def run(self, prompt, cwd=None):
                logdir.mkdir(parents=True, exist_ok=True)
                (logdir / "conversation.jsonl").write_text(
                    json.dumps(
                        {
                            "role": "assistant",
                            "content": "ACP completed.",
                            "metadata": {
                                "usage": {
                                    "input_tokens": 321,
                                    "output_tokens": 54,
                                }
                            },
                        }
                    )
                    + "\n"
                )
                if self.on_update:
                    self.on_update("session-1", FakeUpdate())
                return FakeRunResult()

        monkeypatch.setattr(acp_client, "GptmeAcpClient", FakeAcpClient)

        subagent_api.subagent("acp-token", "test prompt", use_acp=True)

        with _subagents_lock:
            sa = next(s for s in _subagents if s.agent_id == "acp-token")
        assert sa.thread is not None
        sa.thread.join(timeout=5)
        assert not sa.thread.is_alive()

        with _subagent_results_lock:
            result = _subagent_results["acp-token"]
        assert result.status == "success"
        assert result.result == "ACP completed."
        assert result.input_tokens == 321
        assert result.output_tokens == 54

    def test_batch_job_total_tokens_sums_all_results(self):
        """BatchJob.total_tokens() sums input/output tokens from all completed results."""
        job = BatchJob(agent_ids=["a", "b", "c"])
        job.results["a"] = ReturnType(
            "success", "done", input_tokens=1000, output_tokens=100
        )
        job.results["b"] = ReturnType(
            "success", "done", input_tokens=2000, output_tokens=200
        )
        job.results["c"] = ReturnType(
            "failure", "error", input_tokens=500, output_tokens=50
        )

        stats = job.total_tokens()
        assert stats["input_tokens"] == 3500
        assert stats["output_tokens"] == 350

    def test_batch_job_total_tokens_returns_none_when_no_data(self):
        """BatchJob.total_tokens() returns None values when no subagent has token data."""
        job = BatchJob(agent_ids=["a", "b"])
        job.results["a"] = ReturnType("success", "done")
        job.results["b"] = ReturnType("failure", "error")

        stats = job.total_tokens()
        assert stats["input_tokens"] is None
        assert stats["output_tokens"] is None

    def test_batch_job_total_tokens_partial_data(self):
        """BatchJob.total_tokens() sums only available data when some subagents lack token info."""
        job = BatchJob(agent_ids=["a", "b"])
        job.results["a"] = ReturnType(
            "success", "done", input_tokens=1000, output_tokens=100
        )
        job.results["b"] = ReturnType("failure", "error")  # No token data

        stats = job.total_tokens()
        assert stats["input_tokens"] == 1000  # Only from 'a'
        assert stats["output_tokens"] == 100

    def test_batch_job_wait_all_preserves_token_counts(self):
        """BatchJob.wait_all() stores token fields returned by subagent_wait()."""
        from unittest.mock import patch

        def fake_wait(agent_id, timeout=60, max_result_chars=2000):
            tokens = {
                "a": {"input_tokens": 1000, "output_tokens": 100},
                "b": {"input_tokens": 2000, "output_tokens": 200},
            }[agent_id]
            return {"status": "success", "result": f"{agent_id} done", **tokens}

        job = BatchJob(agent_ids=["a", "b"])
        with patch("gptme.tools.subagent.batch.subagent_wait", side_effect=fake_wait):
            results = job.wait_all(timeout=5)

        assert results["a"]["input_tokens"] == 1000
        assert results["a"]["output_tokens"] == 100
        stats = job.total_tokens()
        assert stats["input_tokens"] == 3000
        assert stats["output_tokens"] == 300

    def test_batch_job_total_tokens_empty_results(self):
        """BatchJob.total_tokens() returns None when no results are present yet."""
        job = BatchJob(agent_ids=["a", "b"])
        stats = job.total_tokens()
        assert stats["input_tokens"] is None
        assert stats["output_tokens"] is None


class TestSubagentWaitOutputSchema:
    """subagent_wait() applies output_schema parsing automatically (#554).

    subagent_parallel() and subagent_batch().wait_all() already auto-parse
    results against output_schema via _parse_result(). subagent_wait() should
    do the same when the subagent was spawned with an output_schema so callers
    don't need to call model_validate() separately.
    """

    def _make_subagent_with_schema(
        self,
        agent_id: str,
        output_schema: type | None,
        result_json: str,
        tmp_path,
    ):
        """Helper: register a completed subagent that returned result_json."""
        from gptme.tools.subagent.types import (
            ReturnType,
            Subagent,
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        logdir = tmp_path / agent_id
        logdir.mkdir(parents=True)

        sa = Subagent(
            agent_id=agent_id,
            prompt="test prompt",
            thread=None,
            logdir=logdir,
            model=None,
            output_schema=output_schema,
        )
        with _subagents_lock:
            _subagents.append(sa)

        result = ReturnType("success", result_json)
        with _subagent_results_lock:
            _subagent_results[agent_id] = result

        return sa

    def test_wait_parses_json_when_output_schema_set(self, tmp_path):
        """subagent_wait() returns a parsed dict when output_schema is set."""
        from pydantic import BaseModel

        from gptme.tools.subagent.api import subagent_wait
        from gptme.tools.subagent.types import (
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        class MySchema(BaseModel):
            value: int
            label: str

        agent_id = "wait-schema-test"
        try:
            self._make_subagent_with_schema(
                agent_id, MySchema, '{"value": 42, "label": "hello"}', tmp_path
            )
            result = subagent_wait(agent_id, timeout=1)
            assert result["status"] == "success"
            # _parse_result calls model_validate(...).model_dump(), so result is a dict
            parsed = result["result"]
            assert isinstance(parsed, dict)
            assert parsed["value"] == 42
            assert parsed["label"] == "hello"
        finally:
            with _subagents_lock:
                _subagents[:] = [s for s in _subagents if s.agent_id != agent_id]
            with _subagent_results_lock:
                _subagent_results.pop(agent_id, None)

    def test_wait_returns_raw_result_when_no_schema(self, tmp_path):
        """subagent_wait() returns raw result string when no output_schema."""
        from gptme.tools.subagent.api import subagent_wait
        from gptme.tools.subagent.types import (
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        raw_json = '{"value": 42, "label": "hello"}'
        agent_id = "wait-no-schema-test"
        try:
            self._make_subagent_with_schema(agent_id, None, raw_json, tmp_path)
            result = subagent_wait(agent_id, timeout=1)
            assert result["status"] == "success"
            # Without schema, result is the raw string
            assert result["result"] == raw_json
        finally:
            with _subagents_lock:
                _subagents[:] = [s for s in _subagents if s.agent_id != agent_id]
            with _subagent_results_lock:
                _subagent_results.pop(agent_id, None)

    def test_wait_adds_parse_error_on_invalid_json(self, tmp_path):
        """subagent_wait() adds parse_error key if result isn't valid JSON."""
        from pydantic import BaseModel

        from gptme.tools.subagent.api import subagent_wait
        from gptme.tools.subagent.types import (
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        class MySchema(BaseModel):
            value: int

        agent_id = "wait-bad-json-test"
        try:
            self._make_subagent_with_schema(
                agent_id, MySchema, "not valid json at all", tmp_path
            )
            result = subagent_wait(agent_id, timeout=1)
            assert result["status"] == "success"
            # parse_error is added; result stays as-is
            assert "parse_error" in result
            assert result["result"] == "not valid json at all"
        finally:
            with _subagents_lock:
                _subagents[:] = [s for s in _subagents if s.agent_id != agent_id]
            with _subagent_results_lock:
                _subagent_results.pop(agent_id, None)

    def test_wait_skips_parsing_on_failure_status(self, tmp_path):
        """subagent_wait() does not attempt schema parsing when status != success."""
        from pydantic import BaseModel

        from gptme.tools.subagent.api import subagent_wait
        from gptme.tools.subagent.types import (
            ReturnType,
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        class MySchema(BaseModel):
            value: int

        agent_id = "wait-failure-schema-test"
        try:
            self._make_subagent_with_schema(agent_id, MySchema, "irrelevant", tmp_path)
            # Override the result to be a failure status
            with _subagent_results_lock:
                _subagent_results[agent_id] = ReturnType("failure", "error occurred")

            result = subagent_wait(agent_id, timeout=1)
            assert result["status"] == "failure"
            # No parse_error should be added for non-success results
            assert "parse_error" not in result
        finally:
            with _subagents_lock:
                _subagents[:] = [s for s in _subagents if s.agent_id != agent_id]
            with _subagent_results_lock:
                _subagent_results.pop(agent_id, None)


# ---------------------------------------------------------------------------
# Thread tool isolation regression tests (#3102)
# ---------------------------------------------------------------------------


class TestThreadToolIsolation:
    """Regression tests for the thread-mode tool list isolation fix (PR #3102).

    In Python ≤ 3.11, threading.Thread copies the parent's ContextVar mapping
    into the child thread, so _loaded_tools_var initially points to the *same
    list object* as the parent.  Without clear_tools() at thread start, any
    append inside init_tools() or _ensure_subagent_signal_tools_loaded() would
    mutate the parent's list, creating a data race with the parent's concurrent
    execute_msg() calls — which could make any tool transiently non-runnable
    (is_runnable → False) during the subagent's setup window.

    The tests use copy_context().run() to reproduce the Python ≤ 3.11 behavior
    regardless of the current Python version (in 3.12+ threads start with a
    fresh context and don't exhibit the race natively).
    """

    def test_mutation_visible_without_clear_tools(self):
        """Without clear_tools(), a child running in a copied context mutates the parent's list.

        This test documents the bug: it passes precisely BECAUSE the mutation
        happens, proving that copy_context().run() correctly simulates the race.
        If this test ever starts failing, the test infrastructure is broken.
        """
        import contextvars

        from gptme.tools import _get_loaded_tools, _loaded_tools_var
        from gptme.tools.base import ToolSpec

        sentinel = MagicMock(spec=ToolSpec)
        parent_list: list[ToolSpec] = [sentinel]
        _loaded_tools_var.set(parent_list)

        injected = MagicMock(spec=ToolSpec)

        def child_without_fix():
            # No clear_tools() — child operates directly on parent's shared list
            child_list = _get_loaded_tools()
            child_list.append(injected)

        ctx = contextvars.copy_context()
        t = threading.Thread(target=lambda: ctx.run(child_without_fix))
        t.start()
        t.join()

        current = _loaded_tools_var.get()
        assert current is parent_list
        assert injected in current, (
            "Without clear_tools(), child mutation must be visible to parent"
        )

    def test_clear_tools_prevents_parent_mutation(self):
        """clear_tools() at thread start must isolate the child's list from the parent.

        This is the regression test for the fix in PR #3102: calling clear_tools()
        as the first operation in _create_subagent_thread replaces the inherited
        list reference with a fresh empty list, so subsequent appends (from
        init_tools / _ensure_subagent_signal_tools_loaded) cannot affect the
        parent thread.
        """
        import contextvars

        from gptme.tools import _get_loaded_tools, _loaded_tools_var, clear_tools
        from gptme.tools.base import ToolSpec

        sentinel = MagicMock(spec=ToolSpec)
        parent_list: list[ToolSpec] = [sentinel]
        _loaded_tools_var.set(parent_list)

        child_results: dict = {}

        def child_with_fix():
            # Verify child inherits parent's list before the fix runs
            inherited = _loaded_tools_var.get()
            child_results["inherited_same_list"] = inherited is parent_list

            # The fix: detach from parent's list before doing any tool work
            clear_tools()

            # After clear_tools() the child has its own fresh empty list
            child_list = _get_loaded_tools()
            child_results["after_clear_same"] = child_list is parent_list
            child_results["after_clear_empty"] = len(child_list) == 0

            # Simulate what init_tools() does: append tools to the child's list
            child_list.append(MagicMock(spec=ToolSpec))

        ctx = contextvars.copy_context()
        t = threading.Thread(target=lambda: ctx.run(child_with_fix))
        t.start()
        t.join()

        # Sanity: confirm copy_context gave child the parent's list initially
        assert child_results.get("inherited_same_list") is True, (
            "copy_context() must give child the parent's list — test infrastructure broken"
        )
        # After clear_tools(), child should have its own list
        assert child_results.get("after_clear_same") is False, (
            "clear_tools() must break the shared-list binding"
        )
        assert child_results.get("after_clear_empty") is True, (
            "clear_tools() must give child an empty list"
        )

        # The key assertion: parent's list is unchanged despite child appending to its own
        current_parent = _loaded_tools_var.get()
        assert current_parent is parent_list, (
            "Parent's ContextVar binding must not change"
        )
        assert len(current_parent) == 1 and current_parent[0] is sentinel, (
            "Parent's list content must be unchanged after child thread exits"
        )

    def test_create_subagent_thread_isolates_tool_list(self, monkeypatch, tmp_path):
        """_create_subagent_thread must isolate its tool list from the parent thread.

        This is the actual regression guard for the fix at execution.py:188.
        The two tests above verify that clear_tools() works in isolation, but
        neither calls _create_subagent_thread — so removing clear_tools() from
        execution.py:188 would leave both passing.  This test exercises the real
        fix site: the child runs in a *copied* context (simulating Python ≤ 3.11
        thread semantics), and _ensure_subagent_signal_tools_loaded is replaced
        with a mock that appends to the current tool list.  Without clear_tools(),
        that append would mutate the parent's list; with it, the child gets an
        isolated list and the parent's sentinel is untouched.
        """
        import contextvars
        import importlib

        from gptme.message import Message
        from gptme.tools import _loaded_tools_var
        from gptme.tools.base import ToolSpec

        gptme_chat = importlib.import_module("gptme.chat")
        gptme_executor = importlib.import_module("gptme.executor")
        gptme_llm_models = importlib.import_module("gptme.llm.models")
        gptme_profiles = importlib.import_module("gptme.profiles")
        gptme_prompts = importlib.import_module("gptme.prompts")
        hooks_mod = importlib.import_module("gptme.tools.subagent.hooks")
        exec_mod = importlib.import_module("gptme.tools.subagent.execution")

        # Establish parent's tool list with a sentinel
        sentinel = MagicMock(spec=ToolSpec)
        parent_list: list[ToolSpec] = [sentinel]
        token = _loaded_tools_var.set(parent_list)

        try:
            # This mock replaces _ensure_subagent_signal_tools_loaded.  It appends
            # to the *current context's* tool list, exactly like the real function.
            # If clear_tools() is absent, the current list IS parent_list and this
            # append mutates it.  If clear_tools() ran first, the current list is a
            # fresh empty list so parent_list stays clean.
            injected = MagicMock(spec=ToolSpec)

            def mock_ensure_tools():
                from gptme.tools import _get_loaded_tools

                _get_loaded_tools().append(injected)

            monkeypatch.setattr(gptme_chat, "chat", lambda *args, **kwargs: None)
            monkeypatch.setattr(
                gptme_executor, "prepare_execution_environment", lambda **kwargs: None
            )
            monkeypatch.setattr(
                gptme_llm_models, "set_default_model", lambda *args: None
            )
            monkeypatch.setattr(gptme_profiles, "get_profile", lambda _: None)
            monkeypatch.setattr(
                gptme_prompts,
                "get_prompt",
                lambda *args, **kwargs: [Message("system", "# Agent\ntest")],
            )
            monkeypatch.setattr(
                hooks_mod,
                "_get_complete_instruction",
                lambda *args, **kwargs: "done",
            )
            monkeypatch.setattr(
                exec_mod, "_ensure_subagent_signal_tools_loaded", mock_ensure_tools
            )
            monkeypatch.setattr(exec_mod, "get_tools", lambda: [])

            thread_exc: list[BaseException] = []

            def thread_target():
                try:
                    ctx.run(
                        exec_mod._create_subagent_thread,
                        prompt="test",
                        logdir=tmp_path / "logdir",
                        model=None,
                        context_mode="full",
                        context_include=None,
                        workspace=tmp_path,
                        redact_secrets=False,
                    )
                except Exception as e:
                    thread_exc.append(e)

            ctx = contextvars.copy_context()
            t = threading.Thread(target=thread_target)
            t.start()
            t.join()

            if thread_exc:
                raise thread_exc[0]

            # clear_tools() at execution.py:188 must have routed mock_ensure_tools()'s
            # append to the child's isolated list, not to parent_list.
            current_parent = _loaded_tools_var.get()
            assert current_parent is parent_list, (
                "Parent's ContextVar binding must not change"
            )
            assert injected not in current_parent, (
                "_create_subagent_thread's tool-list append must not reach the "
                "parent list — clear_tools() at execution.py:188 is likely missing"
            )
            assert sentinel in current_parent, "Parent's original tool must be present"
        finally:
            _loaded_tools_var.reset(token)


# ---------------------------------------------------------------------------
# subagent_pipeline tests
# ---------------------------------------------------------------------------


class TestSubagentPipeline:
    """Tests for subagent_pipeline() staged fan-out helper."""

    def _make_mocks(self, monkeypatch, results_by_agent: dict[str, dict] | None = None):
        """Patch batch_mod.subagent and batch_mod.subagent_wait.

        Returns (captured_subagent_calls, captured_wait_calls) lists.
        """
        import gptme.tools.subagent.batch as batch_mod

        captured_subagent: list[dict] = []
        captured_wait: list[dict] = []
        _results = results_by_agent or {}

        def mock_subagent(agent_id, prompt, **kwargs):
            captured_subagent.append({"agent_id": agent_id, "prompt": prompt, **kwargs})

        def mock_wait(agent_id, timeout=300, **kwargs):
            captured_wait.append({"agent_id": agent_id})
            return _results.get(
                agent_id, {"status": "success", "result": f"result-{agent_id}"}
            )

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)
        monkeypatch.setattr(batch_mod, "subagent_wait", mock_wait)
        return captured_subagent, captured_wait

    def test_empty_items_returns_empty_list(self, monkeypatch):
        from gptme.tools.subagent.batch import subagent_pipeline

        self._make_mocks(monkeypatch)
        assert subagent_pipeline([], lambda i, _: i) == []

    def test_no_stages_raises_value_error(self, monkeypatch):
        from gptme.tools.subagent.batch import subagent_pipeline

        self._make_mocks(monkeypatch)
        with pytest.raises(ValueError, match="at least one stage"):
            subagent_pipeline([("a", "do work")])

    def test_single_item_single_stage_result(self, monkeypatch):
        """Single item, single stage: result appears at results[0][0]."""
        from gptme.tools.subagent.batch import subagent_pipeline

        calls, _ = self._make_mocks(
            monkeypatch, {"a-s0": {"status": "success", "result": "done"}}
        )
        results = subagent_pipeline(
            [("a", "item-prompt")], lambda item, _: f"stage0:{item}", timeout=5
        )

        assert len(results) == 1
        assert len(results[0]) == 1
        assert results[0][0]["status"] == "success"
        assert results[0][0]["result"] == "done"
        # Stage fn should have been called with (item_prompt, "")
        assert calls[0]["prompt"] == "stage0:item-prompt"

    def test_stage_fn_receives_prev_result(self, monkeypatch):
        """Stage 1 stage function receives the result text from stage 0."""
        from gptme.tools.subagent.batch import subagent_pipeline

        stage_inputs: list[tuple[str, str, str]] = []

        def stage0(item, prev):
            stage_inputs.append(("s0", item, prev))
            return f"s0:{item}"

        def stage1(item, prev):
            stage_inputs.append(("s1", item, prev))
            return f"s1:{item}:{prev}"

        self._make_mocks(
            monkeypatch,
            {
                "x-s0": {"status": "success", "result": "stage0-output"},
                "x-s1": {"status": "success", "result": "stage1-output"},
            },
        )

        results = subagent_pipeline([("x", "item-x")], stage0, stage1, timeout=5)

        assert len(results) == 1
        assert len(results[0]) == 2
        # stage0 receives empty prev
        assert stage_inputs[0] == ("s0", "item-x", "")
        # stage1 receives the result text from stage0
        assert stage_inputs[1] == ("s1", "item-x", "stage0-output")

    def test_stage_failure_marks_remaining_stages_skipped(self, monkeypatch):
        """When a stage returns a non-success status, remaining stages are skipped."""
        from gptme.tools.subagent.batch import subagent_pipeline

        self._make_mocks(
            monkeypatch,
            {"fail-s0": {"status": "failure", "result": "something broke"}},
        )
        results = subagent_pipeline(
            [("fail", "do work")],
            lambda i, _: i,
            lambda i, p: f"verify:{p}",
            timeout=5,
        )

        assert results[0][0]["status"] == "failure"
        assert results[0][1]["status"] == "skipped"
        assert "skipped" in results[0][1]["result"].lower()

    def test_multiple_items_run_concurrently(self, monkeypatch):
        """Multiple items are launched in parallel threads."""
        import threading

        import gptme.tools.subagent.batch as batch_mod
        from gptme.tools.subagent.batch import subagent_pipeline

        items = [("a", "p-a"), ("b", "p-b"), ("c", "p-c")]
        lock = threading.Lock()
        barrier = threading.Barrier(len(items), timeout=1)
        active = 0
        max_concurrent = 0

        def mock_subagent(agent_id, prompt, **kwargs):
            nonlocal active, max_concurrent
            with lock:
                active += 1
                if active > max_concurrent:
                    max_concurrent = active
            try:
                barrier.wait()
            finally:
                with lock:
                    active -= 1

        def mock_wait(agent_id, **kwargs):
            return {"status": "success", "result": f"result-{agent_id}"}

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)
        monkeypatch.setattr(batch_mod, "subagent_wait", mock_wait)

        results = subagent_pipeline(items, lambda i, _: i, timeout=5)

        assert len(results) == 3
        assert all(r[0]["status"] == "success" for r in results)
        # With 3 items all running concurrently, max concurrent should be > 1
        assert max_concurrent > 1, "Items should run concurrently, not sequentially"

    def test_kwargs_forwarded_to_subagent(self, monkeypatch):
        """model, isolated, and other kwargs are forwarded to every subagent call."""
        from gptme.tools.subagent.batch import subagent_pipeline

        calls, _ = self._make_mocks(monkeypatch)
        subagent_pipeline(
            [("a", "p-a"), ("b", "p-b")],
            lambda i, _: i,
            model="openai/gpt-4o-mini",
            isolated=True,
            timeout=5,
        )

        assert len(calls) == 2
        for call in calls:
            assert call.get("model") == "openai/gpt-4o-mini"
            assert call.get("isolated") is True

    def test_output_schema_only_passed_to_final_stage(self, monkeypatch):
        """output_schema should only be forwarded to the final stage subagent."""
        from gptme.tools.subagent.batch import subagent_pipeline

        calls, _ = self._make_mocks(monkeypatch)

        class _Schema:
            @classmethod
            def model_json_schema(cls):
                return {"type": "object", "properties": {"x": {"type": "integer"}}}

        subagent_pipeline(
            [("a", "p-a")],
            lambda i, _: f"s0:{i}",
            lambda i, p: f"s1:{p}",
            output_schema=_Schema,
            timeout=5,
        )

        assert len(calls) == 2
        # stage 0 (index 0) should NOT have output_schema
        assert calls[0].get("output_schema") is None
        # stage 1 (index 1, final) SHOULD have output_schema
        assert calls[1].get("output_schema") is _Schema

    def test_results_indexed_by_item_and_stage(self, monkeypatch):
        """results[item_idx][stage_idx] indexing is correct for multi-item pipelines."""
        from gptme.tools.subagent.batch import subagent_pipeline

        self._make_mocks(
            monkeypatch,
            {
                "a-s0": {"status": "success", "result": "a-stage0"},
                "b-s0": {"status": "success", "result": "b-stage0"},
            },
        )
        results = subagent_pipeline(
            [("a", "prompt-a"), ("b", "prompt-b")],
            lambda i, _: i,
            timeout=5,
        )

        assert len(results) == 2  # 2 items
        assert len(results[0]) == 1  # 1 stage each
        assert results[0][0]["result"] == "a-stage0"
        assert results[1][0]["result"] == "b-stage0"


# ---------------------------------------------------------------------------
# cancel_on_failure tests
# ---------------------------------------------------------------------------


class TestBatchJobCancelOnFailure:
    """Tests for BatchJob.wait_all(cancel_on_failure=True)."""

    def test_cancel_on_failure_cancels_remaining_when_one_fails(self, monkeypatch):
        """When cancel_on_failure=True, a failing agent causes remaining to be cancelled."""
        import threading

        from gptme.tools.subagent import batch as batch_mod
        from gptme.tools.subagent.batch import BatchJob

        slow_agent_unblocked = threading.Event()
        cancel_calls: list[str] = []

        def mock_subagent_wait(agent_id, timeout=None, max_result_chars=None, **kwargs):
            if agent_id == "failing":
                return {"status": "failure", "result": "task failed"}
            # slow agent blocks until cancelled
            slow_agent_unblocked.wait(timeout=5)
            return {"status": "failure", "result": "Cancelled due to sibling failure"}

        def mock_subagent_cancel(agent_id):
            cancel_calls.append(agent_id)
            slow_agent_unblocked.set()
            return f"Subagent '{agent_id}' cancelled."

        monkeypatch.setattr(batch_mod, "subagent_wait", mock_subagent_wait)
        monkeypatch.setattr(batch_mod, "subagent_cancel", mock_subagent_cancel)

        job = BatchJob(agent_ids=["failing", "slow"])
        results = job.wait_all(timeout=5, cancel_on_failure=True)

        assert results["failing"]["status"] == "failure"
        assert "slow" in results
        # "cancelled" when _wait_one exits early via cancel_event check;
        # "failure" when _wait_one was already inside subagent_wait and the mock
        # returned after the cancel signal unblocked it.  Both are valid outcomes.
        assert results["slow"]["status"] in ("failure", "cancelled")
        assert "slow" in cancel_calls

    def test_cancel_on_failure_false_does_not_cancel_remaining(self, monkeypatch):
        """Without cancel_on_failure=True, a failure does not cancel other agents."""

        from gptme.tools.subagent import batch as batch_mod
        from gptme.tools.subagent.batch import BatchJob

        cancel_calls: list[str] = []

        def mock_subagent_wait(agent_id, timeout=None, max_result_chars=None, **kwargs):
            if agent_id == "failing":
                return {"status": "failure", "result": "task failed"}
            return {"status": "success", "result": "slow done"}

        def mock_subagent_cancel(agent_id):
            cancel_calls.append(agent_id)
            return f"Subagent '{agent_id}' cancelled."

        monkeypatch.setattr(batch_mod, "subagent_wait", mock_subagent_wait)
        monkeypatch.setattr(batch_mod, "subagent_cancel", mock_subagent_cancel)

        job = BatchJob(agent_ids=["failing", "slow"])
        results = job.wait_all(timeout=5, cancel_on_failure=False)

        assert results["failing"]["status"] == "failure"
        assert results["slow"]["status"] == "success"
        assert cancel_calls == []

    def test_cancel_on_failure_no_cancel_when_all_succeed(self, monkeypatch):
        """When all agents succeed, no cancellation happens."""
        from gptme.tools.subagent import batch as batch_mod
        from gptme.tools.subagent.batch import BatchJob

        cancel_calls: list[str] = []

        def mock_subagent_wait(agent_id, timeout=None, max_result_chars=None, **kwargs):
            return {"status": "success", "result": f"{agent_id} done"}

        def mock_subagent_cancel(agent_id):
            cancel_calls.append(agent_id)
            return f"cancelled {agent_id}"

        monkeypatch.setattr(batch_mod, "subagent_wait", mock_subagent_wait)
        monkeypatch.setattr(batch_mod, "subagent_cancel", mock_subagent_cancel)

        job = BatchJob(agent_ids=["a", "b", "c"])
        results = job.wait_all(timeout=5, cancel_on_failure=True)

        assert all(r["status"] == "success" for r in results.values())
        assert cancel_calls == []

    def test_cancel_on_failure_multiple_remaining_agents_all_cancelled(
        self, monkeypatch
    ):
        """When one agent fails, ALL remaining agents are cancelled."""
        import threading

        from gptme.tools.subagent import batch as batch_mod
        from gptme.tools.subagent.batch import BatchJob

        slow_unblocked = threading.Event()
        cancel_calls: list[str] = []

        def mock_subagent_wait(agent_id, timeout=None, max_result_chars=None, **kwargs):
            if agent_id == "fail":
                return {"status": "failure", "result": "fail"}
            slow_unblocked.wait(timeout=5)
            return {"status": "failure", "result": "Cancelled due to sibling failure"}

        def mock_subagent_cancel(agent_id):
            cancel_calls.append(agent_id)
            slow_unblocked.set()
            return f"cancelled {agent_id}"

        monkeypatch.setattr(batch_mod, "subagent_wait", mock_subagent_wait)
        monkeypatch.setattr(batch_mod, "subagent_cancel", mock_subagent_cancel)

        job = BatchJob(agent_ids=["fail", "slow-1", "slow-2"])
        results = job.wait_all(timeout=5, cancel_on_failure=True)

        assert results["fail"]["status"] == "failure"
        # Both remaining agents should be cancelled
        assert len(results) == 3
        assert all(
            results[aid]["status"] in ("failure", "cancelled")
            for aid in ["slow-1", "slow-2"]
        )
        # Both should have been cancelled (cancel_calls may include one or both,
        # depending on which one was still pending when cancel fired)
        assert any(aid in cancel_calls for aid in ["slow-1", "slow-2"])

    def test_cancel_on_failure_with_timeout_result_also_triggers_cancel(
        self, monkeypatch
    ):
        """A timeout result (not just failure) also triggers cancel_on_failure."""
        import threading

        from gptme.tools.subagent import batch as batch_mod
        from gptme.tools.subagent.batch import BatchJob

        slow_unblocked = threading.Event()
        cancel_calls: list[str] = []

        def mock_subagent_wait(agent_id, timeout=None, max_result_chars=None, **kwargs):
            if agent_id == "timed-out":
                return {"status": "timeout", "result": "timed out"}
            slow_unblocked.wait(timeout=5)
            return {"status": "failure", "result": "Cancelled due to sibling failure"}

        def mock_subagent_cancel(agent_id):
            cancel_calls.append(agent_id)
            slow_unblocked.set()
            return f"cancelled {agent_id}"

        monkeypatch.setattr(batch_mod, "subagent_wait", mock_subagent_wait)
        monkeypatch.setattr(batch_mod, "subagent_cancel", mock_subagent_cancel)

        job = BatchJob(agent_ids=["timed-out", "remaining"])
        results = job.wait_all(timeout=5, cancel_on_failure=True)

        assert results["timed-out"]["status"] == "timeout"
        assert "remaining" in results
        assert "remaining" in cancel_calls

    def test_cancel_on_failure_cancels_on_overall_timeout(self, monkeypatch):
        """When the overall as_completed timeout fires, cancel_on_failure cancels agents."""
        import threading
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        from gptme.tools.subagent import batch as batch_mod
        from gptme.tools.subagent.batch import BatchJob

        cancel_calls: list[str] = []
        # Block all agents so the overall timeout fires
        unblocked = threading.Event()

        def mock_subagent_wait(agent_id, timeout=None, max_result_chars=None, **kwargs):
            unblocked.wait(timeout=2)
            return {"status": "success", "result": "done"}

        def mock_as_completed(fs, timeout=None):
            raise FuturesTimeoutError

        def mock_subagent_cancel(agent_id):
            cancel_calls.append(agent_id)

        monkeypatch.setattr(batch_mod, "subagent_wait", mock_subagent_wait)
        monkeypatch.setattr(batch_mod, "as_completed", mock_as_completed)
        monkeypatch.setattr(batch_mod, "subagent_cancel", mock_subagent_cancel)

        job = BatchJob(agent_ids=["agent-a", "agent-b"])
        results = job.wait_all(timeout=1, cancel_on_failure=True)

        # Both agents should be marked as timed out
        assert results["agent-a"]["status"] == "timeout"
        assert results["agent-b"]["status"] == "timeout"
        # And both should have been cancelled
        assert sorted(cancel_calls) == ["agent-a", "agent-b"]
        unblocked.set()  # unblock background threads

    def test_overall_timeout_always_cancels_agents(self, monkeypatch):
        """Overall timeout always cancels agents regardless of cancel_on_failure.

        Subprocess/ACP agents must not keep running after the caller has already
        received timed-out results — cancel_on_failure only controls early
        cancellation on sibling failure, not cleanup at the overall deadline.
        """
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        from gptme.tools.subagent import batch as batch_mod
        from gptme.tools.subagent.batch import BatchJob

        cancel_calls: list[str] = []

        def mock_subagent_wait(agent_id, timeout=None, max_result_chars=None, **kwargs):
            return {"status": "success", "result": "done"}

        def mock_as_completed(fs, timeout=None):
            raise FuturesTimeoutError

        def mock_subagent_cancel(agent_id):
            cancel_calls.append(agent_id)

        monkeypatch.setattr(batch_mod, "subagent_wait", mock_subagent_wait)
        monkeypatch.setattr(batch_mod, "as_completed", mock_as_completed)
        monkeypatch.setattr(batch_mod, "subagent_cancel", mock_subagent_cancel)

        job = BatchJob(agent_ids=["agent-a", "agent-b"])
        results = job.wait_all(timeout=1, cancel_on_failure=False)

        assert results["agent-a"]["status"] == "timeout"
        assert results["agent-b"]["status"] == "timeout"
        # Overall timeout always cancels — regardless of cancel_on_failure flag
        assert sorted(cancel_calls) == ["agent-a", "agent-b"]

    def test_cancel_on_failure_budget_accounts_for_completed_sibling(self, monkeypatch):
        """Budget tokens from an agent that completed concurrently with the cancel loop
        must be recorded even when the cancel loop wrote a synthetic 'cancelled'
        placeholder before the future's real result was available.

        Regression test for the race described in the Greptile review of PR #3198:
        without the post-cancel sweep + overwrite condition fix, the cancel loop's
        synthetic placeholder prevents the budget from seeing the real tokens.
        """
        from gptme.tools.subagent import batch as batch_mod
        from gptme.tools.subagent.batch import BatchJob
        from gptme.tools.subagent.types import SubagentBudget

        def mock_subagent_wait(agent_id, timeout=None, max_result_chars=None, **kwargs):
            if agent_id == "fail":
                return {
                    "status": "failure",
                    "result": "task failed",
                    "output_tokens": 10,
                }
            return {
                "status": "success",
                "result": "done",
                "output_tokens": 500,
                "input_tokens": 100,
            }

        def mock_subagent_cancel(agent_id):
            pass  # no-op — let _wait_one return naturally

        monkeypatch.setattr(batch_mod, "subagent_wait", mock_subagent_wait)
        monkeypatch.setattr(batch_mod, "subagent_cancel", mock_subagent_cancel)

        budget = SubagentBudget(total=10_000)
        job = BatchJob(agent_ids=["fail", "slow"], budget=budget)
        results = job.wait_all(timeout=5, cancel_on_failure=True)

        assert "fail" in results
        assert "slow" in results

        # Budget must record at least the "fail" agent's tokens.
        assert budget.spent() >= 10, (
            f"Expected at least 10 tokens in budget, got {budget.spent()}"
        )
        # If "slow" returned a real result (not just a synthetic placeholder),
        # its 500 tokens must also be counted.
        if results["slow"].get("output_tokens") == 500:
            assert budget.spent() == 510, (
                f"slow completed with real tokens; budget should be 510, got {budget.spent()}"
            )


class TestSubagentParallelCancelOnFailure:
    """Tests for subagent_parallel(cancel_on_failure=True)."""

    def test_parallel_cancel_on_failure_cancels_remaining_on_failure(self, monkeypatch):
        """subagent_parallel with cancel_on_failure=True cancels fleet on first failure."""
        import threading

        from gptme.tools.subagent import batch as batch_mod
        from gptme.tools.subagent.batch import subagent_parallel

        slow_unblocked = threading.Event()
        cancel_calls: list[str] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            pass  # fire-and-forget

        def mock_subagent_wait(agent_id, timeout=None, max_result_chars=None, **kwargs):
            if agent_id == "fail-agent":
                return {"status": "failure", "result": "something went wrong"}
            slow_unblocked.wait(timeout=5)
            return {"status": "failure", "result": "Cancelled due to sibling failure"}

        def mock_subagent_cancel(agent_id):
            cancel_calls.append(agent_id)
            slow_unblocked.set()
            return f"cancelled {agent_id}"

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)
        monkeypatch.setattr(batch_mod, "subagent_wait", mock_subagent_wait)
        monkeypatch.setattr(batch_mod, "subagent_cancel", mock_subagent_cancel)

        results = subagent_parallel(
            [("fail-agent", "do something"), ("slow-agent", "do something slow")],
            timeout=5,
            cancel_on_failure=True,
        )

        assert len(results) == 2
        assert results[0]["status"] == "failure"  # fail-agent
        # "cancelled" when _wait_one exits early via cancel_event check;
        # "failure" when subagent_wait returned after the cancel signal.
        assert results[1]["status"] in (
            "failure",
            "cancelled",
        )  # slow-agent (cancelled)
        assert "slow-agent" in cancel_calls

    def test_parallel_cancel_on_failure_false_collects_all_results(self, monkeypatch):
        """Without cancel_on_failure, subagent_parallel waits for all agents."""
        from gptme.tools.subagent import batch as batch_mod
        from gptme.tools.subagent.batch import subagent_parallel

        cancel_calls: list[str] = []

        def mock_subagent(agent_id, prompt, **kwargs):
            pass

        def mock_subagent_wait(agent_id, timeout=None, max_result_chars=None, **kwargs):
            if agent_id == "fail-agent":
                return {"status": "failure", "result": "task failed"}
            return {"status": "success", "result": f"{agent_id} done"}

        def mock_subagent_cancel(agent_id):
            cancel_calls.append(agent_id)
            return f"cancelled {agent_id}"

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)
        monkeypatch.setattr(batch_mod, "subagent_wait", mock_subagent_wait)
        monkeypatch.setattr(batch_mod, "subagent_cancel", mock_subagent_cancel)

        results = subagent_parallel(
            [("fail-agent", "do x"), ("success-agent", "do y")],
            timeout=5,
            cancel_on_failure=False,
        )

        assert len(results) == 2
        assert results[0]["status"] == "failure"
        assert results[1]["status"] == "success"
        assert cancel_calls == []

    def test_parallel_cancel_on_failure_results_in_input_order(self, monkeypatch):
        """Results are returned in input task order even when cancel_on_failure fires."""
        import threading

        from gptme.tools.subagent import batch as batch_mod
        from gptme.tools.subagent.batch import subagent_parallel

        slow_unblocked = threading.Event()

        def mock_subagent(agent_id, prompt, **kwargs):
            pass

        def mock_subagent_wait(agent_id, timeout=None, max_result_chars=None, **kwargs):
            if agent_id == "third":
                return {"status": "failure", "result": "third failed"}
            slow_unblocked.wait(timeout=5)
            return {"status": "failure", "result": "cancelled"}

        def mock_subagent_cancel(agent_id):
            slow_unblocked.set()
            return f"cancelled {agent_id}"

        monkeypatch.setattr(batch_mod, "subagent", mock_subagent)
        monkeypatch.setattr(batch_mod, "subagent_wait", mock_subagent_wait)
        monkeypatch.setattr(batch_mod, "subagent_cancel", mock_subagent_cancel)

        tasks = [("first", "p1"), ("second", "p2"), ("third", "p3")]
        results = subagent_parallel(tasks, timeout=5, cancel_on_failure=True)

        # 3 results, in order of input tasks
        assert len(results) == 3
        # "third" failed — it should be the failure that triggered cancellation
        third_idx = 2
        assert results[third_idx]["status"] == "failure"
        assert results[third_idx]["result"] == "third failed"


# ---------------------------------------------------------------------------
# Cooperative cancel checkpoint tests
# ---------------------------------------------------------------------------


class TestWriteCancelOp:
    """Tests for _write_cancel_op helper."""

    def test_writes_jsonl_to_control_file(self, tmp_path):
        _write_cancel_op(tmp_path, "agent-1")
        control = tmp_path / "control.jsonl"
        assert control.exists()
        entry = json.loads(control.read_text().strip())
        assert entry["op"] == "cancel"
        assert entry["agent_id"] == "agent-1"
        assert "ts" in entry

    def test_appends_multiple_ops(self, tmp_path):
        _write_cancel_op(tmp_path, "agent-1")
        _write_cancel_op(tmp_path, "agent-1")
        lines = (tmp_path / "control.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            assert json.loads(line)["op"] == "cancel"

    def test_creates_logdir_if_missing(self, tmp_path):
        logdir = tmp_path / "nested" / "logdir"
        _write_cancel_op(logdir, "agent-1")
        assert (logdir / "control.jsonl").exists()


class TestSubagentCancelThreadWritesControlFile:
    """Verify subagent_cancel() writes to control.jsonl for thread mode."""

    def setup_method(self):
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()

    def test_cancel_thread_writes_control_file(self, tmp_path):
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        sa = Subagent(
            agent_id="thr-agent",
            prompt="test",
            thread=mock_thread,
            logdir=tmp_path,
            model=None,
            execution_mode="thread",
        )
        with _subagents_lock:
            _subagents.append(sa)

        result = subagent_cancel("thr-agent")

        assert "cancelled" in result.lower()
        control = tmp_path / "control.jsonl"
        assert control.exists(), "cancel must write control.jsonl for thread mode"
        # Parse first line of JSONL file
        lines = control.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["op"] == "cancel"
        assert entry["agent_id"] == "thr-agent"

    def test_cancel_subprocess_writes_control_file(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0
        sa = Subagent(
            agent_id="sub-agent",
            prompt="test",
            thread=None,
            logdir=tmp_path,
            model=None,
            process=mock_proc,
            execution_mode="subprocess",
        )
        with _subagents_lock:
            _subagents.append(sa)

        subagent_cancel("sub-agent")

        control = tmp_path / "control.jsonl"
        assert control.exists(), "cancel must write control.jsonl for subprocess mode"
        # Parse first line of JSONL file
        lines = control.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["op"] == "cancel"


class TestCancelCheckpointHook:
    """Unit tests for _subagent_cancel_checkpoint STEP_PRE hook."""

    def setup_method(self):
        with _subagent_results_lock:
            _subagent_results.clear()
        import gptme.tools.subagent.execution as _exe

        _exe._thread_local.__dict__.pop("agent_id", None)

    def _make_manager(self, logdir: Path) -> MagicMock:
        m = MagicMock()
        m.logdir = logdir
        return m

    def _set_agent_id(self, agent_id: str) -> None:
        import gptme.tools.subagent.execution as _exe

        _exe._thread_local.agent_id = agent_id

    def test_noop_outside_subagent_thread(self, tmp_path):
        """Hook must be inert when called in the parent's loop (no thread-local agent_id)."""
        (tmp_path / "control.jsonl").write_text(
            json.dumps({"op": "cancel", "agent_id": "x"}) + "\n"
        )
        msgs = list(_subagent_cancel_checkpoint(self._make_manager(tmp_path)))
        assert msgs == []

    def test_noop_without_control_file(self, tmp_path):
        """Hook must be inert when logdir/control.jsonl doesn't exist."""
        self._set_agent_id("agent-1")
        msgs = list(_subagent_cancel_checkpoint(self._make_manager(tmp_path)))
        assert msgs == []

    def test_noop_without_cancel_op(self, tmp_path):
        """Hook must be inert when control.jsonl has no cancel op."""
        self._set_agent_id("agent-1")
        (tmp_path / "control.jsonl").write_text(
            json.dumps({"op": "steer", "data": "something"}) + "\n"
        )
        msgs = list(_subagent_cancel_checkpoint(self._make_manager(tmp_path)))
        assert msgs == []

    def test_cancels_on_cancel_op(self, tmp_path):
        """Hook yields a note and raises SessionCompleteException on cancel op."""
        from gptme.tools.complete import SessionCompleteException

        self._set_agent_id("agent-1")
        (tmp_path / "control.jsonl").write_text(
            json.dumps({"op": "cancel", "agent_id": "agent-1"}) + "\n"
        )
        gen = _subagent_cancel_checkpoint(self._make_manager(tmp_path))
        # First iteration yields the partial-work note
        msg = next(gen)
        assert "cancel" in msg.content.lower()
        # Second iteration raises SessionCompleteException
        with pytest.raises(SessionCompleteException):
            next(gen)
        # Result cache must have 'cancelled' status
        with _subagent_results_lock:
            assert _subagent_results["agent-1"].status == "cancelled"

    def test_first_writer_wins_on_natural_completion(self, tmp_path):
        """When agent already completed, cancel doesn't overwrite the success result."""
        from gptme.tools.complete import SessionCompleteException

        self._set_agent_id("agent-1")
        # Pre-seed with a success result (agent completed naturally first)
        with _subagent_results_lock:
            _subagent_results["agent-1"] = ReturnType("success", "done")

        (tmp_path / "control.jsonl").write_text(
            json.dumps({"op": "cancel", "agent_id": "agent-1"}) + "\n"
        )
        gen = _subagent_cancel_checkpoint(self._make_manager(tmp_path))
        next(gen)  # yield the note
        with pytest.raises(SessionCompleteException):
            next(gen)
        # Original success result must be preserved
        with _subagent_results_lock:
            assert _subagent_results["agent-1"].status == "success"


# ---------------------------------------------------------------------------
# subagent_steer tests
# ---------------------------------------------------------------------------


class TestSubagentSteer:
    """Tests for subagent_steer() — in-flight orchestrator-to-subagent messaging."""

    _logdir = Path("/tmp/test-steer-log")

    def setup_method(self, tmp_path_factory=None):
        """Clear global subagent registry and steer queue before each test."""
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()

    def _register(
        self,
        agent_id: str,
        logdir: Path | None = None,
        execution_mode: Literal["thread", "subprocess", "acp"] = "thread",
        **kwargs,
    ) -> Subagent:
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        sa = Subagent(
            agent_id=agent_id,
            prompt="test",
            thread=kwargs.get("thread", mock_thread),
            logdir=logdir or self._logdir,
            model=None,
            execution_mode=execution_mode,
            process=kwargs.get("process"),
        )
        with _subagents_lock:
            _subagents.append(sa)
        return sa

    def test_steer_unknown_raises(self):
        """subagent_steer raises ValueError when agent_id is unknown."""

        with pytest.raises(ValueError, match="not found"):
            subagent_steer("nonexistent", "change direction")

    def test_steer_finished_subagent_raises(self):
        """subagent_steer raises ValueError when the subagent has already finished."""

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        self._register("done-agent", thread=mock_thread)

        with pytest.raises(ValueError, match="not running"):
            subagent_steer("done-agent", "too late")

    def test_steer_acp_mode_raises_not_implemented(self):
        """subagent_steer raises NotImplementedError for ACP-mode subagents."""

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        sa = Subagent(
            agent_id="acp-agent",
            prompt="test",
            thread=mock_thread,
            logdir=self._logdir,
            model=None,
            execution_mode="acp",
            use_acp=True,
        )
        with _subagents_lock:
            _subagents.append(sa)

        with pytest.raises(NotImplementedError, match="ACP mode"):
            subagent_steer("acp-agent", "steer me")

    def test_steer_writes_to_prompt_queue(self, tmp_path):
        """subagent_steer queues the message as a steer-flagged record."""
        from gptme.prompt_queue import drain_prompt_queue, drain_steer_prompts

        logdir = tmp_path / "subagent-steer-test"
        logdir.mkdir()
        self._register("running-agent", logdir=logdir)

        result = subagent_steer("running-agent", "Focus on async frameworks only")

        assert "queued" in result.lower()

        # Steer messages are steer-flagged and NOT visible to drain_prompt_queue
        # (which drains between-turn prompts).
        assert drain_prompt_queue(logdir) == []

        # drain_steer_prompts sees them for mid-turn injection.
        drained = drain_steer_prompts(logdir)
        assert len(drained) == 1
        assert "Focus on async frameworks only" in drained[0].content

    def test_steer_multiple_messages_queued_in_order(self, tmp_path):
        """Multiple steer calls append messages in FIFO order."""
        from gptme.prompt_queue import drain_steer_prompts

        logdir = tmp_path / "subagent-steer-fifo"
        logdir.mkdir()
        self._register("running-agent", logdir=logdir)

        subagent_steer("running-agent", "First instruction")
        subagent_steer("running-agent", "Second instruction")
        subagent_steer("running-agent", "Third instruction")

        drained = drain_steer_prompts(logdir)
        assert len(drained) == 3
        assert "First instruction" in drained[0].content
        assert "Second instruction" in drained[1].content
        assert "Third instruction" in drained[2].content

    def test_steer_subprocess_mode_uses_logdir(self, tmp_path):
        """subagent_steer works for subprocess-mode subagents via the same logdir channel."""
        from gptme.prompt_queue import drain_steer_prompts

        logdir = tmp_path / "subagent-subprocess-steer"
        logdir.mkdir()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        sa = Subagent(
            agent_id="proc-agent",
            prompt="test",
            thread=mock_thread,
            logdir=logdir,
            model=None,
            execution_mode="subprocess",
            process=mock_proc,
        )
        with _subagents_lock:
            _subagents.append(sa)

        result = subagent_steer("proc-agent", "Change focus to security issues")

        assert "queued" in result.lower()
        drained = drain_steer_prompts(logdir)
        assert len(drained) == 1
        assert "Change focus to security issues" in drained[0].content

    def test_steer_race_condition_finished_after_check(self, tmp_path):
        """subagent_steer detects when subagent finishes after the initial running check.

        This tests the race condition where:
        1. is_running() returns True
        2. The subagent exits
        3. queue_prompt() writes to a queue no one will drain
        4. We re-check is_running() and catch the race
        """
        logdir = tmp_path / "steer-race-detect"
        logdir.mkdir()
        mock_thread = MagicMock(spec=threading.Thread)
        # side_effect order:
        #   [pre-check, post-check, interrupt_thread, conftest join, conftest leak-check]
        # Five calls total:
        #   1. status() pre-check (inside subagent_steer)
        #   2. status() post-check (inside subagent_steer, after queue write)
        #   3. interrupt_thread() — conftest calls interrupt_thread(sa.thread) which
        #      calls thread.is_alive() internally (retry_abort.py:111)
        #   4. conftest join loop: thread.is_alive() guard before thread.join()
        #   5. conftest leak check: thread.is_alive() after join
        # When side_effect is exhausted it raises StopIteration inside the yield
        # fixture generator, which Python 3.7+ wraps as RuntimeError — the list
        # must cover every call.
        mock_thread.is_alive.side_effect = [True, False, False, False, False]
        sa = Subagent(
            agent_id="race-agent",
            prompt="test",
            thread=mock_thread,
            logdir=logdir,
            model=None,
            execution_mode="thread",
        )
        with _subagents_lock:
            _subagents.append(sa)

        with pytest.raises(
            ValueError, match="exited after steering message was queued"
        ):
            subagent_steer("race-agent", "steer me")

    def test_steer_cleanup_window_raises(self, tmp_path):
        """subagent_steer raises when the subagent is in the cleanup window.

        Thread-mode: the wrapper thread is still alive (is_alive()=True),
        the result is NOT yet cached (_subagent_results is empty), but chat()
        has returned and prompt_queue_closed is set. is_running() and status()
        alone would both falsely report "running" here; prompt_queue_closed
        catches this gap directly.
        """
        logdir = tmp_path / "steer-cleanup-window"
        logdir.mkdir()
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True  # thread alive (cleanup still running)
        sa = Subagent(
            agent_id="cleanup-agent",
            prompt="test",
            thread=mock_thread,
            logdir=logdir,
            model=None,
            execution_mode="thread",
        )
        with _subagents_lock:
            _subagents.append(sa)
        # Simulate run_subagent() having set prompt_queue_closed immediately
        # after _create_subagent_thread() returned, before _read_log() and
        # set_subagent_result_if_absent() are called.
        sa.prompt_queue_closed.set()
        # Result is NOT cached yet — the cleanup is mid-flight.
        assert "cleanup-agent" not in _subagent_results

        with pytest.raises(ValueError, match="closed its prompt queue"):
            subagent_steer("cleanup-agent", "too late")

    def test_steer_subprocess_cleanup_window_raises(self, tmp_path):
        """subagent_steer raises for subprocess-mode when prompt_queue_closed is set.

        Subprocess mode: process.poll() can still return None while the OS process
        is exiting its final teardown. prompt_queue_closed is set by _launch_subprocess()
        in the finally block immediately when the subprocess exits, before result
        caching completes. This test verifies that the flag catches the cleanup window
        for subprocess-mode subagents just as it does for thread-mode.
        """
        logdir = tmp_path / "steer-subprocess-cleanup"
        logdir.mkdir()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # process appears running per poll()
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True  # launcher thread still alive
        sa = Subagent(
            agent_id="proc-cleanup-agent",
            prompt="test",
            thread=mock_thread,
            logdir=logdir,
            model=None,
            execution_mode="subprocess",
            process=mock_proc,
        )
        with _subagents_lock:
            _subagents.append(sa)
        # Simulate _launch_subprocess() finally block: subprocess exited,
        # prompt_queue_closed set before result is cached.
        sa.prompt_queue_closed.set()
        assert "proc-cleanup-agent" not in _subagent_results

        with pytest.raises(ValueError, match="closed its prompt queue"):
            subagent_steer("proc-cleanup-agent", "too late")

    def test_steer_subprocess_sentinel_file_raises(self, tmp_path):
        """subagent_steer raises for subprocess-mode when the sentinel file exists.

        chat.py writes "prompt-queue-closed" to the logdir at the actual drain
        boundary — right before `break` in _run_chat_loop (non-interactive path)
        and right before raising SessionCompleteException — so the window between
        the final _drain_external_prompt_queue() call and the sentinel becoming
        visible is just a few bytecode instructions.  The chat() finally block
        writes it again as a safety net for unexpected exit paths (idempotent).

        The sentinel is a child-side signal: no shared memory needed.
        """
        logdir = tmp_path / "steer-subprocess-sentinel"
        logdir.mkdir()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # process appears running per poll()
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        sa = Subagent(
            agent_id="sentinel-agent",
            prompt="test",
            thread=mock_thread,
            logdir=logdir,
            model=None,
            execution_mode="subprocess",
            process=mock_proc,
            started_at=time.time() - 2.0,  # ensure sentinel written next is "current"
        )
        with _subagents_lock:
            _subagents.append(sa)
        # Simulate chat.py writing the sentinel in its finally block
        # (prompt_queue_closed Python event NOT yet set — that happens later
        # when the parent's launcher finally block runs after process exit).
        (logdir / "prompt-queue-closed").touch()
        assert not sa.prompt_queue_closed.is_set()

        with pytest.raises(ValueError, match="closed its prompt queue"):
            subagent_steer("sentinel-agent", "too late")

    def test_steer_thread_mode_sentinel_file_raises(self, tmp_path):
        """Thread-mode steer raises when the sentinel file exists but prompt_queue_closed is not set.

        This is the race window Greptile identified: chat() writes the sentinel
        at the drain boundary (inside _run_chat_loop's non-interactive break),
        but prompt_queue_closed.set() is called only after chat() returns —
        a gap where steer could falsely succeed without the file check.

        Verifies that _queue_is_closed() checks the sentinel file for thread mode
        too, not just subprocess mode.
        """
        logdir = tmp_path / "steer-thread-sentinel"
        logdir.mkdir()
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True  # still "alive" — in cleanup
        sa = Subagent(
            agent_id="thread-sentinel-agent",
            prompt="test",
            thread=mock_thread,
            logdir=logdir,
            model=None,
            execution_mode="thread",
            started_at=time.time() - 2.0,  # ensure sentinel written next is "current"
        )
        with _subagents_lock:
            _subagents.append(sa)
        # Simulate chat.py writing the sentinel at the drain boundary
        # (prompt_queue_closed NOT yet set — chat() hasn't returned yet).
        (logdir / "prompt-queue-closed").touch()
        assert not sa.prompt_queue_closed.is_set()
        assert sa.execution_mode == "thread"

        with pytest.raises(ValueError, match="closed its prompt queue"):
            subagent_steer("thread-sentinel-agent", "too late")

    def test_steer_result_cached_still_raises(self, tmp_path):
        """subagent_steer raises when result is cached (covers the post-cache half of cleanup).

        Thread alive, result already written to _subagent_results but
        prompt_queue_closed not yet set (extremely narrow gap). status() catches this.
        """
        logdir = tmp_path / "steer-cached-not-closed"
        logdir.mkdir()
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        sa = Subagent(
            agent_id="cached-agent",
            prompt="test",
            thread=mock_thread,
            logdir=logdir,
            model=None,
            execution_mode="thread",
        )
        with _subagents_lock:
            _subagents.append(sa)
        with _subagent_results_lock:
            _subagent_results["cached-agent"] = ReturnType("success", "done")

        with pytest.raises(ValueError, match="not running"):
            subagent_steer("cached-agent", "too late")

    def test_steer_stale_sentinel_from_previous_run_does_not_block(self, tmp_path):
        """A prompt-queue-closed sentinel from a *previous* run must not block steering.

        Planner-mode subagents reuse the same logdir (same agent_id, same
        subagent-{agent_id} directory).  When a run exits it writes
        prompt-queue-closed.  A subsequent run using the same logdir must not
        be incorrectly blocked by that stale sentinel — only a sentinel written
        at or after sa.started_at counts as current.
        """
        logdir = tmp_path / "steer-stale-sentinel"
        logdir.mkdir()

        # Simulate a previous run writing the sentinel (stale).
        sentinel = logdir / "prompt-queue-closed"
        sentinel.touch()
        stale_mtime = sentinel.stat().st_mtime

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True  # current run is active

        # started_at is clearly after the stale sentinel — this is a new run.
        sa = Subagent(
            agent_id="stale-sentinel-agent",
            prompt="test",
            thread=mock_thread,
            logdir=logdir,
            model=None,
            execution_mode="thread",
            started_at=stale_mtime + 1.0,
        )
        with _subagents_lock:
            _subagents.append(sa)
        assert not sa.prompt_queue_closed.is_set()

        # steer must succeed — the sentinel predates this run.
        result = subagent_steer("stale-sentinel-agent", "redirect please")
        assert "stale-sentinel-agent" in result

    def test_steer_current_sentinel_after_reused_logdir_raises(self, tmp_path):
        """A fresh sentinel written by the *current* run still blocks steering.

        Companion to test_steer_stale_sentinel_from_previous_run_does_not_block:
        when the sentinel's mtime is >= sa.started_at it belongs to this run
        and steer must raise.
        """
        logdir = tmp_path / "steer-current-sentinel"
        logdir.mkdir()

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True

        # Record a "started_at" in the past so the sentinel written next is current.
        past_start = time.time() - 2.0
        sa = Subagent(
            agent_id="current-sentinel-agent",
            prompt="test",
            thread=mock_thread,
            logdir=logdir,
            model=None,
            execution_mode="thread",
            started_at=past_start,
        )
        with _subagents_lock:
            _subagents.append(sa)

        # Sentinel written after started_at — belongs to the current run.
        (logdir / "prompt-queue-closed").touch()
        assert not sa.prompt_queue_closed.is_set()

        with pytest.raises(ValueError, match="closed its prompt queue"):
            subagent_steer("current-sentinel-agent", "too late")


# ---------------------------------------------------------------------------
# Mid-turn steer injection via _subagent_control_hook (STEP_PRE)
# ---------------------------------------------------------------------------


class TestMidTurnSteerInjection:
    """Tests for mid-turn steer message injection via _subagent_control_hook."""

    def setup_method(self):
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()

    def _make_manager(self, logdir):
        m = MagicMock()
        m.logdir = logdir
        return m

    def test_steer_flag_marks_record(self, tmp_path):
        """queue_prompt(steer=True) adds steer=true to the JSON record."""
        import json

        from gptme.prompt_queue import QUEUE_FILENAME, queue_prompt

        logdir = tmp_path / "steer-flag-mark"
        logdir.mkdir()
        queue_prompt(logdir, "redirect", steer=True)

        lines = (logdir / QUEUE_FILENAME).read_text().splitlines()
        record = json.loads(lines[0])
        assert record["steer"] is True
        assert record["content"] == "redirect"

    def test_regular_prompt_has_no_steer_flag(self, tmp_path):
        """queue_prompt() without steer= does not write a steer field."""
        import json

        from gptme.prompt_queue import QUEUE_FILENAME, queue_prompt

        logdir = tmp_path / "no-steer-flag"
        logdir.mkdir()
        queue_prompt(logdir, "regular message")

        lines = (logdir / QUEUE_FILENAME).read_text().splitlines()
        record = json.loads(lines[0])
        assert "steer" not in record

    def test_drain_steer_prompts_leaves_regular_messages(self, tmp_path):
        """drain_steer_prompts leaves non-steer records for drain_prompt_queue."""
        from gptme.prompt_queue import (
            drain_prompt_queue,
            drain_steer_prompts,
            queue_prompt,
        )

        logdir = tmp_path / "steer-leaves-regular"
        logdir.mkdir()
        queue_prompt(logdir, "regular turn", steer=False)
        queue_prompt(logdir, "steer guidance", steer=True)

        steer_msgs = drain_steer_prompts(logdir)
        assert len(steer_msgs) == 1
        assert "steer guidance" in steer_msgs[0].content

        regular_msgs = drain_prompt_queue(logdir)
        assert len(regular_msgs) == 1
        assert "regular turn" in regular_msgs[0].content

    def test_drain_prompt_queue_leaves_steer_messages(self, tmp_path):
        """drain_prompt_queue leaves steer-flagged records for mid-turn draining."""
        from gptme.prompt_queue import (
            drain_prompt_queue,
            drain_steer_prompts,
            queue_prompt,
        )

        logdir = tmp_path / "regular-leaves-steer"
        logdir.mkdir()
        queue_prompt(logdir, "steer guidance", steer=True)
        queue_prompt(logdir, "regular turn", steer=False)

        # drain_prompt_queue must skip steer records
        regular_msgs = drain_prompt_queue(logdir)
        assert len(regular_msgs) == 1
        assert "regular turn" in regular_msgs[0].content

        # steer record is still on disk
        steer_msgs = drain_steer_prompts(logdir)
        assert len(steer_msgs) == 1
        assert "steer guidance" in steer_msgs[0].content

    def test_control_hook_injects_steer_as_user_message(self, tmp_path):
        """_subagent_control_hook yields steer messages as user messages mid-turn."""
        from gptme.prompt_queue import queue_prompt

        logdir = tmp_path / "hook-steer-inject"
        logdir.mkdir()
        queue_prompt(logdir, "Focus only on async frameworks", steer=True)

        manager = self._make_manager(logdir)
        msgs = list(_subagent_control_hook(manager))

        assert len(msgs) == 1
        assert msgs[0].role == "user"
        assert "Focus only on async frameworks" in msgs[0].content

    def test_control_hook_injects_multiple_steer_messages_in_order(self, tmp_path):
        """Multiple steer messages are injected in FIFO order."""
        from gptme.prompt_queue import queue_prompt

        logdir = tmp_path / "hook-steer-fifo"
        logdir.mkdir()
        queue_prompt(logdir, "First guidance", steer=True)
        queue_prompt(logdir, "Second guidance", steer=True)

        manager = self._make_manager(logdir)
        msgs = list(_subagent_control_hook(manager))

        assert len(msgs) == 2
        assert "First guidance" in msgs[0].content
        assert "Second guidance" in msgs[1].content

    def test_control_hook_noop_when_no_steer_and_no_cancel(self, tmp_path):
        """Hook is inert when there is no control file and no steer queue."""
        logdir = tmp_path / "hook-noop"
        logdir.mkdir()

        manager = self._make_manager(logdir)
        msgs = list(_subagent_control_hook(manager))
        assert msgs == []

    def test_control_hook_cancel_wins_over_pending_steer(self, tmp_path):
        """When there is both a cancel op and a steer message, cancel takes priority."""
        from gptme.prompt_queue import drain_steer_prompts, queue_prompt
        from gptme.tools.subagent.control import append_control_op

        logdir = tmp_path / "hook-cancel-over-steer"
        logdir.mkdir()
        queue_prompt(logdir, "Do this instead", steer=True)
        append_control_op(logdir, "cancel", agent_id="test-agent")

        manager = self._make_manager(logdir)

        with pytest.raises(SessionCompleteException):
            list(_subagent_control_hook(manager))

        # Steer message was not consumed — still on disk after cancel
        # (no point injecting guidance when the agent is stopping)
        steer_msgs = drain_steer_prompts(logdir)
        assert len(steer_msgs) == 1

    def test_control_hook_regular_prompt_not_injected_mid_turn(self, tmp_path):
        """Regular (non-steer) queued prompts are NOT injected mid-turn by the hook."""
        from gptme.prompt_queue import queue_prompt

        logdir = tmp_path / "hook-no-regular-inject"
        logdir.mkdir()
        queue_prompt(logdir, "Between-turn prompt", steer=False)

        manager = self._make_manager(logdir)
        msgs = list(_subagent_control_hook(manager))

        # Hook must not consume non-steer messages
        assert msgs == []
