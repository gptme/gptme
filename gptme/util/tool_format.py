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


def format_tool_summary(
    tool: "ToolSpec", show_status: bool = True, use_color: bool = True
) -> str:
    """Format a single tool as a one-line summary.

    Args:
        tool: The tool to format
        show_status: Whether to show availability status icon
        use_color: Whether to colorize the status icon

    Returns:
        A single line like: "✓ shell        Execute shell commands"
    """
    import click

    status = ""
    if show_status:
        if tool.is_available:
            icon = click.style("✓", fg="green") if use_color else "✓"
        else:
            icon = click.style("✗", fg="red") if use_color else "✗"
        status = f"{icon} "

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
    available = [t for t in tools if t.is_available]
    unavailable = [t for t in tools if not t.is_available]
    available_count = len(available)
    total_count = len(tools)

    lines: list[str] = []
    prefix = " " if compact else "  "

    if show_all and unavailable:
        # Group by availability when showing all
        if compact:
            lines.append(f"Tools [{available_count}/{total_count} available]:")
        else:
            lines.append(f"Available tools ({available_count}):")
            lines.append("")

        lines.extend(
            prefix + format_tool_summary(tool, show_status)
            for tool in sorted(available, key=lambda t: t.name)
        )

        if unavailable:
            lines.append("")
            lines.append(f"Unavailable tools ({len(unavailable)}):")
            lines.append("")
            lines.extend(
                prefix + format_tool_summary(tool, show_status)
                for tool in sorted(unavailable, key=lambda t: t.name)
            )
    else:
        # Only show available (default)
        if compact:
            lines.append(f"Tools [{available_count} available]:")
        else:
            lines.append(f"Available tools ({available_count}):")
            lines.append("")

        lines.extend(
            prefix + format_tool_summary(tool, show_status)
            for tool in sorted(available, key=lambda t: t.name)
        )

        # Hint about unavailable tools
        if unavailable and not show_all:
            unavail_names = ", ".join(sorted(t.name for t in unavailable))
            lines.append("")
            lines.append(f"Unavailable ({len(unavailable)}): {unavail_names}")
            lines.append("Use --all to see details, or install missing dependencies")

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
    truncate: bool = False,
    max_lines: int = 30,
) -> str:
    """Format detailed tool information.

    Args:
        tool: The tool to format
        include_examples: Include example usage
        include_tokens: Show token estimates
        truncate: Truncate long sections (default False for backward compat)
        max_lines: Max lines per section when truncating

    Returns:
        Formatted multi-line string with full tool details
    """
    lines: list[str] = []

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
        instr_lines = tool.instructions.strip().split("\n")
        if truncate and len(instr_lines) > max_lines:
            lines.extend(instr_lines[:max_lines])
            lines.append(f"... ({len(instr_lines) - max_lines} more lines, use -v)")
        else:
            lines.extend(instr_lines)
        lines.append("")

    # Examples
    if include_examples and tool.get_examples():
        lines.append("## Examples")
        lines.append("")
        example_lines = tool.get_examples().strip().split("\n")
        if truncate and len(example_lines) > max_lines:
            lines.extend(example_lines[:max_lines])
            lines.append(f"... ({len(example_lines) - max_lines} more lines, use -v)")
        else:
            lines.extend(example_lines)

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
