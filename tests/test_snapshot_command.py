"""Tests for the /snapshot command (#495 — agents that tree search)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

import gptme.commands  # noqa: F401 – ensures all commands are registered
from gptme.commands.base import handle_cmd
from gptme.workspace_snapshot import (
    Shadow,
    init_shadow,
    list_snapshots,
    restore,
    snapshot,
)

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_state_dir(tmp_path, monkeypatch):
    """Redirect XDG_STATE_HOME to tmp so shadow repos don't leak into live state."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


@pytest.fixture
def workspace(tmp_path, isolated_state_dir):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.txt").write_text("hello\n")
    return ws


@pytest.fixture
def initialized_shadow(workspace):
    return init_shadow(workspace)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_manager(workspace: Path) -> MagicMock:
    manager = MagicMock()
    manager.workspace = str(workspace)
    return manager


def _run_cmd(manager: MagicMock, args: str) -> None:
    """Dispatch a /snapshot command via handle_cmd."""
    list(handle_cmd(f"/snapshot {args}", manager))


# ── workspace_snapshot primitive tests ────────────────────────────────────────


class TestWorkspaceSnapshotPrimitives:
    def test_init_shadow_idempotent(self, workspace, isolated_state_dir):
        shadow1 = init_shadow(workspace)
        shadow2 = init_shadow(workspace)
        assert shadow1.git_dir == shadow2.git_dir
        assert shadow1.initialized()

    def test_snapshot_returns_sha(self, workspace, isolated_state_dir):
        shadow = init_shadow(workspace)
        sha = snapshot(shadow, label="test-snap")
        assert sha is not None
        assert len(sha) >= 7

    def test_list_snapshots_after_init(self, workspace, isolated_state_dir):
        shadow = init_shadow(workspace)
        entries = list_snapshots(shadow)
        # init_shadow takes one initial snapshot labelled "initial"
        assert len(entries) >= 1
        assert entries[0][1] == "initial"

    def test_restore_reverts_deletion(self, workspace, isolated_state_dir):
        shadow = init_shadow(workspace)
        sha = snapshot(shadow, label="before-delete")
        assert sha is not None

        (workspace / "hello.txt").unlink()
        assert not (workspace / "hello.txt").exists()

        ok = restore(shadow, sha)
        assert ok
        assert (workspace / "hello.txt").exists()


# ── /snapshot command tests ───────────────────────────────────────────────────


