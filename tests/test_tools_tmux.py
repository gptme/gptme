"""Tests for the tmux tool."""

import shutil
import subprocess
import time

import pytest

from gptme.tools.tmux import (
    _capture_pane,
    get_sessions,
    kill_session,
    list_sessions,
    new_session,
    wait_for_output,
)

# Skip all tests if tmux is not available
pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux not available"
)


@pytest.fixture(autouse=True)
def cleanup_sessions():
    """Clean up any test sessions before and after each test."""
    # Cleanup before test
    for session in get_sessions():
        if session.startswith("gptme_"):
            subprocess.run(
                ["tmux", "kill-session", "-t", session],
                capture_output=True,
            )
    yield
    # Cleanup after test
    for session in get_sessions():
        if session.startswith("gptme_"):
            subprocess.run(
                ["tmux", "kill-session", "-t", session],
                capture_output=True,
            )


class TestGetSessions:
    """Tests for get_sessions function."""

    def test_empty_when_no_sessions(self):
        """Should return empty list when no tmux sessions exist."""
        # After cleanup, gptme sessions should be empty
        sessions = get_sessions()
        gptme_sessions = [s for s in sessions if s.startswith("gptme_")]
        assert len(gptme_sessions) == 0


class TestNewSession:
    """Tests for new_session function."""

    def test_creates_session(self):
        """Should create a new tmux session."""
        msg = new_session("echo 'hello world'")
        assert "gptme_" in msg.content
        assert "hello world" in msg.content or "Running" in msg.content

    def test_increments_session_id(self):
        """Should create sessions with incrementing IDs."""
        msg1 = new_session("echo 'first'")
        msg2 = new_session("echo 'second'")
        assert "gptme_1" in msg1.content
        assert "gptme_2" in msg2.content


class TestListSessions:
    """Tests for list_sessions function."""

    def test_lists_created_sessions(self):
        """Should list sessions that were created."""
        new_session("echo 'test'")
        msg = list_sessions()
        assert "gptme_1" in msg.content


class TestKillSession:
    """Tests for kill_session function."""

    def test_kills_session(self):
        """Should kill an existing session."""
        new_session("echo 'to kill'")
        msg = kill_session("gptme_1")
        assert "Killed" in msg.content

        # Verify session is gone
        sessions = get_sessions()
        assert "gptme_1" not in sessions


class TestWaitForOutput:
    """Tests for wait_for_output function."""

    def test_waits_for_quick_command(self):
        """Should return quickly for a fast command."""
        new_session("echo 'done'")
        time.sleep(0.5)  # Let command complete

        start = time.time()
        msg = wait_for_output("gptme_1", timeout=10, stable_time=2)
        elapsed = time.time() - start

        assert "stabilized" in msg.content
        assert "done" in msg.content
        assert elapsed < 10  # Should complete before timeout

    def test_timeout_for_ongoing_command(self):
        """Should timeout for a command that keeps producing output."""
        # Use a command that produces continuous output
        new_session("while true; do echo tick; sleep 0.5; done")

        start = time.time()
        msg = wait_for_output("gptme_1", timeout=3, stable_time=2)
        elapsed = time.time() - start

        assert "timed out" in msg.content
        assert elapsed >= 3  # Should have waited for timeout

    def test_auto_prefixes_session_id(self):
        """Should automatically add gptme_ prefix if missing."""
        new_session("echo 'prefix test'")
        time.sleep(0.5)

        # Call without prefix
        msg = wait_for_output("1", timeout=5, stable_time=2)
        assert "gptme_1" in msg.content

    def test_returns_output_content(self):
        """Should include the pane output in the message."""
        new_session("echo 'specific output marker'")
        time.sleep(0.5)

        msg = wait_for_output("gptme_1", timeout=10, stable_time=2)
        assert "specific output marker" in msg.content


class TestCapturePaneInternal:
    """Tests for _capture_pane internal function."""

    def test_captures_output(self):
        """Should capture pane content."""
        new_session("echo 'capture test'")
        time.sleep(0.5)

        output = _capture_pane("gptme_1")
        assert "capture test" in output
