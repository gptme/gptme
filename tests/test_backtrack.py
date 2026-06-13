"""Tests for /backtrack conversation checkpointing (issue #523)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import gptme.commands  # noqa: F401 – ensures all commands are registered
from gptme.commands.base import handle_cmd
from gptme.logmanager.conv_checkpoints import (
    list_conv_checkpoints,
    resolve_conv_checkpoint,
    save_conv_checkpoint,
)
from gptme.message import Message

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_log(n: int) -> list[Message]:
    """Build a simple log of n alternating user/assistant messages."""
    from typing import Literal, cast  # fmt: skip

    Role = Literal["user", "assistant"]
    msgs = []
    for i in range(n):
        role = cast(Role, "user" if i % 2 == 0 else "assistant")
        msgs.append(Message(role, f"msg {i}"))
    return msgs


def _make_manager(tmp_path: Path, n_messages: int = 6) -> MagicMock:
    manager = MagicMock()
    manager.logdir = tmp_path
    messages = _make_log(n_messages)
    manager.log = MagicMock()
    manager.log.messages = messages

    # edit() replaces the log in place (simplified)
    def _edit(new_msgs):
        if isinstance(new_msgs, list):
            manager.log.messages = new_msgs
        else:
            manager.log.messages = list(new_msgs)

    manager.edit.side_effect = _edit
    return manager


# ── ConvCheckpoint primitives ─────────────────────────────────────────────────


class TestConvCheckpointPrimitives:
    def test_save_and_list(self, tmp_path):
        r = save_conv_checkpoint(tmp_path, index=4, label="before-patch")
        assert r.index == 4
        assert r.label == "before-patch"

        records = list_conv_checkpoints(tmp_path)
        assert len(records) == 1
        assert records[0] == r

    def test_multiple_checkpoints(self, tmp_path):
        save_conv_checkpoint(tmp_path, 2, "start")
        save_conv_checkpoint(tmp_path, 6, "mid")
        records = list_conv_checkpoints(tmp_path)
        assert [r.label for r in records] == ["start", "mid"]

    def test_empty_list(self, tmp_path):
        assert list_conv_checkpoints(tmp_path) == []

    def test_sidecar_appends(self, tmp_path):
        save_conv_checkpoint(tmp_path, 1, "a")
        save_conv_checkpoint(tmp_path, 2, "b")
        lines = (tmp_path / "conv-checkpoints.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2

    def test_resolve_by_label(self, tmp_path):
        save_conv_checkpoint(tmp_path, 4, "mymark")
        r = resolve_conv_checkpoint(tmp_path, "mymark")
        assert r.index == 4
        assert r.label == "mymark"

    def test_resolve_by_integer(self, tmp_path):
        r = resolve_conv_checkpoint(tmp_path, "3")
        assert r.index == 3
        assert r.label == "@3"

    def test_resolve_missing_label(self, tmp_path):
        with pytest.raises(KeyError, match="no checkpoint named"):
            resolve_conv_checkpoint(tmp_path, "nonexistent")

    def test_resolve_last_label_wins(self, tmp_path):
        save_conv_checkpoint(tmp_path, 2, "foo")
        save_conv_checkpoint(tmp_path, 8, "foo")  # same label, updated position
        r = resolve_conv_checkpoint(tmp_path, "foo")
        assert r.index == 8


# ── /backtrack command ────────────────────────────────────────────────────────


class TestBacktrackCommand:
    def test_mark_no_label(self, tmp_path, capsys):
        manager = _make_manager(tmp_path, n_messages=6)
        list(handle_cmd("/backtrack mark", manager))
        out = capsys.readouterr().out
        assert "cp1" in out
        assert "index 6" in out
        records = list_conv_checkpoints(tmp_path)
        assert len(records) == 1
        assert records[0].index == 6

    def test_mark_with_label(self, tmp_path, capsys):
        manager = _make_manager(tmp_path, n_messages=4)
        list(handle_cmd("/backtrack mark my-label", manager))
        out = capsys.readouterr().out
        assert "my-label" in out
        records = list_conv_checkpoints(tmp_path)
        assert records[0].label == "my-label"
        assert records[0].index == 4

    def test_mark_duplicate_label_warns(self, tmp_path, capsys):
        manager = _make_manager(tmp_path, n_messages=4)
        list(handle_cmd("/backtrack mark dup", manager))
        capsys.readouterr()  # clear first mark output
        manager = _make_manager(tmp_path, n_messages=6)
        list(handle_cmd("/backtrack mark dup", manager))
        out = capsys.readouterr().out
        assert "Warning" in out
        assert "dup" in out
        # Both records should be saved
        records = list_conv_checkpoints(tmp_path)
        assert len(records) == 2

    def test_list_empty(self, tmp_path, capsys):
        manager = _make_manager(tmp_path)
        list(handle_cmd("/backtrack list", manager))
        out = capsys.readouterr().out
        assert "No checkpoints" in out

    def test_list_shows_records(self, tmp_path, capsys):
        save_conv_checkpoint(tmp_path, 4, "start")
        manager = _make_manager(tmp_path)
        list(handle_cmd("/backtrack list", manager))
        out = capsys.readouterr().out
        assert "start" in out
        assert "4" in out

    def test_rewind_by_index(self, tmp_path):
        manager = _make_manager(tmp_path, n_messages=6)
        msgs = list(handle_cmd("/backtrack 3", manager))
        # Log should be truncated to 3 messages
        assert len(manager.log.messages) == 3
        # A summary system message should be yielded
        assert any(m.role == "system" for m in msgs)

    def test_rewind_by_label(self, tmp_path):
        save_conv_checkpoint(tmp_path, 2, "before-patch")
        manager = _make_manager(tmp_path, n_messages=6)
        msgs = list(handle_cmd("/backtrack before-patch", manager))
        assert len(manager.log.messages) == 2
        assert any("before-patch" in m.content for m in msgs if m.role == "system")

    def test_rewind_injects_reason(self, tmp_path):
        manager = _make_manager(tmp_path, n_messages=6)
        msgs = list(
            handle_cmd(
                '/backtrack 2 --reason "Patch hunk not found; re-read the file."',
                manager,
            )
        )
        system_msgs = [m for m in msgs if m.role == "system"]
        assert system_msgs
        assert "Patch hunk not found" in system_msgs[0].content

    def test_rewind_saves_backup_branch(self, tmp_path):
        manager = _make_manager(tmp_path, n_messages=6)
        list(handle_cmd("/backtrack 2", manager))
        # edit() should have been called once
        assert manager.edit.called

    def test_rewind_to_same_index_is_noop(self, tmp_path, capsys):
        manager = _make_manager(tmp_path, n_messages=4)
        list(handle_cmd("/backtrack 4", manager))
        out = capsys.readouterr().out
        assert "nothing to rewind" in out
        # Log unchanged
        assert len(manager.log.messages) == 4

    def test_rewind_beyond_length_errors(self, tmp_path, capsys):
        manager = _make_manager(tmp_path, n_messages=4)
        list(handle_cmd("/backtrack 99", manager))
        out = capsys.readouterr().out
        assert "beyond current" in out

    def test_help_subcommand(self, tmp_path, capsys):
        manager = _make_manager(tmp_path)
        list(handle_cmd("/backtrack help", manager))
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_no_args_prints_usage(self, tmp_path, capsys):
        manager = _make_manager(tmp_path)
        list(handle_cmd("/backtrack", manager))
        out = capsys.readouterr().out
        assert "Usage" in out
