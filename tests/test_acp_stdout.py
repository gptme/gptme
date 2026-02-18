"""Tests for ACP stdout isolation and write pipe protocol.

Verifies that running gptme in ACP mode doesn't leak non-JSON-RPC
output to stdout, which would corrupt the protocol communication.
Also tests the _WritePipeProtocol used for backpressure handling.
"""

import asyncio
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
        assert real_stdout.mode == "wb"

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


def test_write_pipe_protocol_has_drain_helper():
    """_WritePipeProtocol exposes _drain_helper for StreamWriter.drain()."""

    async def _check():
        from gptme.acp.__main__ import _WritePipeProtocol

        proto = _WritePipeProtocol()
        assert hasattr(proto, "_drain_helper")
        # When not paused, drain should return immediately
        await proto._drain_helper()

    asyncio.run(_check())


def test_write_pipe_protocol_pause_resume():
    """_WritePipeProtocol handles pause/resume writing correctly."""

    async def _check():
        from gptme.acp.__main__ import _WritePipeProtocol

        proto = _WritePipeProtocol()

        # Initially not paused
        assert not proto._paused
        assert proto._drain_waiter is None

        # Pause creates a future
        proto.pause_writing()
        assert proto._paused
        assert proto._drain_waiter is not None
        assert not proto._drain_waiter.done()

        # Resume resolves the future
        proto.resume_writing()
        assert not proto._paused
        assert proto._drain_waiter is None

    asyncio.run(_check())


def test_write_pipe_protocol_drain_blocks_when_paused():
    """_drain_helper blocks until resume_writing is called."""

    async def _check():
        from gptme.acp.__main__ import _WritePipeProtocol

        proto = _WritePipeProtocol()
        proto.pause_writing()

        resumed = False

        async def drain_task():
            await proto._drain_helper()
            return True

        async def resume_task():
            nonlocal resumed
            await asyncio.sleep(0.01)
            proto.resume_writing()
            resumed = True

        # drain should block until resume is called
        results = await asyncio.gather(drain_task(), resume_task())
        assert results[0] is True
        assert resumed

    asyncio.run(_check())


def test_write_pipe_protocol_connection_lost_resolves_drain():
    """connection_lost(None) resolves a blocked _drain_helper()."""

    async def _check():
        from gptme.acp.__main__ import _WritePipeProtocol

        proto = _WritePipeProtocol()
        proto.pause_writing()

        async def drain_task():
            await proto._drain_helper()
            return True

        async def close_task():
            await asyncio.sleep(0.01)
            proto.connection_lost(None)

        results = await asyncio.gather(drain_task(), close_task())
        assert results[0] is True
        assert proto._connection_lost

    asyncio.run(_check())


def test_write_pipe_protocol_connection_lost_with_exception():
    """connection_lost(exc) sets exception on drain waiter."""
    import pytest

    async def _check():
        from gptme.acp.__main__ import _WritePipeProtocol

        proto = _WritePipeProtocol()
        proto.pause_writing()

        async def drain_task():
            await proto._drain_helper()

        async def close_task():
            await asyncio.sleep(0.01)
            proto.connection_lost(OSError("pipe broke"))

        with pytest.raises(OSError, match="pipe broke"):
            await asyncio.gather(drain_task(), close_task())

    asyncio.run(_check())


def test_write_pipe_protocol_drain_after_connection_lost():
    """_drain_helper() raises ConnectionResetError after connection_lost()."""
    import pytest

    async def _check():
        from gptme.acp.__main__ import _WritePipeProtocol

        proto = _WritePipeProtocol()
        proto.connection_lost(None)
        with pytest.raises(ConnectionResetError):
            await proto._drain_helper()

    asyncio.run(_check())
