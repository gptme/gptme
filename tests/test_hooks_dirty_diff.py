"""Tests for the dirty-working-tree diff injection hook (gptme/gptme#4009 follow-on)."""

import subprocess
from pathlib import Path

import pytest

from gptme.hooks import StopPropagation
from gptme.message import Message


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "test-branch", cwd=repo)
    _git("config", "user.email", "bob@superuserlabs.org", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "file.txt").write_text("original\n")
    _git("add", "file.txt", cwd=repo)
    _git("commit", "-q", "-m", "initial", cwd=repo)
    return repo


class _FakeManager:
    def __init__(self, messages, workspace):
        self.log = messages
        self.logdir = None
        self.workspace = workspace


def _run(initial_msgs, workspace: Path | None, manager=None):
    from gptme.hooks.dirty_diff import inject_dirty_diff

    return [
        item
        for item in inject_dirty_diff(
            logdir=None,
            workspace=workspace,
            initial_msgs=initial_msgs,
            manager=manager,
        )
        if not isinstance(item, StopPropagation)
    ]


def test_hook_yields_hidden_system_message_for_dirty_tree(git_repo):
    from gptme.hooks.dirty_diff import _INJECT_SENTINEL

    (git_repo / "file.txt").write_text("changed\n")

    out = _run([], git_repo)

    assert len(out) == 1
    msg = out[0]
    assert msg.role == "system"
    assert msg.hide is True
    assert msg.content.lstrip().startswith(_INJECT_SENTINEL)
    assert "```diff" in msg.content
    assert "-original" in msg.content
    assert "+changed" in msg.content


def test_hook_yields_nothing_for_clean_tree(git_repo):
    assert _run([], git_repo) == []


def test_hook_yields_nothing_without_workspace():
    assert _run([], None) == []


def test_hook_yields_nothing_for_non_git_directory(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _run([], plain) == []


def test_hook_skips_when_already_injected(git_repo):
    from gptme.hooks.dirty_diff import _INJECT_SENTINEL

    (git_repo / "file.txt").write_text("changed\n")
    prior = Message(
        "system",
        f"{_INJECT_SENTINEL}\nalready injected\n",
        hide=True,
    )
    manager = _FakeManager([prior], git_repo)
    assert _run(None, git_repo, manager=manager) == []


def test_hook_reads_workspace_from_manager(git_repo):
    (git_repo / "file.txt").write_text("changed\n")
    manager = _FakeManager([], git_repo)
    out = _run([], None, manager=manager)
    assert len(out) == 1
    assert "+changed" in out[0].content


def test_hook_truncates_large_diffs(git_repo):
    from gptme.hooks.dirty_diff import _MAX_DIFF_CHARS

    (git_repo / "file.txt").write_text("x" * (_MAX_DIFF_CHARS * 2) + "\n")

    out = _run([], git_repo)

    assert len(out) == 1
    assert "(truncated)" in out[0].content
    assert len(out[0].content) < _MAX_DIFF_CHARS * 2


def test_hook_swallows_unexpected_errors(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("gptme.hooks.dirty_diff._get_dirty_diff", boom)
    (git_repo / "file.txt").write_text("changed\n")

    assert _run([], git_repo) == []


def test_register_adds_session_start_and_turn_pre_hooks():
    from gptme.hooks import HookType, clear_hooks, get_hooks
    from gptme.hooks.dirty_diff import register

    clear_hooks()
    register()
    start_names = [hook.name for hook in get_hooks(HookType.SESSION_START)]
    turn_names = [hook.name for hook in get_hooks(HookType.TURN_PRE)]
    assert "dirty_diff.session_start" in start_names
    assert "dirty_diff.turn_pre" in turn_names
