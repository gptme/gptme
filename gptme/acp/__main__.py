#!/usr/bin/env python
"""Run gptme as an ACP agent.

This is the entry point for running gptme as an ACP-compatible agent.
It can be invoked as:

    python -m gptme.acp

Or via the CLI:

    gptme --acp

After ``_capture_stdio_transport()``, fd 1 is redirected to stderr via
``os.dup2(2, 1)``.  The only way to write to the real stdout is through
the file objects returned by that function.  This is intentional —
JSON-RPC owns stdout.
"""

import asyncio
import logging
import os
import sys
from typing import IO

logger = logging.getLogger(__name__)


class _WritePipeProtocol(asyncio.BaseProtocol):
    """Protocol for write pipes that supports ``StreamWriter.drain()``.

    ``asyncio.BaseProtocol`` lacks ``_drain_helper()``, so using it directly
    with ``StreamWriter`` causes ``AttributeError`` when backpressure triggers
    ``drain()``.  This implementation handles pause/resume writing and exposes
    the ``_drain_helper()`` coroutine that ``StreamWriter`` expects.

    Adapted from the agent-client-protocol library's own ``_WritePipeProtocol``.
    """

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._paused = False
        self._drain_waiter: asyncio.Future[None] | None = None
        self._connection_lost = False

    def pause_writing(self) -> None:
        self._paused = True
        if self._drain_waiter is None:
            self._drain_waiter = self._loop.create_future()

    def resume_writing(self) -> None:
        self._paused = False
        if self._drain_waiter is not None and not self._drain_waiter.done():
            self._drain_waiter.set_result(None)
        self._drain_waiter = None

    def connection_lost(self, exc: Exception | None) -> None:
        self._connection_lost = True
        if self._drain_waiter is not None and not self._drain_waiter.done():
            if exc is None:
                self._drain_waiter.set_result(None)
            else:
                self._drain_waiter.set_exception(exc)

    async def _drain_helper(self) -> None:
        if self._connection_lost:
            raise ConnectionResetError("Connection lost")
        if self._paused and self._drain_waiter is not None:
            await self._drain_waiter


def _capture_stdio_transport() -> tuple[IO[bytes], IO[bytes]]:
    """Capture real stdin/stdout fds, then redirect fd 1 to fd 2 at the OS level.

    After this call:
    - The returned (stdin_file, stdout_file) are the ONLY way to talk to the
      real stdin/stdout (i.e., the JSON-RPC channel).
    - fd 1 now points to stderr, so print(), rprint(), Console(),
      sys.stdout.write(), and even C extensions writing to fd 1 all go to stderr.
    - No monkey-patching, no import-order sensitivity.
    """
    # 1. Duplicate the real fds before we clobber them
    real_stdin_fd = os.dup(0)
    real_stdout_fd = os.dup(1)

    # 2. Point fd 1 (stdout) at fd 2 (stderr) — OS level, bulletproof
    os.dup2(2, 1)

    # 3. Rebuild Python's sys.stdout on the now-redirected fd 1
    #    so even sys.stdout.write() goes to stderr
    sys.stdout = open(1, "w", buffering=1, closefd=False)

    # 4. Return raw binary file objects for the JSON-RPC transport
    real_stdin = os.fdopen(real_stdin_fd, "rb", buffering=0)
    real_stdout = os.fdopen(real_stdout_fd, "wb", buffering=0)

    return real_stdin, real_stdout


async def _run_acp(real_stdin: IO[bytes], real_stdout: IO[bytes]) -> None:
    """Run the ACP agent using the captured stdio fds for JSON-RPC.

    All gptme imports happen here — after fd redirect is in place —
    so config loading, console init, etc. all write to stderr safely.
    """
    loop = asyncio.get_running_loop()

    # Create asyncio streams from the real fds
    reader = asyncio.StreamReader()
    reader_protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: reader_protocol, real_stdin)

    write_protocol = _WritePipeProtocol()
    write_transport, _ = await loop.connect_write_pipe(
        lambda: write_protocol, real_stdout
    )
    writer = asyncio.StreamWriter(write_transport, write_protocol, reader, loop)

    # NOW safe to import gptme (triggers config loading, console.log, etc.)
    from acp import run_agent  # type: ignore[import-not-found]

    from .agent import GptmeAgent

    # run_agent params use client perspective:
    #   input_stream = writer (agent writes to client's input)
    #   output_stream = reader (agent reads from client's output)
    await run_agent(GptmeAgent(), input_stream=writer, output_stream=reader)  # type: ignore[arg-type]


def main() -> int:
    """Run the gptme ACP agent."""
    # === FIRST: capture fds before ANY gptme imports ===
    real_stdin, real_stdout = _capture_stdio_transport()

    # Logging goes to stderr (fd 2, untouched)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    try:
        import acp  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        logger.error(
            "agent-client-protocol package not installed.\n"
            "Install with: pip install agent-client-protocol"
        )
        return 1

    try:
        import importlib.metadata

        _version = importlib.metadata.version("gptme")
    except Exception:
        _version = "unknown"
    logger.info("Starting gptme ACP agent (v%s)...", _version)

    try:
        asyncio.run(_run_acp(real_stdin, real_stdout))
        return 0
    except KeyboardInterrupt:
        logger.info("Agent stopped by user")
        return 0
    except Exception as e:
        logger.exception(f"Agent error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
