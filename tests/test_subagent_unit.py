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
)
from gptme.tools.subagent.types import (
    ReturnType,
    Subagent,
    _completion_queue,
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
