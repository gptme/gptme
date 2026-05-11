"""Tests for :mod:`gptme.workspace_snapshot` and the auto-snapshot hook.

Covers:

- shell mutability classifier (positive + negative cases)
- tmux classifier (visible-payload cases only)
- always-mutating vs conditionally-mutating tool dispatch
- snapshot / restore round-trip on a temp workspace
- prune retention
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gptme.hooks.auto_snapshots import (
    _enabled,
    _post,
    _pre,
    _pre_tree_var,
    classify_tool_use,
    is_mutating_shell_payload,
    is_mutating_tmux_payload,
)
from gptme.tools.base import ToolUse
from gptme.workspace_snapshot import (
    Shadow,
    init_shadow,
    list_snapshots,
    prune,
    restore,
    snapshot,
    tree_hash,
)


@pytest.fixture
def isolated_state_dir(tmp_path, monkeypatch):
    """Point gptme.dirs.get_state_dir() at a temp dir."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    # Seed a file so the workspace isn't empty.
    (ws / "README.md").write_text("hello\n")
    return ws


# --- shell classifier -------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "echo hi > out.txt",
        "ls -la >> log",
        "cat file | tee copy.txt",
        "sed -i 's/foo/bar/' file.py",
        "perl -pi -e 's/x/y/' src/*.py",
        "rm -rf tmp/build",
        "touch newfile",
        "mkdir -p dist/",
        "mv a.txt b.txt",
        "cp src.py dst.py",
        "ln -s real link",
        "git apply patch.diff",
        "git restore .",
        "git checkout file.py",
        "git clean -fd",
        "git reset --hard HEAD",
        "tar -xzf release.tar.gz",
        "unzip bundle.zip",
        "make install",
        "cargo build",
        "npm install",
        "uv sync",
        "pytest tests/",
        # Heredoc that writes a file.
        "cat <<'EOF' > out.py\nprint('hi')\nEOF",
    ],
)
def test_shell_classifier_positive(cmd):
    assert is_mutating_shell_payload(cmd), cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "ls -la",
        "cat README.md",
        "rg pattern src/",
        "grep -n foo bar",
        "find . -name '*.py'",
        "git status",
        "git log --oneline",
        "git diff",
        "git show HEAD",
        "echo hello",
        "pwd",
        "wc -l file.py",
        "",
        "   ",
    ],
)
def test_shell_classifier_negative(cmd):
    assert not is_mutating_shell_payload(cmd), cmd


def test_shell_classifier_handles_none():
    assert is_mutating_shell_payload(None) is False


# --- tmux classifier --------------------------------------------------------


def test_tmux_classifier_new_session_mutating():
    assert is_mutating_tmux_payload("new-session -d 'echo hi > out.txt'")


def test_tmux_classifier_new_session_read_only():
    assert not is_mutating_tmux_payload("new-session -d 'ls -la'")


def test_tmux_classifier_split_window_mutating():
    assert is_mutating_tmux_payload("split-window 'rm -rf build/'")


def test_tmux_classifier_send_keys_ignored():
    # send-keys reconstruction is intentionally deferred to a follow-up.
    assert not is_mutating_tmux_payload("send-keys -t s 'rm -rf build/' Enter")


def test_tmux_classifier_none():
    assert is_mutating_tmux_payload(None) is False


# --- dispatch ---------------------------------------------------------------


@pytest.mark.parametrize("tool", ["save", "append", "patch", "morph"])
def test_classify_always_mutating(tool):
    assert classify_tool_use(tool, "anything")
    assert classify_tool_use(tool, "")


def test_classify_shell_only_when_mutating():
    assert classify_tool_use("shell", "echo hi > out.txt")
    assert not classify_tool_use("shell", "ls -la")


def test_classify_tmux_only_when_mutating():
    assert classify_tool_use("tmux", "new-session 'rm -rf x'")
    assert not classify_tool_use("tmux", "new-session 'ls'")


def test_classify_unknown_tool():
    # Unknown tools (e.g. read_url, browser) never trigger snapshots.
    assert not classify_tool_use("browser", "anything")
    assert not classify_tool_use("read_url", "https://example.com")


# --- snapshot / restore round trip ------------------------------------------


def test_init_creates_shadow_and_initial_snapshot(isolated_state_dir, workspace):
    shadow = init_shadow(workspace)
    assert shadow.initialized()
    # XDG-located, not inside workspace.
    assert str(shadow.git_dir).startswith(str(isolated_state_dir))
    assert not (workspace / ".gptme-snapshots").exists()
    snapshots = list_snapshots(shadow)
    assert len(snapshots) == 1
    assert snapshots[0][1] == "initial"


def test_snapshot_after_mutation_diverges_tree(isolated_state_dir, workspace):
    shadow = init_shadow(workspace)
    before = tree_hash(shadow)
    (workspace / "new.txt").write_text("payload\n")
    after = tree_hash(shadow)
    assert before != after


def test_snapshot_restore_roundtrip(isolated_state_dir, workspace):
    shadow = init_shadow(workspace)
    (workspace / "keep.txt").write_text("v1\n")
    sha_v1 = snapshot(shadow, label="pre:save")
    assert sha_v1

    # Mutate: change file + add another.
    (workspace / "keep.txt").write_text("v2\n")
    (workspace / "extra.txt").write_text("oops\n")
    snapshot(shadow, label="post:save")
    assert (workspace / "extra.txt").exists()
    assert (workspace / "keep.txt").read_text() == "v2\n"

    # Restore v1 — modifications reverted, extra removed.
    assert restore(shadow, sha_v1)
    assert (workspace / "keep.txt").read_text() == "v1\n"
    assert not (workspace / "extra.txt").exists()


