"""Tests for cwd_tracking hook."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.hooks.cwd_tracking import (
    _cwd_before_var,
    track_cwd_post_execute,
    track_cwd_pre_execute,
)
from gptme.logmanager import Log
from gptme.message import Message


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def empty_log() -> Log:
    """Create an empty Log for testing."""
    return Log()


@pytest.fixture(autouse=True)
def reset_contextvar():
    """Reset the cwd ContextVar between tests."""
    token = _cwd_before_var.set(None)
    yield
    _cwd_before_var.reset(token)


class TestPreExecute:
    """Tests for track_cwd_pre_execute."""

    def test_stores_current_cwd(self, workspace: Path, empty_log: Log) -> None:
        cwd = os.getcwd()
        list(track_cwd_pre_execute(log=empty_log, workspace=workspace, tool_use=None))
        assert _cwd_before_var.get() == cwd

    def test_yields_no_messages(self, workspace: Path, empty_log: Log) -> None:
        msgs = list(
            track_cwd_pre_execute(log=empty_log, workspace=workspace, tool_use=None)
        )
        assert len(msgs) == 0

    def test_handles_getcwd_error(self, workspace: Path, empty_log: Log) -> None:
        with patch("os.getcwd", side_effect=OSError("dir deleted")):
            # Should not raise
            msgs = list(
                track_cwd_pre_execute(log=empty_log, workspace=workspace, tool_use=None)
            )
            assert len(msgs) == 0


class TestPostExecute:
    """Tests for track_cwd_post_execute."""

    def test_no_message_when_cwd_unchanged(
        self, workspace: Path, empty_log: Log
    ) -> None:
        _cwd_before_var.set(os.getcwd())
        msgs = list(
            track_cwd_post_execute(log=empty_log, workspace=workspace, tool_use=None)
        )
        assert len(msgs) == 0

    def test_message_when_cwd_changes(
        self, workspace: Path, tmp_path: Path, empty_log: Log
    ) -> None:
        new_dir = tmp_path / "subdir"
        new_dir.mkdir()

        original = os.getcwd()
        _cwd_before_var.set(original)

        os.chdir(new_dir)
        try:
            msgs = list(
                track_cwd_post_execute(
                    log=empty_log, workspace=workspace, tool_use=None
                )
            )
            assert len(msgs) == 1
            msg = msgs[0]
            assert isinstance(msg, Message)
            assert msg.role == "system"
            assert str(new_dir) in msg.content
            assert "Working directory changed" in msg.content
        finally:
            os.chdir(original)

    def test_no_message_when_no_stored_cwd(
        self, workspace: Path, empty_log: Log
    ) -> None:
        msgs = list(
            track_cwd_post_execute(log=empty_log, workspace=workspace, tool_use=None)
        )
        assert len(msgs) == 0

    def test_handles_getcwd_error_gracefully(
        self, workspace: Path, empty_log: Log
    ) -> None:
        _cwd_before_var.set("/some/path")
        with patch("os.getcwd", side_effect=OSError("dir deleted")):
            msgs = list(
                track_cwd_post_execute(
                    log=empty_log, workspace=workspace, tool_use=None
                )
            )
            assert len(msgs) == 0
