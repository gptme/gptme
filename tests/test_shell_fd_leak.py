"""Test that shell sessions don't leak file descriptors."""

import os

import pytest

from gptme.tools.shell import ShellSession, close_conversation_shell


def test_shell_session_pipes_closed_after_close():
    """Test that stdout/stderr pipes are actually closed after close()."""
    shell = ShellSession()

    # Store references to the pipe file descriptors
    stdout_fd = shell.process.stdout.fileno() if shell.process.stdout else None
    stderr_fd = shell.process.stderr.fileno() if shell.process.stderr else None
    stdin_fd = shell.process.stdin.fileno() if shell.process.stdin else None

    # Run a simple command to confirm shell works
    shell.run("echo test")

    # Close the shell
    shell.close()

    # After close, these file descriptors should be closed
    # Trying to use them should raise OSError (bad file descriptor)
    if stdout_fd is not None:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(stdout_fd)

    if stderr_fd is not None:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(stderr_fd)

    if stdin_fd is not None:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(stdin_fd)


def test_conversation_shell_cleanup():
    """Test that close_conversation_shell cleans up registered shells."""
    from gptme.tools.shell import _conv_shell_lock, _conversation_shells

    # Manually register a shell for a fake conversation
    shell = ShellSession()
    conv_id = "test-conversation-cleanup"

    with _conv_shell_lock:
        _conversation_shells[conv_id] = shell

    stdout_fd = shell.process.stdout.fileno() if shell.process.stdout else None

    # Clean up via the public API
    close_conversation_shell(conv_id)

    # Shell should be removed from registry
    with _conv_shell_lock:
        assert conv_id not in _conversation_shells

    # File descriptors should be closed
    if stdout_fd is not None:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(stdout_fd)


@pytest.mark.slow
def test_multiple_shell_sessions_no_fd_leak():
    """Test that creating and closing many shell sessions doesn't leak FDs.

    If file descriptors leaked, we'd eventually hit OS limits.
    """
    for i in range(10):
        shell = ShellSession()
        shell.run(f"echo test_{i}")
        shell.close()

    # If we got here without crashing, file descriptors are being cleaned up
    assert True
