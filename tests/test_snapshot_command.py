"""Tests for the /snapshot command (#495 — agents that tree search)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

import gptme.commands  # noqa: F401 – ensures all commands are registered
from gptme.commands.base import handle_cmd
from gptme.workspace_snapshot import (
    Shadow,
    get_snapshot_n_msgs,
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


def _make_manager(workspace: Path, n_msgs: int | None = None) -> MagicMock:
    manager = MagicMock()
    manager.workspace = str(workspace)
    if n_msgs is not None:
        manager.log = [MagicMock(role="user")] * n_msgs
    else:
        manager.log = MagicMock()
        manager.log.__len__ = MagicMock(return_value=0)
        manager.log.__iter__ = MagicMock(return_value=iter([]))
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

    def test_create_no_workspace_prints_single_error(self, capsys):
        """Create without a workspace reports the error once."""
        manager = MagicMock()
        manager.workspace = None
        _run_cmd(manager, "create before-attempt")
        out = capsys.readouterr().out
        assert out.lower().count("no workspace configured") == 1

    def test_create_initializes_uninitialized_workspace(
        self, workspace, isolated_state_dir, capsys
    ):
        """First explicit create initializes the shadow without spurious warnings."""
        manager = _make_manager(workspace)
        _run_cmd(manager, "create before-attempt")
        out = capsys.readouterr().out
        assert "snapshot recorded" in out.lower()
        assert "no snapshot history" not in out.lower()

    def test_create_then_restore_roundtrip(self, workspace, isolated_state_dir, capsys):
        """Full tree-search round-trip: create snapshot, mutate, restore."""
        shadow = init_shadow(workspace)
        manager = _make_manager(workspace)

        # Step 1: record state before attempt
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, "create before-attempt")
        out = capsys.readouterr().out
        # Extract SHA from output like "Snapshot recorded: abc1234  (before-attempt)"
        match = re.search(r"Snapshot recorded:\s+(\S+)", out)
        sha = match.group(1) if match else None
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


# ── conversation-summary tests ────────────────────────────────────────────────


class TestSnapshotConversationSummary:
    """Tests for the conversation-message summary embedded in snapshots."""

    def test_get_snapshot_n_msgs_absent(self, workspace, isolated_state_dir):
        """Old snapshots without n_msgs metadata return None."""
        shadow = init_shadow(workspace)
        sha = snapshot(shadow, label="no-meta")
        assert sha is not None
        assert get_snapshot_n_msgs(shadow, sha) is None

    def test_get_snapshot_n_msgs_present(self, workspace, isolated_state_dir):
        """Snapshots created with n_msgs embed and return the count."""
        shadow = init_shadow(workspace)
        sha = snapshot(shadow, label="with-meta", n_msgs=7)
        assert sha is not None
        assert get_snapshot_n_msgs(shadow, sha) == 7

    def test_diff_shows_conversation_summary(
        self, workspace, isolated_state_dir, capsys
    ):
        """diff output includes message count when n_msgs was stored at create time."""
        shadow = init_shadow(workspace)
        # Snapshot at 2 messages.
        sha = snapshot(shadow, label="checkpoint", n_msgs=2)
        assert sha is not None

        # Manager now has 5 messages; 3 added since snapshot (user, assistant, user).
        manager = MagicMock()
        manager.workspace = str(workspace)
        manager.log = [
            MagicMock(role="user"),
            MagicMock(role="assistant"),
            # --- snapshot boundary ---
            MagicMock(role="user"),
            MagicMock(role="assistant"),
            MagicMock(role="user"),
        ]

        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, f"diff {sha}")
        out = capsys.readouterr().out
        assert "+3 messages" in out
        assert "assistant" in out
        assert "user" in out

    def test_diff_no_new_messages(self, workspace, isolated_state_dir, capsys):
        """diff with same message count shows no-new-messages line."""
        shadow = init_shadow(workspace)
        sha = snapshot(shadow, label="checkpoint", n_msgs=4)
        assert sha is not None

        manager = _make_manager(workspace, n_msgs=4)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, f"diff {sha}")
        out = capsys.readouterr().out
        assert "no new messages" in out.lower()

    def test_diff_with_meta_and_clean_workspace_prints_no_changes(
        self, workspace, isolated_state_dir, capsys
    ):
        """diff should still say the workspace is clean when metadata is present."""
        shadow = init_shadow(workspace)
        sha = snapshot(shadow, label="checkpoint", n_msgs=4)
        assert sha is not None

        manager = _make_manager(workspace, n_msgs=4)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, f"diff {sha}")
        out = capsys.readouterr().out
        assert "no changes between current workspace and snapshot" in out.lower()

    def test_diff_without_meta_skips_summary(
        self, workspace, isolated_state_dir, capsys
    ):
        """diff against a legacy snapshot (no n_msgs) shows no summary line."""
        shadow = init_shadow(workspace)
        sha = snapshot(shadow, label="legacy-no-meta")  # no n_msgs
        assert sha is not None

        manager = _make_manager(workspace, n_msgs=4)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, f"diff {sha}")
        out = capsys.readouterr().out
        assert "messages" not in out.lower() or "no changes" in out.lower()

    def test_create_embeds_n_msgs(self, workspace, isolated_state_dir, capsys):
        """create command stores the current message count in the snapshot."""
        shadow = init_shadow(workspace)
        manager = _make_manager(workspace, n_msgs=6)

        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, "create after-setup")
        out = capsys.readouterr().out
        match = re.search(r"Snapshot recorded:\s+(\S+)", out)
        assert match, f"No SHA in output: {out!r}"
        sha = match.group(1)

        # The embedded count should be recoverable.
        assert get_snapshot_n_msgs(shadow, sha) == 6


# ── prune tests ───────────────────────────────────────────────────────────────


class TestSnapshotPrune:
    """Tests that prune() preserves conversation metadata (n_msgs) after
    the retain-and-replay pass so /snapshot diff still shows the summary."""

    def test_prune_includes_n_msgs_in_commit_body(self, workspace, isolated_state_dir):
        """After pruning, metadata-bearing commits keep their n_msgs body line."""
        from gptme.workspace_snapshot import prune

        shadow = init_shadow(workspace)

        # Create a few snapshots, each with a distinct n_msgs.
        shas: list[str] = []
        for i in range(3):
            sha = snapshot(shadow, label=f"gen-{i}", n_msgs=10 + i)
            assert sha is not None
            shas.append(sha)

        # Prune to keep=2 and confirm each kept commit still carries its body.
        pruned = prune(shadow, keep=2)
        assert pruned > 0

        # The oldest (gen-0, n_msgs=10) should be gone; gen-1 and gen-2 survive.
        survivors = [sha for sha, _ in list_snapshots(shadow, limit=10)]
        # At least one snapshot should still have recoverable n_msgs metadata.
        preserved = any(
            get_snapshot_n_msgs(shadow, sha) is not None for sha in survivors
        )
        assert preserved, (
            "After prune, no surviving snapshot has n_msgs metadata — "
            "prune() dropped the commit body"
        )

    def test_prune_keep_entire_body_recovery(self, workspace, isolated_state_dir):
        """Full round-trip: create metadata snapshots, prune, recover n_msgs
        for the survivors via get_snapshot_n_msgs."""
        from gptme.workspace_snapshot import prune

        shadow = init_shadow(workspace)

        sha_before = snapshot(shadow, label="before-prune", n_msgs=42)
        assert sha_before is not None

        # Create enough additional snapshots to trigger pruning.
        for i in range(5):
            snapshot(shadow, label=f"filler-{i}", n_msgs=99)

        pruned = prune(shadow, keep=3)
        assert pruned > 0, "Expected some snapshots to be pruned"

        # The before-prune commit may or may not have survived,
        # but at least one survivor should still report n_msgs=42 or 99.
        survivors = [sha for sha, _ in list_snapshots(shadow, limit=10)]
        recovered = {sha: get_snapshot_n_msgs(shadow, sha) for sha in survivors}
        assert any(v in {42, 99} for v in recovered.values()), (
            f"After prune, no surviving snapshot recovered its original "
            f"n_msgs value. Recovered: {recovered!r}"
        )


class TestSnapshotPruneCommand:
    def test_prune_no_args_defaults_to_30_days(
        self, workspace, isolated_state_dir, capsys
    ):
        """Bare prune should apply the documented 30-day age window."""
        import time

        shadow = init_shadow(workspace)
        for i in range(4):
            (workspace / f"f{i}.txt").write_text(str(i))
            snapshot(shadow, label=f"snap-{i}")

        future = time.time() + 31 * 86400
        manager = _make_manager(workspace)
        with (
            patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow),
            patch("gptme.workspace_snapshot.time.time", return_value=future),
        ):
            _run_cmd(manager, "prune")
        out = capsys.readouterr().out
        assert "pruned" in out.lower()
        remaining = list_snapshots(shadow, limit=50)
        assert len(remaining) == 1

    def test_prune_no_op_when_all_recent(self, workspace, isolated_state_dir, capsys):
        """All snapshots are fresh — nothing pruned, reports 0."""
        shadow = init_shadow(workspace)
        snapshot(shadow, label="fresh")
        manager = _make_manager(workspace)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, "prune --days 30")
        out = capsys.readouterr().out
        assert "no snapshots to prune" in out.lower()

    def test_prune_drops_old_snapshots(self, workspace, isolated_state_dir, capsys):
        """With mocked future time, old snapshots are removed."""
        import time

        shadow = init_shadow(workspace)
        for i in range(4):
            (workspace / f"f{i}.txt").write_text(str(i))
            snapshot(shadow, label=f"snap-{i}")

        future = time.time() + 31 * 86400
        manager = _make_manager(workspace)
        with (
            patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow),
            patch("gptme.workspace_snapshot.time.time", return_value=future),
        ):
            _run_cmd(manager, "prune --days 30")
        out = capsys.readouterr().out
        assert "pruned" in out.lower()
        remaining = list_snapshots(shadow, limit=50)
        assert len(remaining) == 1

    def test_prune_max_entries_flag(self, workspace, isolated_state_dir, capsys):
        """--max-entries keeps only K newest snapshots."""
        shadow = init_shadow(workspace)
        for i in range(8):
            (workspace / f"f{i}.txt").write_text(str(i))
            snapshot(shadow, label=f"snap-{i}")

        manager = _make_manager(workspace)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, "prune --days 3650 --max-entries 3")
        out = capsys.readouterr().out
        assert "pruned" in out.lower()
        remaining = list_snapshots(shadow, limit=50)
        assert len(remaining) == 3

    def test_prune_max_entries_without_days_skips_age_prune(
        self, workspace, isolated_state_dir, capsys
    ):
        """--max-entries alone should not implicitly trigger age pruning."""
        import time

        shadow = init_shadow(workspace)
        for i in range(8):
            (workspace / f"f{i}.txt").write_text(str(i))
            snapshot(shadow, label=f"snap-{i}")

        future = time.time() + 31 * 86400
        manager = _make_manager(workspace)
        with (
            patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow),
            patch("gptme.workspace_snapshot.time.time", return_value=future),
        ):
            _run_cmd(manager, "prune --max-entries 3")
        out = capsys.readouterr().out
        assert "pruned" in out.lower()
        remaining = list_snapshots(shadow, limit=50)
        assert len(remaining) == 3
        assert [label for _, label in remaining] == ["snap-7", "snap-6", "snap-5"]

    def test_prune_unknown_arg(self, workspace, isolated_state_dir, capsys):
        manager = _make_manager(workspace)
        _run_cmd(manager, "prune --invalid-flag")
        out = capsys.readouterr().out
        assert "unknown argument" in out.lower()

    def test_prune_days_missing_value(self, workspace, isolated_state_dir, capsys):
        manager = _make_manager(workspace)
        _run_cmd(manager, "prune --days")
        out = capsys.readouterr().out
        assert "--days requires a value" in out.lower()

    def test_prune_days_non_integer(self, workspace, isolated_state_dir, capsys):
        manager = _make_manager(workspace)
        _run_cmd(manager, "prune --days abc")
        out = capsys.readouterr().out
        assert "--days must be an integer" in out.lower()

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_prune_days_requires_positive_integer(
        self, workspace, isolated_state_dir, capsys, value
    ):
        manager = _make_manager(workspace)
        _run_cmd(manager, f"prune --days {value}")
        out = capsys.readouterr().out
        assert "--days must be a positive integer" in out.lower()

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_prune_max_entries_requires_positive_integer(
        self, workspace, isolated_state_dir, capsys, value
    ):
        manager = _make_manager(workspace)
        _run_cmd(manager, f"prune --max-entries {value}")
        out = capsys.readouterr().out
        assert "--max-entries must be a positive integer" in out.lower()

    def test_prune_dry_run_skips_deletion_by_count(
        self, workspace, isolated_state_dir, capsys
    ):
        """--dry-run reports what would be pruned without removing anything."""
        shadow = init_shadow(workspace)
        for i in range(5):
            (workspace / f"f{i}.txt").write_text(str(i))
            snapshot(shadow, label=f"snap-{i}")

        before = list_snapshots(shadow, limit=50)
        manager = _make_manager(workspace)
        with patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow):
            _run_cmd(manager, "prune --max-entries 3 --dry-run")
        out = capsys.readouterr().out
        assert "would prune" in out.lower()
        # Nothing should have been deleted.
        after = list_snapshots(shadow, limit=50)
        assert len(after) == len(before)

    def test_prune_dry_run_default_age_applied(
        self, workspace, isolated_state_dir, capsys
    ):
        """--dry-run alone previews the default 30-day prune, not a no-op."""
        import time

        shadow = init_shadow(workspace)
        for i in range(3):
            (workspace / f"f{i}.txt").write_text(str(i))
            snapshot(shadow, label=f"snap-{i}")

        future = time.time() + 31 * 86400
        before = list_snapshots(shadow, limit=50)
        manager = _make_manager(workspace)
        with (
            patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow),
            patch("gptme.workspace_snapshot.time.time", return_value=future),
        ):
            _run_cmd(manager, "prune --dry-run")
        out = capsys.readouterr().out
        assert "would prune" in out.lower()
        after = list_snapshots(shadow, limit=50)
        assert len(after) == len(before)

    def test_prune_dry_run_short_flag_skips_deletion_by_age(
        self, workspace, isolated_state_dir, capsys
    ):
        """-n mirrors --dry-run for age-based prune previews."""
        import time

        shadow = init_shadow(workspace)
        for i in range(3):
            (workspace / f"f{i}.txt").write_text(str(i))
            snapshot(shadow, label=f"snap-{i}")

        future = time.time() + 31 * 86400
        before = list_snapshots(shadow, limit=50)
        manager = _make_manager(workspace)
        with (
            patch("gptme.commands.snapshot.Shadow.for_workspace", return_value=shadow),
            patch("gptme.workspace_snapshot.time.time", return_value=future),
        ):
            _run_cmd(manager, "prune -n --days 30")
        out = capsys.readouterr().out
        assert "would prune" in out.lower()
        after = list_snapshots(shadow, limit=50)
        assert len(after) == len(before)