class TestSnapshotCommand:
    def test_registered(self):
        from gptme.commands.base import get_registered_commands

        assert "snapshot" in get_registered_commands()

    def test_list_no_workspace(self, tmp_path, capsys):
        manager = MagicMock()
        manager.workspace = None
        _run_cmd(manager, "list")
        out = capsys.readouterr().out
        assert "no workspace" in out.lower()

    def test_list_uninitialized_workspace(self, workspace, isolated_state_dir, capsys):
        """Snapshot command reports clearly when no snapshot history exists."""
        manager = _make_manager(workspace)
        # Shadow not initialized — workspace dir exists but no shadow repo
        mock_shadow = MagicMock(spec=Shadow)
        mock_shadow.initialized.return_value = False
        with patch(
            "gptme.commands.snapshot.Shadow.for_workspace", return_value=mock_shadow
        ):
            _run_cmd(manager, "list")
        out = capsys.readouterr().out
        assert "no snapshot history" in out.lower()

    def test_list_shows_entries(self, workspace, isolated_state_dir, capsys):
        shadow = init_shadow(workspace)
        snapshot(shadow, label="step-1")
        snapshot(shadow, label="step-2")

        manager = _make_manager(workspace)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, "list")
        out = capsys.readouterr().out
        assert "step-1" in out or "step-2" in out

    def test_list_limit_flag(self, workspace, isolated_state_dir, capsys):
        shadow = init_shadow(workspace)
        for i in range(5):
            snapshot(shadow, label=f"snap-{i}")

        manager = _make_manager(workspace)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, "list --limit 2")
        out = capsys.readouterr().out
        lines = [
            line
            for line in out.splitlines()
            if line.strip() and not line.startswith(("SHA", "-"))
        ]
        # At most 2 snapshot entries
        assert len(lines) <= 2

    def test_restore_missing_sha(self, workspace, isolated_state_dir, capsys):
        shadow = init_shadow(workspace)
        manager = _make_manager(workspace)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, "restore deadbeefdeadbeef")
        out = capsys.readouterr().out
        assert "restore failed" in out.lower()

    def test_restore_valid_sha(self, workspace, isolated_state_dir, capsys):
        shadow = init_shadow(workspace)
        (workspace / "extra.txt").write_text("extra\n")
        sha = snapshot(shadow, label="with-extra")
        assert sha is not None

        (workspace / "extra.txt").unlink()
        assert not (workspace / "extra.txt").exists()

        manager = _make_manager(workspace)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, f"restore {sha}")

        out = capsys.readouterr().out
        assert "restored" in out.lower()
        assert (workspace / "extra.txt").exists()

    def test_help_no_args(self, workspace, isolated_state_dir, capsys):
        manager = _make_manager(workspace)
        _run_cmd(manager, "")
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_unknown_subcommand(self, workspace, isolated_state_dir, capsys):
        manager = _make_manager(workspace)
        _run_cmd(manager, "frobnitz")
        out = capsys.readouterr().out
        assert "unknown subcommand" in out.lower()

    def test_diff_no_changes(self, workspace, isolated_state_dir, capsys):
        shadow = init_shadow(workspace)
        sha = snapshot(shadow, label="current")
        assert sha is not None

        manager = _make_manager(workspace)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, f"diff {sha}")
        out = capsys.readouterr().out
        # Either empty or explicit "no changes" message
        assert "no changes" in out.lower() or out.strip() == ""

    def test_restore_requires_one_arg(self, workspace, isolated_state_dir, capsys):
        manager = _make_manager(workspace)
        _run_cmd(manager, "restore")
        out = capsys.readouterr().out
        assert "restore requires" in out.lower()

    def test_diff_requires_one_arg(self, workspace, isolated_state_dir, capsys):
        manager = _make_manager(workspace)
        _run_cmd(manager, "diff")
        out = capsys.readouterr().out
        assert "diff requires" in out.lower()

    def test_create_explicit_snapshot(self, workspace, isolated_state_dir, capsys):
        """Agent can explicitly record a named snapshot before a risky attempt."""
        shadow = init_shadow(workspace)
        manager = _make_manager(workspace)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, "create before-attempt")
        out = capsys.readouterr().out
        assert "snapshot recorded" in out.lower()
        assert "before-attempt" in out

    def test_create_default_label(self, workspace, isolated_state_dir, capsys):
        """Create with no label uses 'manual' as default."""
        shadow = init_shadow(workspace)
        manager = _make_manager(workspace)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, "create")
        out = capsys.readouterr().out
        assert "snapshot recorded" in out.lower()
        assert "manual" in out

    def test_create_then_restore_roundtrip(self, workspace, isolated_state_dir, capsys):
        """Full tree-search round-trip: create snapshot, mutate, restore."""
        shadow = init_shadow(workspace)
        manager = _make_manager(workspace)

        # Step 1: record state before attempt
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, "create before-attempt")
        out = capsys.readouterr().out
        # Extract SHA from output like "Snapshot recorded: abc1234  (before-attempt)"
        sha = out.split(":")[1].strip().split()[0] if ":" in out else None
        assert sha is not None, f"Could not extract SHA from: {out!r}"

        # Step 2: mutate workspace (simulate a failed attempt)
        (workspace / "bad_change.txt").write_text("oops\n")
        assert (workspace / "bad_change.txt").exists()

        # Step 3: restore to before-attempt
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, f"restore {sha}")
        capsys.readouterr()  # consume

        # Mutation should be gone
        assert not (workspace / "bad_change.txt").exists()
