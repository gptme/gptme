"""Integration tests for the subagent thread-mode completion roundtrip.

These tests exercise the full path without a live LLM:
  subagent() → thread spawned → log file created → _read_log() → result stored
  → notify_completion() queued → subagent_wait() returns result

Prior to PR #3102 (clear_tools() fix), the ``subagent-complete-roundtrip`` eval
was 0/3 pass because the subagent's thread setup could transiently corrupt the
parent's loaded-tool list, causing ``read`` to appear non-runnable and the
Anthropic API to reject the request with a hard 400.

These tests verify the roundtrip path deterministically without a live model
so regressions can be caught without an API key.
"""

import json
import queue
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import gptme.cli.main as cli_main
import gptme.llm.models as llm_models
import gptme.profiles as profiles
import gptme.tools.subagent.api as subagent_api
from gptme.tools.subagent.api import subagent, subagent_wait
from gptme.tools.subagent.hooks import (
    _subagent_completion_hook,
)
from gptme.tools.subagent.types import (
    _completion_queue,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)


def _drain_completion_queue():
    while not _completion_queue.empty():
        try:
            _completion_queue.get_nowait()
        except queue.Empty:
            break


def _setup_patches(monkeypatch, tmp_path):
    """Shared helper: apply the four monkeypatches needed for roundtrip tests."""
    monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
    monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
    monkeypatch.setattr(profiles, "get_profile", lambda _: None)
    monkeypatch.setattr(subagent_api._exec, "_cleanup_isolation", lambda sa: None)


@pytest.fixture(autouse=True)
def clean_state():
    """Clear global subagent state before each test."""
    from gptme.tools import clear_tools

    clear_tools()
    with _subagents_lock:
        _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()
    _drain_completion_queue()
    yield
    with _subagents_lock:
        _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()
    _drain_completion_queue()
    clear_tools()


def _make_log_file(logdir: Path, content: str) -> None:
    """Create a minimal conversation.jsonl log with the given last-message content."""
    logdir.mkdir(parents=True, exist_ok=True)
    logfile = logdir / "conversation.jsonl"
    # Minimal log: one user message + one assistant message with the complete block
    messages = [
        {
            "role": "user",
            "content": "Task",
            "timestamp": "2026-01-01T00:00:00",
        },
        {
            "role": "assistant",
            "content": content,
            "timestamp": "2026-01-01T00:00:01",
        },
    ]
    with logfile.open("w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")


class TestThreadModeCompletionRoundtrip:
    """End-to-end thread-mode roundtrip without a live LLM.

    Patches _create_subagent_thread to create a fake log file with a
    complete block, then verifies that subagent_wait() returns the correct
    result and that notify_completion() is queued.
    """

    def test_success_result_from_complete_block(self, monkeypatch, tmp_path):
        """subagent_wait returns success when subagent log contains a complete block."""
        _setup_patches(monkeypatch, tmp_path)

        def fast_thread(**kwargs):
            logdir = kwargs["logdir"]
            _make_log_file(logdir, "```complete\nCOMPLETE_SUM: 5050\n```")

        monkeypatch.setattr(subagent_api._exec, "_create_subagent_thread", fast_thread)

        subagent("roundtrip-ok", "Compute sum 1..100 and return COMPLETE_SUM: 5050")

        result = subagent_wait("roundtrip-ok", timeout=10)
        assert result["status"] == "success"
        assert "5050" in result["result"]

    def test_failure_result_when_no_log(self, monkeypatch, tmp_path):
        """subagent_wait returns failure when subagent exits without writing a log."""
        _setup_patches(monkeypatch, tmp_path)

        def no_op_thread(**kwargs):
            pass  # does not create any log file

        monkeypatch.setattr(subagent_api._exec, "_create_subagent_thread", no_op_thread)

        subagent("roundtrip-nofile", "task that creates no log")

        result = subagent_wait("roundtrip-nofile", timeout=10)
        assert result["status"] == "failure"
        assert "log" in result["result"].lower() or "exited" in result["result"].lower()

    def test_completion_hook_notified_on_success(self, monkeypatch, tmp_path):
        """notify_completion is called after a successful subagent run."""
        _setup_patches(monkeypatch, tmp_path)

        def fast_thread(**kwargs):
            logdir = kwargs["logdir"]
            _make_log_file(logdir, "```complete\nDONE\n```")

        monkeypatch.setattr(subagent_api._exec, "_create_subagent_thread", fast_thread)

        subagent("hook-test", "task")

        # Wait for thread to finish
        with _subagents_lock:
            sa = next((s for s in _subagents if s.agent_id == "hook-test"), None)
        assert sa is not None
        if sa.thread:
            sa.thread.join(timeout=10)

        # The completion hook should have queued a notification
        assert not _completion_queue.empty()
        agent_id, status, summary = _completion_queue.get_nowait()
        assert agent_id == "hook-test"
        assert status == "success"

    def test_hook_delivers_notification_as_system_message(self, monkeypatch, tmp_path):
        """_subagent_completion_hook yields a system message for the parent."""
        _setup_patches(monkeypatch, tmp_path)

        def fast_thread(**kwargs):
            logdir = kwargs["logdir"]
            _make_log_file(logdir, "```complete\nFINISHED\n```")

        monkeypatch.setattr(subagent_api._exec, "_create_subagent_thread", fast_thread)

        subagent("hook-delivery", "task")

        # Wait for thread to finish
        with _subagents_lock:
            sa = next((s for s in _subagents if s.agent_id == "hook-delivery"), None)
        assert sa is not None
        if sa.thread:
            sa.thread.join(timeout=10)

        # Drain the hook — it should yield a system message
        manager = MagicMock()
        messages = list(
            _subagent_completion_hook(manager, interactive=False, prompt_queue=None)
        )
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "hook-delivery" in messages[0].content
        assert "✅" in messages[0].content

    def test_result_stored_in_cache_for_subagent_wait(self, monkeypatch, tmp_path):
        """After thread completes, result is in _subagent_results for subagent_wait."""
        _setup_patches(monkeypatch, tmp_path)

        def fast_thread(**kwargs):
            logdir = kwargs["logdir"]
            _make_log_file(logdir, "```complete\nCACHED_RESULT\n```")

        monkeypatch.setattr(subagent_api._exec, "_create_subagent_thread", fast_thread)

        subagent("cache-check", "task")

        result = subagent_wait("cache-check", timeout=10)
        assert result["status"] == "success"
        # Result should also be in the global cache
        with _subagent_results_lock:
            cached = _subagent_results.get("cache-check")
        assert cached is not None
        assert cached.status == "success"


