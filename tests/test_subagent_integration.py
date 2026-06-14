"""Integration tests for subagent cancel + max_concurrent lifecycle.

Uses real threading with mocked LLM calls to exercise the semaphore-gating and
cancel-lifecycle paths without requiring API keys.
"""

import queue
import threading
import time
from pathlib import Path

import pytest

import gptme.tools.subagent.api as subagent_api
from gptme.tools.subagent.api import subagent, subagent_cancel
from gptme.tools.subagent.concurrency import _reset_slot_sem
from gptme.tools.subagent.types import (
    _completion_queue,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)


@pytest.fixture(autouse=True)
def clean_subagent_state():
    """Clear global subagent state BEFORE each test for a clean slate.

    Post-test cleanup (thread join, process terminate, semaphore reset) is
    handled by conftest.py's autouse ``cleanup_subagents_after`` fixture.
    """
    with _subagents_lock:
        _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()
    while not _completion_queue.empty():
        try:
            _completion_queue.get_nowait()
        except queue.Empty:
            break
    _reset_slot_sem()
    return


class TestCancelLifecycle:
    """Integration tests: cancel completes within deadline and marks result as failure."""

    def _spawn_blocking_subagent(
        self,
        agent_id: str,
        hold_event: threading.Event,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        """Spawn a subagent whose LLM call blocks until hold_event is set."""
        import gptme.cli.main as cli_main
        import gptme.llm.models as llm_models
        import gptme.profiles as profiles

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)

        def blocking_thread(**kwargs):
            hold_event.wait(timeout=30)

        monkeypatch.setattr(
            subagent_api._exec, "_create_subagent_thread", blocking_thread
        )
        monkeypatch.setattr(subagent_api._exec, "_cleanup_isolation", lambda sa: None)

        subagent(agent_id, "do a thing")

    def test_cancel_marks_result_as_failure(self, monkeypatch, tmp_path):
        """Cancelled subagent result is failure/cancelled within 5 seconds."""
        hold = threading.Event()

        self._spawn_blocking_subagent("agent-a", hold, monkeypatch, tmp_path)

        # Let the thread start and acquire the semaphore.
        with _subagents_lock:
            sa = next(s for s in _subagents if s.agent_id == "agent-a")
        assert sa.thread is not None
        time.sleep(0.1)

        result_msg = subagent_cancel("agent-a")
        assert "cancelled" in result_msg.lower()

        with _subagent_results_lock:
            result = _subagent_results.get("agent-a")
        assert result is not None
        assert result.status == "failure"
        assert result.result is not None
        assert "cancelled" in result.result.lower()

        # Unblock the thread so it can exit cleanly.
        hold.set()
        sa.thread.join(timeout=5)
        assert not sa.thread.is_alive(), "thread must exit within 5 s after unblock"

    def test_cancel_of_finished_subagent_reports_not_running(
        self, monkeypatch, tmp_path
    ):
        """Cancelling an already-completed subagent returns 'not running' message."""
        hold = threading.Event()
        hold.set()  # unblocked immediately → subagent completes right away

        self._spawn_blocking_subagent("agent-done", hold, monkeypatch, tmp_path)

        with _subagents_lock:
            sa = next(s for s in _subagents if s.agent_id == "agent-done")
        assert sa.thread is not None
        sa.thread.join(timeout=5)
        assert not sa.thread.is_alive()

        result = subagent_cancel("agent-done")
        assert "not running" in result.lower()


class TestMaxConcurrentGating:
    """Integration tests: slot freed by cancel allows a queued subagent to proceed."""

    def test_cancel_releases_slot_for_second_subagent(self, monkeypatch, tmp_path):
        """With max_concurrent=1, cancelling subagent-1 lets subagent-2 acquire the slot."""
        import gptme.cli.main as cli_main
        import gptme.llm.models as llm_models
        import gptme.profiles as profiles

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)

        agent1_hold = threading.Event()
        agent2_started = threading.Event()
        agent2_hold = threading.Event()

        call_count = [0]

        def patched_create_thread(**kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 0:
                agent1_hold.wait(timeout=30)
            else:
                agent2_started.set()
                agent2_hold.wait(timeout=30)

        monkeypatch.setattr(
            subagent_api._exec, "_create_subagent_thread", patched_create_thread
        )
        monkeypatch.setattr(subagent_api._exec, "_cleanup_isolation", lambda sa: None)

        _reset_slot_sem(max_concurrent=1)

        subagent("agent-1", "task one")
        subagent("agent-2", "task two")

        # Give threads time to start; agent-1 holds the slot, agent-2 should block.
        time.sleep(0.2)
        assert not agent2_started.is_set(), (
            "agent-2 must not enter LLM work while agent-1 holds the only slot"
        )

        # Cancel agent-1 (marks result as failure) then unblock its thread.
        result_msg = subagent_cancel("agent-1")
        assert "cancelled" in result_msg.lower()
        agent1_hold.set()

        # agent-1's thread releases the semaphore → agent-2 should acquire it.
        assert agent2_started.wait(timeout=5), (
            "agent-2 must acquire the slot within 5 s after agent-1 is cancelled+unblocked"
        )

        # Clean up agent-2.
        agent2_hold.set()
        with _subagents_lock:
            sa2 = next(s for s in _subagents if s.agent_id == "agent-2")
        assert sa2.thread is not None
        sa2.thread.join(timeout=5)

    def test_slot_available_after_natural_completion(self, monkeypatch, tmp_path):
        """Slot freed by normal completion allows a queued subagent to run."""
        import gptme.cli.main as cli_main
        import gptme.llm.models as llm_models
        import gptme.profiles as profiles

        monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
        monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
        monkeypatch.setattr(profiles, "get_profile", lambda _: None)

        agent2_started = threading.Event()

        call_count = [0]

        def patched_create_thread(**kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 0:
                return  # agent-1 completes immediately
            agent2_started.set()

        monkeypatch.setattr(
            subagent_api._exec, "_create_subagent_thread", patched_create_thread
        )
        monkeypatch.setattr(subagent_api._exec, "_cleanup_isolation", lambda sa: None)

        _reset_slot_sem(max_concurrent=1)

        subagent("agent-x", "quick task")
        subagent("agent-y", "queued task")

        assert agent2_started.wait(timeout=5), (
            "agent-y must start after agent-x completes naturally"
        )

        with _subagents_lock:
            sa_y = next(s for s in _subagents if s.agent_id == "agent-y")
        assert sa_y.thread is not None
        sa_y.thread.join(timeout=5)
