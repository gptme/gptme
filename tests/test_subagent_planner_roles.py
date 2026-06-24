"""Regression tests for planner subtask role -> execution-mode forwarding.

Covers the behavior added in PR #2881 (forward subprocess/isolated defaults from
planner subtask roles): ``_run_planner`` must dispatch each subtask through the
backend its ``role`` resolves to, independently per subtask.

- role="verify"    -> subprocess + isolated  (sandboxed validation)
- role="explore"   -> thread mode            (read-only analysis)
- role="implement" -> thread mode            (full capability)
- no role          -> thread mode, no isolation

The role->defaults resolution itself is unit-tested in
``test_subagent_unit.py::TestResolveRoleDefaults``. These tests cover the
*integration*: that the planner actually routes to the resolved backend, which
``resolve_role_defaults`` coverage alone does not exercise.

The executor backends (subprocess spawn, thread run, worktree creation,
subprocess monitor) are mocked so no real process/thread/worktree is created;
sequential mode is used so every executor joins before ``_run_planner`` returns.
"""

from unittest.mock import MagicMock

import pytest

import gptme.cli.main as cli_main
import gptme.tools.subagent.execution as execution
import gptme.util.git_worktree as git_worktree
from gptme.tools.subagent.concurrency import _reset_slot_sem
from gptme.tools.subagent.types import (
    SubtaskDef,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)


@pytest.fixture
def planner_env(monkeypatch, tmp_path):
    """Mock executor backends and clear global subagent state.

    Returns a dict of the backend mocks so tests can assert dispatch routing.
    """
    _reset_slot_sem()
    with _subagents_lock:
        _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()

    # Cheap logdir, no real LLM lookups.
    monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)

    # Backend mocks: assert which path each subtask takes without doing real work.
    subprocess_spawn = MagicMock(
        name="_run_subagent_subprocess", return_value=MagicMock()
    )
    create_thread = MagicMock(name="_create_subagent_thread")
    monitor = MagicMock(name="_monitor_subprocess")
    monkeypatch.setattr(execution, "_run_subagent_subprocess", subprocess_spawn)
    monkeypatch.setattr(execution, "_create_subagent_thread", create_thread)
    monkeypatch.setattr(execution, "_monitor_subprocess", monitor)

    # Isolation: pretend we're in a git repo and hand back a path without
    # actually creating a worktree on disk.
    monkeypatch.setattr(git_worktree, "get_git_root", lambda ws: tmp_path)
    monkeypatch.setattr(
        git_worktree,
        "create_worktree",
        lambda root, branch_name: tmp_path / branch_name,
    )

    yield {
        "subprocess_spawn": subprocess_spawn,
        "create_thread": create_thread,
        "monitor": monitor,
    }

    with _subagents_lock:
        _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()


def _subtask(task_id: str, role: str | None = None) -> SubtaskDef:
    st: SubtaskDef = {"id": task_id, "description": f"do {task_id}"}
    if role is not None:
        st["role"] = role  # type: ignore[typeddict-item]
    return st


def _registered():
    """Snapshot of currently-registered subagents."""
    with _subagents_lock:
        return list(_subagents)


def test_verify_role_dispatches_subprocess_isolated(planner_env):
    """role="verify" runs the executor in subprocess + isolated mode."""
    execution._run_planner(
        "planner-v",
        "ctx",
        [_subtask("t1", role="verify")],
        execution_mode="sequential",
    )

    planner_env["subprocess_spawn"].assert_called_once()
    planner_env["create_thread"].assert_not_called()

    sa = next(s for s in _registered() if s.agent_id == "planner-v-t1")
    assert sa.execution_mode == "subprocess"
    assert sa.isolated is True
    assert sa.profile == "verifier"


def test_explore_role_dispatches_thread(planner_env):
    """role="explore" stays in thread mode (no subprocess, no isolation)."""
    execution._run_planner(
        "planner-e",
        "ctx",
        [_subtask("t1", role="explore")],
        execution_mode="sequential",
    )

    planner_env["create_thread"].assert_called_once()
    planner_env["subprocess_spawn"].assert_not_called()

    sa = next(s for s in _registered() if s.agent_id == "planner-e-t1")
    assert sa.execution_mode == "thread"
    assert sa.isolated is False
    assert sa.profile == "explorer"


def test_implement_role_dispatches_thread(planner_env):
    """role="implement" stays in thread mode (no subprocess, no isolation)."""
    execution._run_planner(
        "planner-i",
        "ctx",
        [_subtask("t1", role="implement")],
        execution_mode="sequential",
    )

    planner_env["create_thread"].assert_called_once()
    planner_env["subprocess_spawn"].assert_not_called()

    sa = next(s for s in _registered() if s.agent_id == "planner-i-t1")
    assert sa.execution_mode == "thread"
    assert sa.isolated is False


def test_no_role_defaults_to_thread(planner_env):
    """A subtask with no role uses thread mode and no isolation (base default)."""
    execution._run_planner(
        "planner-n",
        "ctx",
        [_subtask("t1")],
        execution_mode="sequential",
    )

    planner_env["create_thread"].assert_called_once()
    planner_env["subprocess_spawn"].assert_not_called()

    sa = next(s for s in _registered() if s.agent_id == "planner-n-t1")
    assert sa.execution_mode == "thread"
    assert sa.isolated is False


def test_mixed_roles_routed_independently(planner_env):
    """Each subtask is routed to its own backend within one planner run."""
    execution._run_planner(
        "planner-m",
        "ctx",
        [
            _subtask("verify-step", role="verify"),
            _subtask("explore-step", role="explore"),
        ],
        execution_mode="sequential",
    )

    # One subprocess executor (verify) and one thread executor (explore).
    planner_env["subprocess_spawn"].assert_called_once()
    planner_env["create_thread"].assert_called_once()

    by_id = {s.agent_id: s for s in _registered()}
    assert by_id["planner-m-verify-step"].execution_mode == "subprocess"
    assert by_id["planner-m-verify-step"].isolated is True
    assert by_id["planner-m-explore-step"].execution_mode == "thread"
    assert by_id["planner-m-explore-step"].isolated is False