def test_restore_creates_safety_snapshot(isolated_state_dir, workspace):
    shadow = init_shadow(workspace)
    (workspace / "a.txt").write_text("first\n")
    sha = snapshot(shadow, label="checkpoint")
    assert sha is not None
    (workspace / "a.txt").write_text("second\n")

    pre_count = len(list_snapshots(shadow, limit=100))
    restore(shadow, sha)
    post_count = len(list_snapshots(shadow, limit=100))
    # Safety snapshot inserted on top of the restore action.
    assert post_count >= pre_count + 1
    labels = [label for _, label in list_snapshots(shadow, limit=100)]
    assert any("pre-restore-to-" in label for label in labels)


# --- prune ------------------------------------------------------------------


def test_prune_keeps_newest_n(isolated_state_dir, workspace):
    shadow = init_shadow(workspace)
    for i in range(10):
        (workspace / f"f{i}.txt").write_text(str(i))
        snapshot(shadow, label=f"snap-{i}")
    dropped = prune(shadow, keep=3)
    assert dropped > 0
    remaining = list_snapshots(shadow, limit=50)
    assert len(remaining) == 3


def test_prune_noop_when_under_limit(isolated_state_dir, workspace):
    shadow = init_shadow(workspace)
    (workspace / "f.txt").write_text("x")
    snapshot(shadow, label="one")
    dropped = prune(shadow, keep=50)
    assert dropped == 0


# --- hook activation gate ---------------------------------------------------


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GPTME_AUTO_SNAPSHOTS", raising=False)
    assert not _enabled()


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_enabled_via_env(monkeypatch, val):
    monkeypatch.setenv("GPTME_AUTO_SNAPSHOTS", val)
    assert _enabled()


def test_pre_post_noop_when_disabled(isolated_state_dir, workspace, monkeypatch):
    monkeypatch.delenv("GPTME_AUTO_SNAPSHOTS", raising=False)
    tu = MagicMock(tool="save", content="x")
    # Generator returns nothing and does not create a shadow.
    list(_pre(MagicMock(), workspace, tu))
    list(_post(MagicMock(), workspace, tu))
    # No XDG shadow created.
    snaps_dir = isolated_state_dir / "gptme" / "workspace-snapshots"
    assert not snaps_dir.exists()


def test_pre_post_snapshot_round_trip_via_hook(
    isolated_state_dir, workspace, monkeypatch
):
    monkeypatch.setenv("GPTME_AUTO_SNAPSHOTS", "1")
    _pre_tree_var.set(None)

    # Simulate a save tool call.
    tu = MagicMock(tool="save", content="anything")
    list(_pre(MagicMock(), workspace, tu))
    # Hook ran on a clean workspace — at least the initial snapshot is recorded.
    shadow = Shadow.for_workspace(workspace)
    assert shadow.initialized()
    snaps_before = list_snapshots(shadow, limit=100)
    pre_labels = [label for _, label in snaps_before]
    assert any(label == "pre:save" for label in pre_labels)

    # Mutate the workspace, then run post hook.
    (workspace / "after-save.txt").write_text("new\n")
    list(_post(MagicMock(), workspace, tu))
    snaps_after = list_snapshots(shadow, limit=100)
    post_labels = [label for _, label in snaps_after]
    assert any(label == "post:save" for label in post_labels)


def test_post_skips_when_no_mutation(isolated_state_dir, workspace, monkeypatch):
    """Post hook should not emit a snapshot when the tree did not change."""
    monkeypatch.setenv("GPTME_AUTO_SNAPSHOTS", "1")
    _pre_tree_var.set(None)
    tu = MagicMock(tool="shell", content="echo hi > out.txt")

    list(_pre(MagicMock(), workspace, tu))
    shadow = Shadow.for_workspace(workspace)
    pre_count = len(list_snapshots(shadow, limit=100))

    # Run post immediately, no actual workspace mutation.
    list(_post(MagicMock(), workspace, tu))
    post_count = len(list_snapshots(shadow, limit=100))
    # Pre adds one snapshot; post adds zero because tree is unchanged.
    assert post_count == pre_count


def test_pre_post_snapshot_round_trip_via_tool_format_shell_kwargs(
    isolated_state_dir, workspace, monkeypatch
):
    monkeypatch.setenv("GPTME_AUTO_SNAPSHOTS", "1")
    _pre_tree_var.set(None)
    tu = ToolUse(
        tool="shell",
        args=None,
        content=None,
        kwargs={"command": "printf 'beta\\n' >> smoke.txt"},
        _format="tool",
    )

    list(_pre(MagicMock(), workspace, tu))
    shadow = Shadow.for_workspace(workspace)
    assert shadow.initialized()
    pre_labels = [label for _, label in list_snapshots(shadow, limit=100)]
    assert any(label == "pre:shell" for label in pre_labels)

    (workspace / "smoke.txt").write_text("alpha\nbeta\n")
    list(_post(MagicMock(), workspace, tu))
    post_labels = [label for _, label in list_snapshots(shadow, limit=100)]
    assert any(label == "post:shell" for label in post_labels)


def test_hook_skips_non_mutating_shell(isolated_state_dir, workspace, monkeypatch):
    monkeypatch.setenv("GPTME_AUTO_SNAPSHOTS", "1")
    _pre_tree_var.set(None)
    tu = MagicMock(tool="shell", content="ls -la")
    list(_pre(MagicMock(), workspace, tu))
    # No shadow should be initialized for a plain read.
    snaps_dir = isolated_state_dir / "gptme" / "workspace-snapshots"
    assert not snaps_dir.exists()
