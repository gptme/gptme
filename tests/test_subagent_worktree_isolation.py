"""Tests for subagent worktree isolation contract (issue #3190).

Covers:
- subagent(..., isolation="worktree") API
- subagent_parallel([...], isolation="worktree") fan-out
- Automatic cleanup on success and failure
- Smart cleanup: branch preserved when changes exist, removed when unchanged
- Concurrent agents don't clobber each other (isolation guarantee)

Unit-style — mocks the LLM/execution layer; no real API calls needed.
"""

import importlib
import subprocess
from pathlib import Path

import pytest

import gptme.tools.subagent.api as subagent_api
import gptme.tools.subagent.execution as subagent_execution
from gptme.tools.subagent.api import subagent
from gptme.tools.subagent.batch import subagent_parallel
from gptme.tools.subagent.types import (
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_subagent_state():
    """Clear global subagent state between tests."""
    with _subagents_lock:
        _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()
    yield
    with _subagents_lock:
        _subagents.clear()
    with _subagent_results_lock:
        _subagent_results.clear()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Minimal git repository for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "main"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--no-verify", "-m", "init"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return repo


def _patch_subagent_env(monkeypatch, tmp_path):
    """Patch the minimal set of imports so subagent() can run without real LLM/CLI."""
    cli_main = importlib.import_module("gptme.cli.main")
    llm_models = importlib.import_module("gptme.llm.models")

    monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
    monkeypatch.setattr(llm_models, "get_default_model", lambda: None)
    monkeypatch.setattr(
        subagent_execution, "_create_subagent_thread", lambda **kw: None
    )
    monkeypatch.setattr(subagent_api, "notify_completion", lambda *a, **kw: None)


def _wait_for_agent(agent_id: str, timeout: float = 5.0) -> None:
    with _subagents_lock:
        sa = next((s for s in _subagents if s.agent_id == agent_id), None)
    if sa and sa.thread:
        sa.thread.join(timeout=timeout)


# ---------------------------------------------------------------------------
# isolation="worktree" parameter validation
# ---------------------------------------------------------------------------


def test_isolation_worktree_enables_isolated_flag(monkeypatch, tmp_path, git_repo):
    """isolation='worktree' sets isolated=True and isolation_mode='worktree'."""
    git_worktree = importlib.import_module("gptme.util.git_worktree")
    created: list[Path] = []

    monkeypatch.setattr(git_worktree, "get_git_root", lambda _: git_repo)

    def _mock_create_worktree(repo_path, branch_name=None, worktree_base=None):
        created.append(branch_name)
        return tmp_path / "wts" / (branch_name or "wt")

    monkeypatch.setattr(git_worktree, "create_worktree", _mock_create_worktree)
    monkeypatch.setattr(subagent_execution, "_cleanup_isolation", lambda sa: None)
    _patch_subagent_env(monkeypatch, tmp_path)

    subagent("iso-test", "do something", isolation="worktree", workdir=git_repo)
    _wait_for_agent("iso-test")

    with _subagents_lock:
        sa = next((s for s in _subagents if s.agent_id == "iso-test"), None)

    assert sa is not None
    assert sa.isolated is True, "isolated must be True when isolation='worktree'"
    assert sa.isolation_mode == "worktree", "isolation_mode must be 'worktree'"
    assert created, "create_worktree should have been called"


def test_isolation_unknown_value_raises():
    """isolation with an unsupported string raises ValueError immediately."""
    with pytest.raises(ValueError, match="Unknown isolation mode"):
        subagent("bad-iso", "task", isolation="container")  # type: ignore[arg-type]


def test_isolation_none_does_not_create_worktree(monkeypatch, tmp_path):
    """Default (no isolation) never calls create_worktree."""
    git_worktree = importlib.import_module("gptme.util.git_worktree")
    create_calls: list = []

    def _noop_create_worktree(*a, **kw):
        create_calls.append(1)
        return tmp_path

    monkeypatch.setattr(git_worktree, "create_worktree", _noop_create_worktree)
    monkeypatch.setattr(subagent_execution, "_cleanup_isolation", lambda sa: None)
    _patch_subagent_env(monkeypatch, tmp_path)

    subagent("no-iso", "do something")
    _wait_for_agent("no-iso")

    assert not create_calls, "No worktree should be created without isolation parameter"


# ---------------------------------------------------------------------------
# subagent_parallel isolation="worktree" fan-out
# ---------------------------------------------------------------------------


def test_subagent_parallel_isolation_worktree_creates_per_agent_worktree(
    monkeypatch, tmp_path, git_repo
):
    """subagent_parallel(isolation='worktree') gives each agent its own worktree."""
    git_worktree = importlib.import_module("gptme.util.git_worktree")
    created: list[str] = []

    def fake_create(repo_path, branch_name=None, worktree_base=None):
        created.append(branch_name or "unnamed")
        wt = tmp_path / "wts" / (branch_name or "unnamed")
        wt.mkdir(parents=True, exist_ok=True)
        return wt

    monkeypatch.setattr(git_worktree, "get_git_root", lambda _: git_repo)
    monkeypatch.setattr(git_worktree, "create_worktree", fake_create)
    monkeypatch.setattr(subagent_execution, "_cleanup_isolation", lambda sa: None)
    _patch_subagent_env(monkeypatch, tmp_path)

    # Patch subagent_wait so the parallel call doesn't block waiting for real work
    monkeypatch.setattr(
        "gptme.tools.subagent.batch.subagent_wait",
        lambda agent_id, **kw: {"status": "success", "result": "done"},
    )

    tasks = [("agent-x", "task x"), ("agent-y", "task y")]
    subagent_parallel(tasks, isolation="worktree", workdir=git_repo)

    assert len(created) == len(tasks), (
        f"Expected {len(tasks)} worktrees created, got {len(created)}"
    )
    # Branch names should be distinct
    assert len(set(created)) == len(tasks), (
        "Each agent should get a distinct worktree branch"
    )


# ---------------------------------------------------------------------------
# Automatic cleanup contract
# ---------------------------------------------------------------------------


def test_cleanup_called_after_thread_completes(monkeypatch, tmp_path, git_repo):
    """_cleanup_isolation is called when the thread finishes normally."""
    git_worktree = importlib.import_module("gptme.util.git_worktree")
    cleanup_calls: list[str] = []

    monkeypatch.setattr(git_worktree, "get_git_root", lambda _: git_repo)
    monkeypatch.setattr(
        git_worktree,
        "create_worktree",
        lambda repo, branch_name=None, worktree_base=None: (
            tmp_path / "wts" / (branch_name or "wt")
        ),
    )

    def recording_cleanup(sa):
        cleanup_calls.append(sa.agent_id)

    monkeypatch.setattr(subagent_execution, "_cleanup_isolation", recording_cleanup)
    _patch_subagent_env(monkeypatch, tmp_path)

    subagent("cleanup-agent", "do something", isolation="worktree", workdir=git_repo)
    _wait_for_agent("cleanup-agent")

    assert "cleanup-agent" in cleanup_calls, (
        "_cleanup_isolation was not called after subagent thread completed"
    )


def test_cleanup_called_when_thread_raises(monkeypatch, tmp_path, git_repo):
    """_cleanup_isolation is called even when the subagent thread raises an exception."""
    git_worktree = importlib.import_module("gptme.util.git_worktree")
    cleanup_calls: list[str] = []

    monkeypatch.setattr(git_worktree, "get_git_root", lambda _: git_repo)
    monkeypatch.setattr(
        git_worktree,
        "create_worktree",
        lambda repo, branch_name=None, worktree_base=None: (
            tmp_path / "wts" / (branch_name or "wt")
        ),
    )
    monkeypatch.setattr(
        subagent_execution,
        "_create_subagent_thread",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("simulated subagent failure")),
    )
    monkeypatch.setattr(subagent_api, "notify_completion", lambda *a, **kw: None)

    cli_main = importlib.import_module("gptme.cli.main")
    llm_models = importlib.import_module("gptme.llm.models")
    monkeypatch.setattr(cli_main, "get_logdir", lambda name: tmp_path / name)
    monkeypatch.setattr(llm_models, "get_default_model", lambda: None)

    def recording_cleanup(sa):
        cleanup_calls.append(sa.agent_id)

    monkeypatch.setattr(subagent_execution, "_cleanup_isolation", recording_cleanup)

    subagent("exc-agent", "will fail", isolation="worktree", workdir=git_repo)
    _wait_for_agent("exc-agent")

    assert "exc-agent" in cleanup_calls, (
        "_cleanup_isolation was not called after subagent thread raised"
    )


