"""A stdio MCP server that dies between calls must fail fast, not hang.

Once the server process exits, the SDK's reader task is gone, so a request sent
afterwards waits forever for a response unless the client notices the closed
transport and raises a connection error the auto-restart path acts on.
"""

import asyncio
import os
import signal
import sys
import textwrap
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import anyio
import pytest

from gptme.config import Config, MCPConfig, MCPServerConfig, UserConfig
from gptme.mcp.client import MCPClient, _is_connection_error
from gptme.tools.mcp_adapter import _call_mcp_tool_with_retry

SERVER_SRC = textwrap.dedent(
    """
    import os
    import sys

    from mcp.server.fastmcp import FastMCP

    app = FastMCP("tiny")


    @app.tool()
    def echo(text: str) -> str:
        return f"echo:{text}"


    if __name__ == "__main__":
        with open(sys.argv[1], "w") as f:
            f.write(str(os.getpid()))
        app.run("stdio")
    """
)


@pytest.mark.parametrize(
    "exc",
    [anyio.ClosedResourceError(), anyio.BrokenResourceError(), anyio.EndOfStream()],
)
def test_closed_transport_stream_is_a_connection_error(exc):
    assert _is_connection_error(exc) is True


def test_closed_transport_stream_triggers_restart():
    dead = MagicMock()
    dead.call_tool.side_effect = anyio.ClosedResourceError()
    fresh = MagicMock()
    fresh.call_tool.return_value = "ok"
    clients: dict[str, Any] = {"srv": dead}
    with patch("gptme.mcp.client.MCPClient", return_value=fresh):
        result = _call_mcp_tool_with_retry(
            "srv", "echo", {}, MagicMock(), clients=clients
        )
    assert result == "ok"
    assert clients["srv"] is fresh


@pytest.mark.timeout(60)
def test_call_after_server_death_fails_fast(tmp_path):
    server = tmp_path / "tiny_server.py"
    server.write_text(SERVER_SRC)
    pid_file = tmp_path / "server.pid"
    srv = MCPServerConfig(
        name="tiny", command=sys.executable, args=[str(server), str(pid_file)]
    )
    client = MCPClient(
        config=Config(user=UserConfig(mcp=MCPConfig(enabled=True, servers=[srv])))
    )
    client.connect("tiny")
    try:
        assert client.call_tool("echo", {"text": "1"}) == "echo:1"

        # The server crashes (OOM, segfault, idle exit) between two calls, while
        # the client's event loop is stopped: the EOF is not processed yet.
        pid = int(pid_file.read_text())
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        if hasattr(os, "waitid"):
            # Block until it has exited, without reaping it: the client's
            # child watcher still owns the exit status.
            try:
                os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
            except ChildProcessError:
                # asyncio's watcher thread (Python 3.11) reaped it first:
                # the server has already exited.
                pass
        else:
            time.sleep(0.5)

        outcome: dict = {}

        def _call():
            try:
                outcome["result"] = client.call_tool("echo", {"text": "2"})
            except BaseException as e:
                outcome["error"] = e

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout=15)
        assert not t.is_alive(), "call_tool() on a dead stdio server hung"
        assert "error" in outcome, outcome
        assert _is_connection_error(outcome["error"]), outcome["error"]
    finally:
        client.close()


class _FakeStream:
    def __init__(self, open_streams: int):
        self.open_streams = open_streams

    def statistics(self):
        return MagicMock(open_send_streams=self.open_streams)


def _bare_client(stream: _FakeStream) -> MCPClient:
    client = MCPClient.__new__(MCPClient)
    client._read_stream = stream
    return client


def test_server_dead_before_send_is_retryable():
    sent = []

    async def request():
        sent.append(True)

    client = _bare_client(_FakeStream(0))
    with pytest.raises(ConnectionError) as info:
        anyio.run(client._unless_transport_closed, request())
    assert _is_connection_error(info.value)
    assert sent == []


def test_cancel_before_send_closes_the_request():
    """A cancel during the pre-send drain must not leak the unsent coroutine."""
    stream = _FakeStream(1)

    async def request():
        pass

    async def scenario():
        coro = request()
        client = _bare_client(stream)
        task = asyncio.ensure_future(client._unless_transport_closed(coro))
        await asyncio.sleep(0)  # task is now inside the drain
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return coro

    coro = asyncio.run(scenario())
    assert coro.cr_frame is None  # closed, so no "never awaited" warning


def test_server_dying_mid_call_is_not_retried():
    """The tool may already have run: the retry path must not repeat it."""
    stream = _FakeStream(1)

    async def request():
        stream.open_streams = 0  # server exits after receiving the request
        await anyio.sleep(10)

    client = _bare_client(stream)
    with pytest.raises(RuntimeError) as info:
        anyio.run(client._unless_transport_closed, request())
    assert not _is_connection_error(info.value)
    assert "result is unknown" in str(info.value)


def test_eof_pending_from_between_calls_is_seen_before_send():
    """An EOF queued while the loop was stopped must count as died-before-send.

    The reader closes the stream only once the loop runs again. If the request
    were sent first, the close would read as "died mid-call" and the safe
    restart would be skipped.
    """
    sent = []

    async def request():
        sent.append(True)
        await anyio.sleep(10)

    async def scenario():
        writer, reader = anyio.create_memory_object_stream[object](0)
        client = MCPClient.__new__(MCPClient)
        client._read_stream = reader

        async def reader_sees_eof():
            # fd readiness, StreamReader wakeup, then the writer closes
            for _ in range(3):
                await anyio.sleep(0)
            writer.close()

        async with anyio.create_task_group() as tg:
            tg.start_soon(reader_sees_eof)
            await anyio.sleep(0)  # the reader task is scheduled, not yet run
            try:
                await client._unless_transport_closed(request())
            except Exception as e:
                return e
            finally:
                reader.close()
        return None

    error = anyio.run(scenario)
    assert isinstance(error, ConnectionError), error
    assert _is_connection_error(error)
    assert sent == []
