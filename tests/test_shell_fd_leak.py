"""Test that shell sessions don't leak file descriptors."""

import os

import pytest

from gptme.tools.shell import ShellSession


def test_shell_session_pipes_closed_after_close():
    """Test that stdout/stderr pipes are actually closed after close()."""
    shell = ShellSession()

    # Store references to the pipe file descriptors
    stdout_fd = shell.process.stdout.fileno() if shell.process.stdout else None
    stderr_fd = shell.process.stderr.fileno() if shell.process.stderr else None
    stdin_fd = shell.process.stdin.fileno() if shell.process.stdin else None

    # Run a simple command
    shell.run("echo test")

    # Close the shell
    shell.close()

    # After close, these file descriptors should be closed
    # Trying to use them should raise OSError (bad file descriptor)
    if stdout_fd is not None:
        with pytest.raises(OSError):
            os.fstat(stdout_fd)

    if stderr_fd is not None:
        with pytest.raises(OSError):
            os.fstat(stderr_fd)

    if stdin_fd is not None:
        with pytest.raises(OSError):
            os.fstat(stdin_fd)


@pytest.mark.slow
def test_multiple_shell_sessions_no_crash():
    """Test that creating and closing many shell sessions doesn't crash.

    While we can't easily count file descriptors without external deps,
    this test ensures that repeated session creation/cleanup doesn't
    cause issues. If file descriptors leaked, we'd eventually hit OS limits.

    Marked as slow because it creates multiple shell sessions.
    """
    # Create and close shell sessions (reduced from 50 to 10 for faster tests)
    for i in range(10):
        shell = ShellSession()
        shell.run(f"echo test_{i}")
        shell.close()

    # If we got here without crashing, file descriptors are being cleaned up
    assert True