# ---------------------------------------------------------------------------
# Smart cleanup — branch preserved vs deleted based on changes
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path, name: str) -> Path:
    """Create a minimal git repo with one commit."""
    repo = tmp_path / name
    repo.mkdir()
    for cmd in [
        ["git", "init"],
        ["git", "config", "user.email", "t@t.com"],
        ["git", "config", "user.name", "T"],
        ["git", "checkout", "-b", "main"],
    ]:
        subprocess.run(cmd, cwd=repo, capture_output=True, check=True)
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--no-verify", "-m", "init"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return repo


def test_smart_cleanup_preserves_branch_when_worktree_has_changes(tmp_path):
    """isolation_mode='worktree': branch kept (and returned) when worktree has changes."""
    from gptme.tools.subagent.types import Subagent
    from gptme.util.git_worktree import create_worktree

    repo = _make_repo(tmp_path, "repo-dirty")
    wt = create_worktree(
        repo, branch_name="dirty-agent-wt", worktree_base=tmp_path / "wts"
    )

    # Dirty the worktree (uncommitted change)
    (wt / "output.txt").write_text("agent result\n")

    sa = Subagent(
        agent_id="dirty-agent",
        prompt="task",
        thread=None,
        logdir=tmp_path / "log",
        model=None,
        isolated=True,
        isolation_mode="worktree",
        worktree_path=wt,
        repo_path=repo,
    )

    preserved = subagent_execution._cleanup_isolation(sa)

    assert not wt.exists(), "Working tree directory should be removed"
    assert preserved == "dirty-agent-wt", (
        "Branch name should be returned when changes exist"
    )

    result = subprocess.run(
        ["git", "branch", "--list", "dirty-agent-wt"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "dirty-agent-wt" in result.stdout, "Branch should still exist in the repo"

    # Cleanup the preserved branch
    subprocess.run(
        ["git", "branch", "-D", "dirty-agent-wt"],
        cwd=repo,
        capture_output=True,
        check=False,
    )


def test_smart_cleanup_removes_branch_when_worktree_is_clean(tmp_path):
    """isolation_mode='worktree': branch and directory removed when worktree is unchanged."""
    from gptme.tools.subagent.types import Subagent
    from gptme.util.git_worktree import create_worktree

    repo = _make_repo(tmp_path, "repo-clean")
    wt = create_worktree(
        repo, branch_name="clean-agent-wt", worktree_base=tmp_path / "wts"
    )

    sa = Subagent(
        agent_id="clean-agent",
        prompt="task",
        thread=None,
        logdir=tmp_path / "log",
        model=None,
        isolated=True,
        isolation_mode="worktree",
        worktree_path=wt,
        repo_path=repo,
    )

    preserved = subagent_execution._cleanup_isolation(sa)

    assert not wt.exists(), "Working tree directory should be removed"
    assert preserved is None, "No branch should be preserved when worktree is clean"

    result = subprocess.run(
        ["git", "branch", "--list", "clean-agent-wt"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "clean-agent-wt" not in result.stdout, (
        "Branch should be deleted (no changes)"
    )


def test_legacy_isolated_bool_always_does_full_cleanup(tmp_path):
    """isolated=True without isolation_mode always removes both dir and branch."""
    from gptme.tools.subagent.types import Subagent
    from gptme.util.git_worktree import create_worktree

    repo = _make_repo(tmp_path, "repo-legacy")
    wt = create_worktree(repo, branch_name="legacy-wt", worktree_base=tmp_path / "wts")

    # Make the worktree dirty
    (wt / "file.txt").write_text("changed\n")

    sa = Subagent(
        agent_id="legacy-agent",
        prompt="task",
        thread=None,
        logdir=tmp_path / "log",
        model=None,
        isolated=True,
        isolation_mode=None,  # legacy: no string mode → full cleanup regardless
        worktree_path=wt,
        repo_path=repo,
    )

    preserved = subagent_execution._cleanup_isolation(sa)

    assert not wt.exists(), "Working tree should be removed"
    assert preserved is None, "Legacy isolated=True should always do full cleanup"

    result = subprocess.run(
        ["git", "branch", "--list", "legacy-wt"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "legacy-wt" not in result.stdout, "Branch should be deleted in legacy mode"


# ---------------------------------------------------------------------------
# Isolation guarantee: concurrent agents don't clobber each other
# ---------------------------------------------------------------------------


def test_concurrent_worktrees_are_isolated(tmp_path):
    """Files written by agent A are not visible in agent B's worktree."""
    from gptme.util.git_worktree import cleanup_worktree, create_worktree

    repo = _make_repo(tmp_path, "repo-concurrent")
    base = tmp_path / "wts"

    wt_a = create_worktree(repo, branch_name="agent-a", worktree_base=base)
    wt_b = create_worktree(repo, branch_name="agent-b", worktree_base=base)

    try:
        (wt_a / "a_output.txt").write_text("from A\n")
        (wt_b / "b_output.txt").write_text("from B\n")

        assert not (wt_a / "b_output.txt").exists(), "A should not see B's file"
        assert not (wt_b / "a_output.txt").exists(), "B should not see A's file"
        assert not (repo / "a_output.txt").exists(), "Main repo unaffected by A"
        assert not (repo / "b_output.txt").exists(), "Main repo unaffected by B"
    finally:
        cleanup_worktree(wt_a, repo)
        cleanup_worktree(wt_b, repo)