class TestClarificationRoundtrip:
    """Verify the clarification request path works end-to-end."""

    def test_clarify_block_detected(self, monkeypatch, tmp_path):
        """A clarify block in the log sets status to clarification_needed."""
        _setup_patches(monkeypatch, tmp_path)

        def clarify_thread(**kwargs):
            logdir = kwargs["logdir"]
            _make_log_file(logdir, "```clarify\nWhich language should I use?\n```")

        monkeypatch.setattr(
            subagent_api._exec, "_create_subagent_thread", clarify_thread
        )

        subagent("clarify-agent", "Write a greeting")
        result = subagent_wait("clarify-agent", timeout=10)
        assert result["status"] == "clarification_needed"
        assert "language" in result["result"].lower()

    def test_hook_delivers_clarification_as_question_message(
        self, monkeypatch, tmp_path
    ):
        """Clarification hook notification includes ❓ emoji and subagent_reply hint."""
        _setup_patches(monkeypatch, tmp_path)

        def clarify_thread(**kwargs):
            logdir = kwargs["logdir"]
            _make_log_file(logdir, "```clarify\nWhat format: JSON or CSV?\n```")

        monkeypatch.setattr(
            subagent_api._exec, "_create_subagent_thread", clarify_thread
        )

        subagent("clarify-hook", "Produce output")

        with _subagents_lock:
            sa = next((s for s in _subagents if s.agent_id == "clarify-hook"), None)
        assert sa is not None, "subagent clarify-hook should be registered"
        if sa.thread:
            sa.thread.join(timeout=10)

        manager = MagicMock()
        messages = list(
            _subagent_completion_hook(manager, interactive=False, prompt_queue=None)
        )
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "❓" in messages[0].content
        assert "clarify-hook" in messages[0].content
        assert "subagent_reply" in messages[0].content


class TestParentToolIsolationDuringSubagentRun:
    """Regression tests for the clear_tools() fix (PR #3102 / issue #554).

    When a subagent thread starts, it calls clear_tools() to detach from the
    parent's tool list.  Without this fix, both threads initially share the
    same list object (Python ≥ 3.7 threading.Thread copies ContextVar values
    but not the underlying mutable objects — this semantic is stable across
    all supported Python versions), so appends inside the subagent's
    init_tools() would mutate the parent's list and could make tools appear
    non-runnable in the parent's concurrent execute_msg() calls.
    """

    def test_parent_tools_not_mutated_by_concurrent_subagent(
        self, monkeypatch, tmp_path
    ):
        """Parent's tool list is stable while a subagent thread is initializing.

        Uses copy_context().run() to simulate Python ≥ 3.7 semantics where
        the child thread inherits the parent's ContextVar mapping (same list
        object).  Verifies that clear_tools() at thread entry prevents the
        child's tool operations from affecting the parent.
        """
        from contextvars import copy_context

        from gptme.tools import clear_tools, get_tools, init_tools, load_tool

        # Set up a known parent tool list
        init_tools(["read"])
        parent_tools_before = list(get_tools())

        parent_list = parent_tools_before[:]  # snapshot before child starts

        ready = threading.Event()
        done = threading.Event()
        child_errors: list[BaseException] = []
        child_tool_names: list[set[str]] = []

        def child_with_clear():
            """Simulates _create_subagent_thread with clear_tools() at entry."""
            try:
                clear_tools()  # detach from parent's list
                load_tool(
                    "shell"
                )  # append to OWN fresh list; shell is always available
                child_tool_names.append({tool.name for tool in get_tools()})
            except BaseException as exc:
                child_errors.append(exc)
            finally:
                ready.set()
            done.wait(timeout=5)

        ctx = copy_context()
        t = threading.Thread(target=lambda: ctx.run(child_with_clear), daemon=True)
        t.start()

        assert ready.wait(timeout=5), "child thread did not reach tool-loading point"
        # While child is running, parent's tool list should be unchanged
        parent_tools_after = list(get_tools())
        done.set()
        t.join(timeout=5)
        assert not t.is_alive(), "child thread did not stop"
        if child_errors:
            raise child_errors[0]

        parent_names_before = {tool.name for tool in parent_list}
        parent_names_after = {tool.name for tool in parent_tools_after}
        assert parent_names_before == {"read"}
        assert child_tool_names == [{"shell"}]
        assert parent_names_before == parent_names_after, (
            f"Parent tool list mutated by child thread: "
            f"before={parent_names_before}, after={parent_names_after}"
        )
