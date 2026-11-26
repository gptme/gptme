"""Tool and settings arguments shared by the Claude Code eval launchers."""

import logging

logger = logging.getLogger(__name__)

# Claude Code requires Read before editing or overwriting an existing file.
_TOOL_MAP = {
    "shell": ("Bash",),
    "read": ("Read", "Glob", "Grep"),
    "save": ("Read", "Write"),
    "append": ("Read", "Edit", "Write"),
    "patch": ("Read", "Edit"),
    "morph": ("Read", "Edit"),
    "browser": ("WebFetch", "WebSearch"),
}


def map_tools_to_claude_code(tools: list[str]) -> list[str]:
    """Translate gptme tool names and preserve native names and input order."""
    mapped: list[str] = []
    for tool in tools:
        if tool in _TOOL_MAP:
            mapped.extend(_TOOL_MAP[tool])
        elif tool.isidentifier() and tool[0].isupper():
            mapped.append(tool)
        else:
            logger.warning("No Claude Code equivalent for eval tool %r", tool)
    return list(dict.fromkeys(mapped))


def claude_code_tool_args(
    tools: list[str] | None, include_user_context: bool
) -> list[str]:
    """Build CLI arguments for the eval's tool list and context preference."""
    if tools is None and include_user_context:
        return []

    # --tools limits built-ins. MCP servers contribute their own tools.
    args = ["--strict-mcp-config"]
    if not include_user_context:
        args.extend(["--setting-sources", ""])

    if tools is None:
        # Print mode cannot ask for approval. With host settings excluded,
        # pre-approve the built-ins that correspond to gptme's tools.
        allowed = dict.fromkeys(name for names in _TOOL_MAP.values() for name in names)
        args.extend(["--allowedTools", ",".join(allowed)])
    else:
        selected = ",".join(map_tools_to_claude_code(tools))
        args.extend(["--tools", selected])
        if selected:
            args.extend(["--allowedTools", selected])
    return args
