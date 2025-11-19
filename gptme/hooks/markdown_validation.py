"""
Markdown validation hook that detects potential codeblock cut-offs.

This hook checks the last line of markdown tool uses in assistant messages
for suspicious patterns that indicate incomplete content due to missing
language tags.

Suspicious patterns:
- Lines starting with '#' (incomplete headers)
- Lines ending with ':' (incomplete content)
"""

import logging
from collections.abc import Generator
from typing import TYPE_CHECKING

from ..message import Message
from . import HookType, StopPropagation, register_hook

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)


def register() -> None:
    """Setup function to register the markdown validation hook."""
    register_hook(
        "markdown_validation",
        HookType.MESSAGE_POST_PROCESS,
        validate_markdown_on_message_complete,
        priority=1,  # Low priority to run after most other hooks
    )


def check_last_line_suspicious(content: str) -> tuple[bool, str | None]:
    """Check if the last line of content has suspicious patterns.

    Args:
        content: The content to check

    Returns:
        Tuple of (is_suspicious, pattern_description)
    """
    if not content or not content.strip():
        return False, None

    lines = content.split("\n")

    # Get last non-empty line
    last_line = None
    for line in reversed(lines):
        if line.strip():
            last_line = line.strip()
            break

    if not last_line:
        return False, None

    # Check for suspicious patterns
    if last_line.startswith("#"):
        return True, f"ends with header start: '{last_line}'"

    return False, None


def validate_markdown_on_message_complete(
    manager: "LogManager",
) -> Generator[Message | StopPropagation, None, None]:
    """Hook that validates markdown content in assistant messages.

    Checks the last line of the last markdown tooluse for suspicious patterns
    that indicate the codeblock was cut off due to missing language tags.

    Args:
        manager: The log manager containing conversation history

    Yields:
        System message warning about potential cut-off if detected
    """
    # Import here to avoid circular dependency
    from ..tools.base import ToolUse

    # Get the last message
    if not manager.log.messages:
        return

    last_msg = manager.log.messages[-1]

    # Only check assistant messages
    if last_msg.role != "assistant":
        return

    # Extract all tool uses from the message
    tool_uses = list(
        ToolUse.iter_from_content(last_msg.content, tool_format_override="markdown")
    )

    if not tool_uses:
        return

    # Check the last tool use
    last_tool_use = tool_uses[-1]

    # Only check tool uses with content
    if not last_tool_use.content:
        return

    # Check if the last line is suspicious
    is_suspicious, pattern = check_last_line_suspicious(last_tool_use.content)

    if not is_suspicious:
        return

    # Yield warning message
    warning = f"""⚠️  **Potential markdown codeblock cut-off detected**

The last codeblock in your response {pattern}

This often happens when markdown codeblocks lack language tags, causing the parser to misinterpret closing backticks and cut content early.

**Fix**: Add explicit language tags to all codeblocks:
```txt
Plain text content
```
"""

    yield Message("system", warning, hide=False)
