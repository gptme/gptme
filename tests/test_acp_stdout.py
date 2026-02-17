"""Tests for ACP stdout isolation.

Verifies that running gptme in ACP mode doesn't leak non-JSON-RPC
output to stdout, which would corrupt the protocol communication.
"""

import io
import os
import sys


def test_capture_stdio_transport():
    """_capture_stdio_transport redirects fd 1 to stderr at OS level."""
    from gptme.acp.__main__ import _capture_stdio_transport

    # Save original state
    original_stdout_fd = os.dup(1)
    original_sys_stdout = sys.stdout

    try:
        real_stdin, real_stdout = _capture_stdio_transport()

        # real_stdout should be a writable binary file object
        assert hasattr(real_stdout, "write")
        assert real_stdout.mode == "rb" or hasattr(real_stdout, "writable")

        # real_stdin should be a readable binary file object
        assert hasattr(real_stdin, "read")

        # fd 1 should now point to the same place as fd 2 (stderr)
        # Writing to fd 1 should go to stderr, not to the real stdout
        assert os.fstat(1).st_ino == os.fstat(2).st_ino

        # sys.stdout should be rebuilt on the redirected fd 1
        assert sys.stdout is not original_sys_stdout

        real_stdin.close()
        real_stdout.close()
    finally:
        # Restore original fd 1
        os.dup2(original_stdout_fd, 1)
        os.close(original_stdout_fd)
        sys.stdout = original_sys_stdout


def test_fd_redirect_catches_print():
    """After fd redirect, print() goes to stderr, not real stdout."""
    original_stdout_fd = os.dup(1)
    original_sys_stdout = sys.stdout

    try:
        # Create a pipe to capture what goes to stderr (fd 2)
        r_fd, w_fd = os.pipe()

        # Redirect fd 1 to write end of pipe (simulating os.dup2(2, 1))
        os.dup2(w_fd, 1)
        os.close(w_fd)
        sys.stdout = open(1, "w", buffering=1, closefd=False)  # noqa: SIM115

        print("test message", flush=True)

        # Read from pipe
        os.set_blocking(r_fd, False)
        data = os.read(r_fd, 4096)
        os.close(r_fd)

        assert b"test message" in data
    finally:
        os.dup2(original_stdout_fd, 1)
        os.close(original_stdout_fd)
        sys.stdout = original_sys_stdout


def test_global_console_uses_gptme_util():
    """The plugins module should use gptme.util.console, not its own."""
    from gptme.plugins import console as plugins_console
    from gptme.util import console as util_console

    # They should be the exact same object
    assert plugins_console is util_console


def test_rich_print_goes_to_stderr_after_redirect():
    """When sys.stdout is redirected, rich.print also goes to stderr."""
    from rich import print as rprint

    original_stdout = sys.stdout
    stderr_capture = io.StringIO()

    try:
        sys.stdout = stderr_capture
        rprint("rich test message")
        assert "rich test message" in stderr_capture.getvalue()
    finally:
        sys.stdout = original_stdout


def test_console_log_goes_to_stderr_after_redirect():
    """When sys.stdout is redirected, Console().log() goes to stderr."""
    from rich.console import Console

    original_stdout = sys.stdout
    stderr_capture = io.StringIO()

    try:
        sys.stdout = stderr_capture
        console = Console()
        console.log("console test message")
        assert "console test message" in stderr_capture.getvalue()
    finally:
        sys.stdout = original_stdout


def test_no_stdout_pollution_from_imports():
    """Importing gptme modules shouldn't write to stdout.

    Exercises key modules that use Rich console, logging, or print()
    to verify they don't write to stdout during normal import/usage.
    """
    import importlib

    original_stdout = sys.stdout
    capture = io.StringIO()

    try:
        sys.stdout = capture

        # Re-import modules known to use console/print at import or init time
        importlib.reload(importlib.import_module("gptme.util"))
        importlib.reload(importlib.import_module("gptme.config"))

        output = capture.getvalue()
        assert output == "", f"Unexpected stdout output during imports: {output!r}"
    finally:
        sys.stdout = original_stdout
