"""Unit tests for subagent module pure-logic functions.

Tests the refactored subagent package (hooks, types, batch) without
requiring API keys or running actual LLM calls.
"""

import importlib
import json
import queue
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import gptme.tools.subagent.api as subagent_api
import gptme.tools.subagent.execution as subagent_execution
from gptme.tools.subagent.api import subagent, subagent_cancel
from gptme.tools.subagent.batch import BatchJob
from gptme.tools.subagent.execution import _monitor_subprocess
from gptme.tools.subagent.hooks import (
    _get_complete_instruction,
    _subagent_completion_hook,
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

    def test_cancel_subprocess_terminates_process(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        mock_proc.wait.return_value = 0
        self._register("proc-agent", process=mock_proc, execution_mode="subprocess")
        result = subagent_cancel("proc-agent")
        mock_proc.terminate.assert_called_once()
        assert "cancelled" in result.lower()
        with _subagent_results_lock:
            assert _subagent_results["proc-agent"].status == "failure"
            assert "Cancelled" in (_subagent_results["proc-agent"].result or "")

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
                "failure", "Cancelled by orchestrator"
            )

        _monitor_subprocess(sa)

        with _subagent_results_lock:
            assert _subagent_results["proc-agent"].status == "failure"
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

    def test_cancel_thread_marks_result(self):
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        self._register("thread-agent", thread=mock_thread)
        result = subagent_cancel("thread-agent")
        assert "cancelled" in result.lower()
        with _subagent_results_lock:
            assert _subagent_results["thread-agent"].status == "failure"

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
                    "failure", "Cancelled by orchestrator"
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
                "failure", "Cancelled by orchestrator"
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
                    "failure", "Cancelled by orchestrator"
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
                "failure", "Cancelled by orchestrator"
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
                    "failure", "Cancelled by orchestrator"
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
                "failure", "Cancelled by orchestrator"
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
                "failure", "Cancelled by orchestrator"
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
                "failure", "Cancelled by orchestrator"
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
                "failure", "Cancelled by orchestrator"
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
                "failure", "Cancelled by orchestrator"
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
        parsed = json.loads((result.result or "").split("\n\nFull log:")[0])
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
        assert result.result.startswith("hello world")
