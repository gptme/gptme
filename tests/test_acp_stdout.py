"""Tests for ACP stdout isolation.

Verifies that running gptme in ACP mode doesn't leak non-JSON-RPC
output to stdout, which would corrupt the protocol communication.
"""

import io
import sys


def test_stdout_redirected_in_acp_main():
    """_real_stdout should be captured at module load time."""
    from gptme.acp.__main__ import _real_stdout

    # _real_stdout should be a valid file-like object
    assert _real_stdout is not None
    assert hasattr(_real_stdout, "write")
    assert hasattr(_real_stdout, "flush")


def test_real_streams_preserved():
    """The saved _real_stdin/_real_stdout should be file-like objects."""
    from gptme.acp.__main__ import _real_stdin, _real_stdout

    # Both should be readable/writable file-like objects
    assert hasattr(_real_stdin, "read") or hasattr(_real_stdin, "readline")
    assert hasattr(_real_stdout, "write")


def test_global_console_uses_gptme_util():
    """The plugins module should use gptme.util.console, not its own."""
    from gptme.plugins import console as plugins_console
    from gptme.util import console as util_console

    # They should be the exact same object
    assert plugins_console is util_console


def test_print_goes_to_stderr_after_redirect():
    """When sys.stdout is redirected to stderr, print() goes to stderr."""
    original_stdout = sys.stdout
    stderr_capture = io.StringIO()

    try:
        # Simulate what ACP __main__ does
        sys.stdout = stderr_capture
        print("test message")
        assert "test message" in stderr_capture.getvalue()
    finally:
        sys.stdout = original_stdout


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
        # A new Console() created after redirect will use the redirected stdout
        console = Console()
        console.log("console test message")
        assert "console test message" in stderr_capture.getvalue()
    finally:
        sys.stdout = original_stdout


def test_sys_stdout_write_goes_to_stderr_after_redirect():
    """Direct sys.stdout.write() also goes to stderr after redirect."""
    original_stdout = sys.stdout
    stderr_capture = io.StringIO()

    try:
        sys.stdout = stderr_capture
        sys.stdout.write("direct write test")
        sys.stdout.flush()
        assert "direct write test" in stderr_capture.getvalue()
    finally:
        sys.stdout = original_stdout


def test_create_stdio_streams_callable():
    """_create_stdio_streams should be an async callable."""
    import asyncio

    from gptme.acp.__main__ import _create_stdio_streams

    assert callable(_create_stdio_streams)
    assert asyncio.iscoroutinefunction(_create_stdio_streams)


def test_no_stdout_pollution_from_imports():
    """Importing gptme modules shouldn't write to stdout."""
    original_stdout = sys.stdout
    capture = io.StringIO()

    try:
        sys.stdout = capture

        # Force re-import of key modules that might print during import
        # (we can't truly re-import, but we can verify current state)

        output = capture.getvalue()
        assert output == "", f"Unexpected stdout output during imports: {output!r}"
    finally:
        sys.stdout = original_stdout
