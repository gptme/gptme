"""
Export and replay commands: export, summarize, replay.
"""

import logging
import shlex
from pathlib import Path

from .base import CommandContext, command

logger = logging.getLogger(__name__)


@command("summarize")
def cmd_summarize(ctx: CommandContext) -> None:
    """Summarize the conversation."""
    from .. import llm  # fmt: skip
    from ..logmanager import prepare_messages  # fmt: skip
    from ..message import print_msg  # fmt: skip

    ctx.manager.undo(1, quiet=True)
    msgs = prepare_messages(ctx.manager.log.messages)
    msgs = [m for m in msgs if not m.hide]
    print_msg(llm.summarize(msgs))


def _complete_replay(partial: str, _prev_args: list[str]) -> list[tuple[str, str]]:
    """Complete replay command options."""
    from ..tools import get_tools  # fmt: skip

    completions: list[tuple[str, str]] = []
    options = [
        ("last", "Replay only the last assistant message"),
        ("all", "Replay all assistant messages"),
    ]
    for opt, desc in options:
        if opt.startswith(partial.lower()):
            completions.append((opt, desc))

    # Also suggest tool names
    for tool in get_tools():
        if tool.name.startswith(partial.lower()):
            completions.append((tool.name, f"Replay all {tool.name} operations"))

    return completions


@command("replay", completer=_complete_replay)
def cmd_replay(ctx: CommandContext) -> None:
    """Replay the conversation or specific tool operations."""
    from ..message import print_msg  # fmt: skip
    from ..tools import ToolUse, execute_msg  # fmt: skip

    ctx.manager.undo(1, quiet=True)
    ctx.manager.write()

    # Check if replaying a specific tool
    if ctx.args and ctx.args[0].lower() not in ["last", "all"]:
        tool_name = ctx.args[0]
        _replay_tool(ctx.manager.log, tool_name)
        return

    # Determine replay scope for messages
    if ctx.args:
        scope = ctx.args[0].lower()
        if scope not in ["last", "all"]:
            print(f"Invalid option '{scope}'. Use 'last', 'all', or a tool name.")
            return
    else:
        print("Replay options:")
        print("  last - Replay only the last assistant message")
        print("  all  - Replay all assistant messages")
        print("  <tool> - Replay all operations for a specific tool (e.g., todowrite)")
        scope = input("Choose (last/all/<tool>): ").strip().lower()
        if scope not in ["last", "all"]:
            # Try as tool name
            _replay_tool(ctx.manager.log, scope)
            return

    assistant_messages = [msg for msg in ctx.manager.log if msg.role == "assistant"]

    if not assistant_messages:
        print("No assistant messages found to replay.")
        return

    if scope == "last":
        # Find the last assistant message that contains tool uses
        last_with_tools = None
        for msg in reversed(assistant_messages):
            # Check if message has any tool uses by trying to execute it
            if any(ToolUse.iter_from_content(msg.content)):
                last_with_tools = msg
                break

        if not last_with_tools:
            print("No assistant messages with tool uses found to replay.")
            return

        messages_to_replay = [last_with_tools]
        print("Replaying last assistant message with tool uses...")
    else:  # scope == "all"
        messages_to_replay = assistant_messages
        print(f"Replaying all {len(assistant_messages)} assistant messages...")

    for msg in messages_to_replay:
        for reply_msg in execute_msg(msg, ctx.confirm):
            print_msg(reply_msg, oneline=False)


def _replay_tool(log, tool_name: str) -> None:
    """Replay all operations for a specific tool from the conversation log."""
    from ..tools import ToolUse, get_tool  # fmt: skip

    tool = get_tool(tool_name)
    if not tool:
        print(f"Error: Tool '{tool_name}' not found or not loaded.")
        return

    print(f"Replaying all '{tool_name}' operations...")
    count = 0

    for msg in log:
        for tooluse in ToolUse.iter_from_content(msg.content):  # noqa: F821 - ToolUse imported above
            if tooluse.tool == tool_name and tooluse.content:
                count += 1
                # Execute the tool operation
                lines = [
                    line.strip()
                    for line in tooluse.content.strip().split("\n")
                    if line.strip()
                ]

                for line in lines:
                    # Use the tool's execute function directly
                    # For tools like todowrite, this will update internal state
                    try:
                        parts = shlex.split(line)
                        if parts:
                            # Import the tool's helper function if it exists
                            # For todowrite, this would be _todowrite
                            helper_name = f"_{tool_name}"
                            tool_module = __import__(
                                f"gptme.tools.{tool_name}",
                                fromlist=[helper_name],
                            )
                            if hasattr(tool_module, helper_name):
                                helper_func = getattr(tool_module, helper_name)
                                result = helper_func(*parts)
                                if result:
                                    print(f"  {result}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to replay {tool_name} operation '{line}': {e}"
                        )

    if count == 0:
        print(f"No '{tool_name}' operations found to replay.")
    else:
        print(f"✅ Replayed {count} '{tool_name}' operations")


@command("export")
def cmd_export(ctx: CommandContext) -> None:
    """Export conversation as HTML."""
    from ..util.export import export_chat_to_html  # fmt: skip

    ctx.manager.undo(1, quiet=True)
    ctx.manager.write()
    # Get output path from args or use default
    output_path = (
        Path(ctx.args[0])
        if ctx.args
        else Path(f"{ctx.manager.logfile.parent.name}.html")
    )
    # Export the chat
    export_chat_to_html(ctx.manager.name, ctx.manager.log, output_path)
    print(f"Exported conversation to {output_path}")
