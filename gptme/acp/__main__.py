#!/usr/bin/env python
"""Run gptme as an ACP agent.

This is the entry point for running gptme as an ACP-compatible agent.
It can be invoked as:

    python -m gptme.acp

Or via the CLI:

    gptme --acp
"""

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)

# Save real stdout/stdin before any redirection.
# ACP JSON-RPC communication needs the original stdout (fd 1),
# while all other output (Rich, print, logging) must go to stderr.
_real_stdin = sys.stdin
_real_stdout = sys.stdout


async def _create_stdio_streams() -> (
    tuple["asyncio.StreamReader", "asyncio.StreamWriter"]
):
    """Create asyncio streams from the real stdin/stdout file objects.

    Must be called after sys.stdout has been redirected to stderr,
    using the saved _real_stdin/_real_stdout references.
    """
    loop = asyncio.get_running_loop()

    # Reader from real stdin
    reader = asyncio.StreamReader()
    reader_protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: reader_protocol, _real_stdin)

    # Writer to real stdout using a drain-capable protocol
    write_protocol = _DrainProtocol()
    transport, _ = await loop.connect_write_pipe(lambda: write_protocol, _real_stdout)
    writer = asyncio.StreamWriter(transport, write_protocol, None, loop)

    return reader, writer


class _DrainProtocol(asyncio.BaseProtocol):
    """Minimal protocol providing drain support for pipe writes."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._paused = False
        self._drain_waiter: asyncio.Future[None] | None = None

    def pause_writing(self) -> None:  # type: ignore[override]
        self._paused = True
        if self._drain_waiter is None:
            self._drain_waiter = self._loop.create_future()

    def resume_writing(self) -> None:  # type: ignore[override]
        self._paused = False
        if self._drain_waiter is not None and not self._drain_waiter.done():
            self._drain_waiter.set_result(None)
        self._drain_waiter = None

    async def _drain_helper(self) -> None:
        if self._paused and self._drain_waiter is not None:
            await self._drain_waiter


async def _run_acp() -> None:
    """Create streams from real stdout and run the ACP agent."""
    from acp import run_agent  # type: ignore[import-not-found]

    from .agent import GptmeAgent

    reader, writer = await _create_stdio_streams()
    # run_agent params use client perspective:
    #   input_stream = writer (agent writes to client's input)
    #   output_stream = reader (agent reads from client's output)
    await run_agent(GptmeAgent(), input_stream=writer, output_stream=reader)


def main() -> int:
    """Run the gptme ACP agent."""
    # Configure logging to stderr (stdout is reserved for JSON-RPC)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    # Redirect sys.stdout to stderr globally.
    # This is the nuclear option: any print(), rprint(), console.log(),
    # sys.stdout.write() etc. from gptme or its dependencies will go to
    # stderr instead of corrupting the JSON-RPC stream on stdout.
    # The ACP transport uses _real_stdout (saved above) via explicit streams.
    sys.stdout = sys.stderr

    try:
        import acp  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        logger.error(
            "agent-client-protocol package not installed.\n"
            "Install with: pip install agent-client-protocol"
        )
        return 1

    logger.info("Starting gptme ACP agent...")

    try:
        asyncio.run(_run_acp())
        return 0
    except KeyboardInterrupt:
        logger.info("Agent stopped by user")
        return 0
    except Exception as e:
        logger.exception(f"Agent error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
