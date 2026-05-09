"""@server MCP namespace hint hook.

Detects @server_name references in user messages and injects tool hints
so the model knows which MCP tools are available for each server without
memorizing the flat tool-name namespace.

Gemini CLI feature: `@github list my open PRs` → model gets a tool-use hint.
"""

import re
from collections.abc import Generator
from logging import getLogger

from gptme.message import Message

from . import HookType, StopPropagation, register_hook

logger = getLogger(__name__)

# Match @name patterns — server names are alphanumeric + hyphens
# Match @name patterns — server names are alphanumeric + hyphens.
# Negative lookbehind avoids false positives on email addresses (user@github.com).
_MCP_HINT_RE = re.compile(r"(?<!\w)@([\w][\w-]*)")


def mcp_namespace_hint(
    messages: list[Message],
    **kwargs,
) -> Generator[Message | StopPropagation, None, None]:
    """Scan the last user message for @server_name and inject MCP tool hints.

    When a user writes ``@github list my issues``, this hook detects ``github``
    as an MCP server reference and appends a system message listing the
    available tools on that server so the model can pick the right one.

    Only fires when:
    - The last user message contains ``@name`` references
    - At least one reference matches a loaded MCP server
    - The matched server has tools available
    """
    # Find the last user message
    last_user: Message | None = None
    for msg in reversed(messages):
        if msg.role == "user":
            last_user = msg
            break

    if not last_user or not last_user.content:
        return

    # Find @server_name references
    refs = _MCP_HINT_RE.findall(last_user.content)
    if not refs:
        return

    # Get loaded MCP servers and their tool listings
    from gptme.tools.mcp_adapter import get_mcp_clients

    all_clients = get_mcp_clients()
    if not all_clients:
        return

    # Match references to loaded servers (deduplicate via dict)
    matched: dict[str, list[object]] = {}
    for ref in refs:
        if ref in all_clients:
            client = all_clients[ref]
            tools = getattr(client, "tools", None)
            if tools is not None:
                # tools is ListToolsResult; .tools gives list[Tool]
                tool_list = getattr(tools, "tools", [])
                if tool_list:
                    matched[ref] = tool_list

    if not matched:
        return

    # Build a compact hint message
    lines: list[str] = []
    for server_name, tools in matched.items():
        total = len(tools)
        lines.append(
            f"\nMCP tool hint for @{server_name}: {total} available tool(s). "
            f"Use the full tool name (e.g. ``{server_name}.tool_name``).\n"
        )
        for tool in tools:
            desc = getattr(tool, "description", "") or ""
            name = getattr(tool, "name", "?")
            desc_line = f"- `{server_name}.{name}`" + (f": {desc}" if desc else "")
            lines.append(desc_line)
        lines.append("")

    hint = "\n".join(lines)
    logger.debug(f"Injected MCP namespace hint for {list(matched.keys())}")
    yield Message("system", hint)


def register() -> None:
    register_hook("mcp_namespace_hint", HookType.GENERATION_PRE, mcp_namespace_hint)
