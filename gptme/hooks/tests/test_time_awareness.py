"""Tests for time_awareness hook."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from gptme.hooks.time_awareness import (
    _conversation_start_times_var,
    _get_next_milestone,
    _shown_milestones_var,
    add_time_message,
)
from gptme.message import Message


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.fixture(autouse=True)
def reset_contextvars():
    """Reset context vars between tests."""
    tok1 = _conversation_start_times_var.set(None)
    tok2 = _shown_milestones_var.set(None)
    yield
    _conversation_start_times_var.reset(tok1)
    _shown_milestones_var.reset(tok2)


def _call_add_time_message(
    workspace: Path | None = None,
) -> list[Message]:
    """Helper to call add_time_message with a dummy log, returning only Messages."""
    # The hook doesn't use the log parameter, so we pass a sentinel
    results = list(
        add_time_message(log=cast(Any, None), workspace=workspace, tool_use=None)
    )
    return [r for r in results if isinstance(r, Message)]


class TestGetNextMilestone:
    """Tests for _get_next_milestone helper."""

    def test_under_one_minute(self) -> None:
        assert _get_next_milestone(0) is None

    def test_one_minute(self) -> None:
        assert _get_next_milestone(1) == 1
        assert _get_next_milestone(4) == 1

    def test_five_minutes(self) -> None:
        assert _get_next_milestone(5) == 5
        assert _get_next_milestone(9) == 5

    def test_ten_minutes(self) -> None:
        assert _get_next_milestone(10) == 10
        assert _get_next_milestone(14) == 10

    def test_fifteen_minutes(self) -> None:
        assert _get_next_milestone(15) == 15
        assert _get_next_milestone(19) == 15

    def test_twenty_minutes(self) -> None:
        assert _get_next_milestone(20) == 20
        assert _get_next_milestone(29) == 20

    def test_every_ten_after_twenty(self) -> None:
        assert _get_next_milestone(30) == 30
        assert _get_next_milestone(35) == 30
        assert _get_next_milestone(40) == 40
        assert _get_next_milestone(59) == 50
        assert _get_next_milestone(60) == 60


class TestAddTimeMessage:
    """Tests for add_time_message hook."""

    def test_no_workspace_returns_nothing(self) -> None:
        msgs = _call_add_time_message(workspace=None)
        assert len(msgs) == 0

    def test_first_call_initializes_no_message(self, workspace: Path) -> None:
        msgs = _call_add_time_message(workspace=workspace)
        assert len(msgs) == 0

        # Verify state was initialized
        start_times = _conversation_start_times_var.get()
        assert start_times is not None
        assert str(workspace) in start_times

    def test_message_at_one_minute(self, workspace: Path) -> None:
        _call_add_time_message(workspace=workspace)

        # Fast-forward time by 2 minutes
        start_times = _conversation_start_times_var.get()
        assert start_times is not None
        start_times[str(workspace)] = datetime.now(tz=timezone.utc) - timedelta(
            minutes=2
        )
        _conversation_start_times_var.set(start_times)

        msgs = _call_add_time_message(workspace=workspace)
        assert len(msgs) == 1
        assert msgs[0].role == "system"
        assert "Time elapsed" in msgs[0].content
        assert "2min" in msgs[0].content

    def test_no_duplicate_milestone(self, workspace: Path) -> None:
        _call_add_time_message(workspace=workspace)

        start_times = _conversation_start_times_var.get()
        assert start_times is not None
        start_times[str(workspace)] = datetime.now(tz=timezone.utc) - timedelta(
            minutes=2
        )
        _conversation_start_times_var.set(start_times)

        # First call at ~2min shows the 1-min milestone
        msgs1 = _call_add_time_message(workspace=workspace)
        assert len(msgs1) == 1

        # Second call (still in same range) should NOT repeat
        msgs2 = _call_add_time_message(workspace=workspace)
        assert len(msgs2) == 0

    def test_shows_hours_format(self, workspace: Path) -> None:
        _call_add_time_message(workspace=workspace)

        start_times = _conversation_start_times_var.get()
        assert start_times is not None
        start_times[str(workspace)] = datetime.now(tz=timezone.utc) - timedelta(
            minutes=65
        )
        _conversation_start_times_var.set(start_times)

        msgs = _call_add_time_message(workspace=workspace)
        assert len(msgs) == 1
        assert "1h 5min" in msgs[0].content

    def test_message_is_hidden(self, workspace: Path) -> None:
        _call_add_time_message(workspace=workspace)

        start_times = _conversation_start_times_var.get()
        assert start_times is not None
        start_times[str(workspace)] = datetime.now(tz=timezone.utc) - timedelta(
            minutes=6
        )
        _conversation_start_times_var.set(start_times)

        msgs = _call_add_time_message(workspace=workspace)
        assert len(msgs) == 1
        assert msgs[0].hide is True
