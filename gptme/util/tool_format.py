"""
Shared tool formatting utilities.

Design Goals (documented for LLMs and contributors):

1. **Human-readable**: Easy to scan quickly
   - Consistent alignment and spacing
   - Status icons (✓/✗) for quick visual parsing
   - Truncated descriptions for overview

2. **Agent-friendly**: Parseable, consistent structure
   - Markdown headers for sections (## Instructions, ## Examples)
   - Consistent field names (Status:, Tokens:)
   - No variable formatting between tools

3. **Context-efficient**: No unnecessary verbosity
   - Compact mode for token-constrained contexts
   - Options to omit examples/tokens when not needed
   - Descriptions truncated to 50 chars in list view

4. **Progressive disclosure**: Summary first, details on demand
   - List shows name + short description
   - Info command shows full details
   - Hints guide users to more detailed commands

These formatters are used by both:
- /tools command (in-session)
- gptme-util tools (CLI utility)

The unified format ensures consistency across:
- CLI help output
- In-session commands
- Generated system prompts
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..tools.base import ToolSpec


def format_tool_summary(tool: "ToolSpec", show_status: bool = True) -> str:
    """Format a single tool as a one-line summary.

    Args:
        tool: The tool to format
        show_status: Whether to show availability status icon

    Returns:
        A single line like: "✓ shell        Execute shell commands"
    """
    status = ""
    if show_status:
        status = "✓ " if tool.is_available else "✗ "

    # Truncate description to keep it scannable
    desc = tool.desc.rstrip(".")
    if len(desc) > 50:
        desc = desc[:47] + "..."

    return f"{status}{tool.name:<12} {desc}"


def format_tools_list(
    tools: list["ToolSpec"],
    show_all: bool = False,
    show_status: bool = True,
    compact: bool = False,
) -> str:
    """Format a list of tools for display.

    Args:
        tools: List of tools to format
        show_all: Include unavailable tools
        show_status: Show ✓/✗ status icons
        compact: Use more compact format

    Returns:
        Formatted multi-line string
    """
    filtered = [t for t in tools if show_all or t.is_available]
    available_count = sum(1 for t in tools if t.is_available)

    lines = []
    if compact:
        lines.append(f"Tools [{available_count} available]:")
    else:
        lines.append(f"Available tools ({available_count}/{len(tools)}):")
        lines.append("")

    for tool in sorted(filtered, key=lambda t: t.name):
        prefix = " " if compact else "  "
        lines.append(prefix + format_tool_summary(tool, show_status))

    if not compact:
        lines.append("")
        lines.append(
            "Run '/tools <name>' or 'gptme-util tools info <name>' for details"
        )

    return "\n".join(lines)


def format_tool_info(
    tool: "ToolSpec",
    include_examples: bool = True,
    include_tokens: bool = True,
) -> str:
    """Format detailed tool information.

    Args:
        tool: The tool to format
        include_examples: Include example usage
        include_tokens: Show token estimates

    Returns:
        Formatted multi-line string with full tool details
    """
    lines = []

    # Header
    lines.append(f"# {tool.name}")
    lines.append("")
    lines.append(tool.desc)
    lines.append("")

    # Status line
    status = "✓ available" if tool.is_available else "✗ not available"
    lines.append(f"Status: {status}")

    # Token estimates if requested
    if include_tokens:
        from ..message import len_tokens  # fmt: skip

        instr_tokens = (
            len_tokens(tool.instructions, "gpt-4") if tool.instructions else 0
        )
        example_tokens = (
            len_tokens(tool.get_examples(), "gpt-4") if tool.get_examples() else 0
        )
        lines.append(
            f"Tokens: ~{instr_tokens} (instructions) + ~{example_tokens} (examples)"
        )

    lines.append("")

    # Instructions
    if tool.instructions:
        lines.append("## Instructions")
        lines.append("")
        lines.append(tool.instructions.strip())
        lines.append("")

    # Examples
    if include_examples and tool.get_examples():
        lines.append("## Examples")
        lines.append("")
        lines.append(tool.get_examples().strip())

    return "\n".join(lines)


def format_langtags(tools: list["ToolSpec"]) -> str:
    """Format available language tags for code blocks.

    Returns:
        Formatted list of supported language tags
    """
    lines = ["Supported language tags:"]
    for tool in sorted(tools, key=lambda t: t.name):
        if tool.block_types:
            primary = tool.block_types[0]
            aliases = tool.block_types[1:]
            alias_str = f"  (aliases: {', '.join(aliases)})" if aliases else ""
            lines.append(f"  - {primary}{alias_str}")
    return "\n".join(lines)
