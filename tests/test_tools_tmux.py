"""Tests for the tmux tool."""

import os
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


def _get_worker_id() -> str:
    """Get a unique ID for this pytest worker to avoid session collisions."""
    # PYTEST_XDIST_WORKER is set to 'gw0', 'gw1', etc. in parallel runs
    return os.environ.get("PYTEST_XDIST_WORKER", "main")


def _create_test_session(command: str, worker_id: str, session_num: int = 1) -> str:
    """Create a tmux session with worker-unique name for testing.

    Returns the session ID.
    """
    session_id = f"gptme_{worker_id}_{session_num}"

    # Create session with bash
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_id, "bash"],
        check=True,
        capture_output=True,
    )

    # Set session size
    subprocess.run(
        ["tmux", "resize-window", "-t", session_id, "-x", "120", "-y", "40"],
        check=True,
        capture_output=True,
    )

    # Send the command
    subprocess.run(
        ["tmux", "send-keys", "-t", session_id, command, "Enter"],
        check=True,
        capture_output=True,
    )

    return session_id


@pytest.fixture
def worker_id():
    """Fixture that provides the current pytest worker ID."""
    return _get_worker_id()


@pytest.fixture(autouse=True)
def cleanup_sessions(worker_id):
    """Clean up worker-specific test sessions before and after each test.
    
    Only cleans up sessions with worker prefix (gptme_{worker_id}_*) to avoid
    race conditions in parallel test runs. Tests that use new_session() directly
    should use the cleanup_new_session_sessions fixture instead.
    """

    def cleanup():
        for session in get_sessions():
            # Clean up sessions with our worker prefix (for parallel test isolation)
            if session.startswith(f"gptme_{worker_id}_"):
                subprocess.run(
                    ["tmux", "kill-session", "-t", session],
                    capture_output=True,
                )

    cleanup()
    yield
    cleanup()


@pytest.fixture
def cleanup_new_session_sessions():
    """Clean up gptme_N sessions for tests that use new_session() directly.
    
    This fixture is NOT autouse - only apply to specific tests that need it.
    Tests using this should also use @pytest.mark.xdist_group("tmux_new_session")
    to ensure they run serially and avoid race conditions with other workers.
    """
    import re

    def cleanup():
        for session in get_sessions():
            if session.startswith("gptme_"):
                # Match gptme_N where N is just digits (not gptme_gw0_*, etc.)
                if re.fullmatch(r"gptme_\d+", session):
                    subprocess.run(
                        ["tmux", "kill-session", "-t", session],
                        capture_output=True,
                    )

    cleanup()
    yield
    cleanup()


class TestGetSessions:
    """Tests for get_sessions function."""

    def test_empty_when_no_sessions(self, worker_id):
        """Should return empty list when no tmux sessions exist."""
        # After cleanup, our worker's sessions should be empty
        sessions = get_sessions()
        worker_sessions = [s for s in sessions if s.startswith(f"gptme_{worker_id}_")]
        assert len(worker_sessions) == 0


@pytest.mark.xdist_group("tmux_new_session")
class TestNewSession:
    """Tests for new_session function.
    
    These tests use new_session() directly which creates gptme_N sessions.
    They are grouped to run serially to avoid race conditions.
    """

    def test_creates_session(self, cleanup_new_session_sessions):
        """Should create a new tmux session."""
        msg = new_session("echo 'hello world'")
        assert "gptme_" in msg.content
        assert "hello world" in msg.content or "Running" in msg.content

    def test_increments_session_id(self, cleanup_new_session_sessions):
        """Should create sessions with incrementing IDs."""
        msg1 = new_session("echo 'first'")
        msg2 = new_session("echo 'second'")
        # Note: IDs depend on existing sessions, so just verify both were created
        assert "gptme_" in msg1.content
        assert "gptme_" in msg2.content
        # Verify they got different session IDs
        import re

        id1 = re.search(r"gptme_(\d+)", msg1.content)
        id2 = re.search(r"gptme_(\d+)", msg2.content)
        assert id1 and id2
        assert id1.group(1) != id2.group(1)


class TestListSessions:
    """Tests for list_sessions function."""

    def test_lists_created_sessions(self, worker_id):
        """Should list sessions that were created.

        Uses worker-isolated session to avoid race conditions in parallel tests.
        """
        # Create a worker-isolated session to avoid race conditions
        # where other workers' cleanup might remove our session
        session_id = _create_test_session("echo 'test'", worker_id)

        msg = list_sessions()
        assert session_id in msg.content


class TestKillSession:
    """Tests for kill_session function.
    
    Uses worker-isolated sessions to avoid race conditions in parallel tests.
    """

    def test_kills_session(self, worker_id):
        """Should kill an existing session."""
        # Use worker-isolated session to avoid race conditions
        session_id = _create_test_session("echo 'to kill'", worker_id, 1)

        msg = kill_session(session_id)
        assert "Killed" in msg.content

        # Verify session is gone
        sessions = get_sessions()
        assert session_id not in sessions


class TestWaitForOutput:
    """Tests for wait_for_output function."""

    def test_waits_for_quick_command(self, worker_id):
        """Should return quickly for a fast command."""
        session_id = _create_test_session("echo 'done'", worker_id, 1)
        time.sleep(2.0)  # Let command complete (extra time for CI)

        start = time.time()
        msg = wait_for_output(session_id, timeout=15, stable_time=2)
        elapsed = time.time() - start

        assert "stabilized" in msg.content
        assert "done" in msg.content
        assert elapsed < 15  # Should complete before timeout

    def test_timeout_for_ongoing_command(self, worker_id):
        """Should timeout for a command that keeps producing output."""
        # Use a command that produces continuous output
        session_id = _create_test_session(
            "while true; do echo tick; sleep 0.5; done", worker_id, 1
        )

        start = time.time()
        msg = wait_for_output(session_id, timeout=4, stable_time=2)
        elapsed = time.time() - start

        assert "timed out" in msg.content
        assert elapsed >= 4  # Should have waited for timeout

    @pytest.mark.xdist_group("tmux_new_session")
    def test_auto_prefixes_session_id(self, cleanup_new_session_sessions):
        """Should automatically add gptme_ prefix if missing.
        
        Uses new_session() directly, so grouped with other new_session tests.
        """
        # This test verifies the prefix behavior of wait_for_output
        # We use new_session here because we need a gptme_N style session
        msg = new_session("echo 'prefix test'")
        time.sleep(2.0)  # Extra time for CI

        # Extract the numeric ID
        import re

        match = re.search(r"gptme_(\d+)", msg.content)
        assert match
        numeric_id = match.group(1)

        # Call without prefix - should auto-add gptme_
        msg = wait_for_output(numeric_id, timeout=10, stable_time=2)
        assert f"gptme_{numeric_id}" in msg.content

    def test_returns_output_content(self, worker_id):
        """Should include the pane output in the message."""
        session_id = _create_test_session("echo 'specific output marker'", worker_id, 1)
        time.sleep(2.0)  # Extra time for CI

        msg = wait_for_output(session_id, timeout=15, stable_time=2)
        assert "specific output marker" in msg.content


class TestCapturePaneInternal:
    """Tests for _capture_pane internal function."""

    def test_captures_output(self, worker_id):
        """Should capture pane content including scrollback."""
        session_id = _create_test_session("echo 'capture test'", worker_id, 1)
        time.sleep(2.0)  # Extra time for CI

        output = _capture_pane(session_id)
        assert "capture test" in output
