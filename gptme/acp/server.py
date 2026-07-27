"""
ACP (Agent Client Protocol) server for gptme.

Handles JSON-RPC 2.0 communication over stdio, implementing the
Agent Client Protocol so gptme can be launched by agent registries
(Zed, JetBrains, etc.) via `uvx gptme-acp`.
"""

import json
import logging
import sys
import threading
from pathlib import Path

from ..__version__ import __version__
from ..chat import chat
from ..config import ChatConfig, Config, set_config
from ..dirs import get_logs_dir
from ..llm.models import get_default_model
from ..logmanager import LogManager, prepare_messages
from ..message import Message
from ..tools import ToolFormat, get_available_tools, init_tools

logger = logging.getLogger(__name__)

# ACP protocol version
ACP_PROTOCOL_VERSION = 1


class ACPProtocolHandler:
    """Handles ACP JSON-RPC 2.0 messages over stdio."""

    def __init__(self):
        self._request_id = 0
        self._lock = threading.Lock()
        self._running = True

    def _next_id(self):
        with self._lock:
            self._request_id += 1
            return self._request_id

    def _send(self, message: dict):
        """Send a JSON-RPC message to stdout."""
        line = json.dumps(message) + "\n"
        sys.stdout.write(line)
        sys.stdout.flush()

    def _send_response(self, id: int, result=None, error=None):
        """Send a JSON-RPC response."""
        msg = {"jsonrpc": "2.0", "id": id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        self._send(msg)

    def _send_error(self, id: int, code: int, message: str, data=None):
        """Send a JSON-RPC error response."""
        err = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._send_response(id, error=err)

    def handle_initialize(self, params: dict, id: int):
        """Handle the initialize request."""
        protocol_version = params.get("protocolVersion", 1)
        client_capabilities = params.get("clientCapabilities", {})

        if protocol_version != ACP_PROTOCOL_VERSION:
            self._send_error(
                id,
                -32602,
                f"Unsupported protocol version: {protocol_version}. "
                f"Expected {ACP_PROTOCOL_VERSION}.",
            )
            return

        result = {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "agentInfo": {
                "name": "gptme",
                "title": "gptme ACP Agent",
                "version": __version__,
            },
            "capabilities": {
                "tools": True,
                "prompts": True,
            },
        }
        self._send_response(id, result=result)

    def handle_list_tools(self, params: dict, id: int):
        """Handle the tools/list request — return available tools."""
        tools = get_available_tools()
        result_tools = []
        for t in tools:
            if t.is_available:
                result_tools.append(
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                        },
                    }
                )
        self._send_response(id, result={"tools": result_tools})

    def handle_call_tool(self, params: dict, id: int):
        """Handle the tools/call request — execute a tool."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        tools = get_available_tools()
        tool_obj = next((t for t in tools if t.name == tool_name), None)

        if tool_obj is None:
            self._send_error(
                id, -32603, f"Unknown tool: {tool_name}"
            )
            return

        try:
            # Build a minimal ToolUse-like object
            from ..tools import ToolUse

            tooluse = ToolUse(
                tool=tool_name,
                args=arguments,
                content=arguments.get("content", ""),
                start=0,
            )

            outputs = list(tooluse.execute(lambda _: True))
            output_text = "\n".join(
                o.content for o in outputs if o.content
            )

            self._send_response(
                id,
                result={
                    "content": [
                        {
                            "type": "text",
                            "text": output_text or "Tool executed successfully.",
                        }
                    ]
                },
            )
        except Exception as e:
            logger.exception(f"Error executing tool {tool_name}: {e}")
            self._send_error(
                id, -32603, f"Tool execution failed: {str(e)}"
            )

    def handle_list_prompts(self, params: dict, id: int):
        """Handle the prompts/list request."""
        self._send_response(id, result={"prompts": []})

    def handle_get_prompt(self, params: dict, id: int):
        """Handle the prompts/get request."""
        self._send_response(
            id,
            result={
                "messages": [
                    {
                        "role": "assistant",
                        "content": {
                            "type": "text",
                            "text": "gptme is ready. How can I help?",
                        },
                    }
                ]
            },
        )

    def handle_message(self, message: dict):
        """Dispatch a JSON-RPC message to the appropriate handler."""
        method = message.get("method", "")
        id = message.get("id")

        if id is None:
            # Notifications without id — ignore
            return

        handlers = {
            "initialize": self.handle_initialize,
            "tools/list": self.handle_list_tools,
            "tools/call": self.handle_call_tool,
            "prompts/list": self.handle_list_prompts,
            "prompts/get": self.handle_get_prompt,
        }

        handler = handlers.get(method)
        if handler:
            params = message.get("params", {})
            handler(params, id)
        else:
            self._send_error(
                id, -32601, f"Method not found: {method}"
            )

    def run(self):
        """Read JSON-RPC messages from stdin and respond."""
        logger.info("ACP server started, waiting for messages on stdin")
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                    self.handle_message(message)
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON from client: {e}")
        except KeyboardInterrupt:
            logger.info("ACP server shutting down")
        except Exception as e:
            logger.exception(f"ACP server error: {e}")


def run_server():
    """Entry point for the gptme-acp command."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    handler = ACPProtocolHandler()
    handler.run()