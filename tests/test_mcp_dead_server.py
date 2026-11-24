"""A stdio MCP server that dies between calls must fail fast, not hang.

Once the server process exits, the SDK's reader task is gone, so a request sent
afterwards waits forever for a response unless the client notices the closed
transport and raises a connection error the auto-restart path acts on.
"""

import sys
import textwrap
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import anyio
import psutil
import pytest

from gptme.config import Config, MCPConfig, MCPServerConfig, UserConfig
from gptme.mcp.client import MCPClient, _is_connection_error
from gptme.tools.mcp_adapter import _call_mcp_tool_with_retry

SERVER_SRC = textwrap.dedent(
    """
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("tiny")


    @app.tool()
    def echo(text: str) -> str:
        return f"echo:{text}"


    if __name__ == "__main__":
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
    srv = MCPServerConfig(name="tiny", command=sys.executable, args=[str(server)])
    client = MCPClient(
        config=Config(user=UserConfig(mcp=MCPConfig(enabled=True, servers=[srv])))
    )
    client.connect("tiny")
    try:
        assert client.call_tool("echo", {"text": "1"}) == "echo:1"

        # The server crashes (OOM, segfault, idle exit) between two calls.
        victims = [
            p
            for p in psutil.Process().children(recursive=True)
            if str(server) in " ".join(p.cmdline())
        ]
        assert victims, "stdio server process not found"
        for p in victims:
            p.kill()
        psutil.wait_procs(victims, timeout=5)
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
