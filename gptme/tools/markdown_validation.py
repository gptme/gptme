"""
Markdown validation hook that detects potential codeblock cut-offs in assistant messages.

This tool hooks into MESSAGE_POST_PROCESS to check if assistant messages end with
suspicious patterns that indicate incomplete codeblock content due to missing language tags.

Suspicious patterns detected:
- Lines starting with '#' (incomplete headers)
- Lines ending with ':' (incomplete content like "Title:" or "Description:")

The validation runs after message processing to provide immediate feedback about
potential issues that need correction.

This implements the architectural pattern suggested in gptme/gptme#822 review,
using MESSAGE_POST_PROCESS hooks instead of pre-commit validation.
"""

import logging
from collections.abc import Generator
from typing import TYPE_CHECKING

from ..hooks import HookType, StopPropagation
from ..message import Message
from .base import ToolSpec

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)


def check_markdown_ending(content: str) -> tuple[bool, str | None]:
    """Check if markdown content has suspicious ending patterns.

    Args:
        content: The markdown content to check

    Returns:
        Tuple of (is_suspicious, pattern_description)
        - is_suspicious: True if suspicious pattern detected
        - pattern_description: Description of what was detected, or None
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

    if last_line.endswith(":"):
        return True, f"ends with colon: '{last_line}'"

    return False, None


def validate_markdown_on_message_complete(
    manager: "LogManager",
) -> Generator[Message | StopPropagation, None, None]:
    """Hook that validates markdown content in assistant messages.

    Checks the last assistant message for suspicious endings that might indicate
    codeblock content was cut off due to missing language tags.

    Args:
        manager: The log manager containing conversation history

    Yields:
        System message warning about potential cut-off if detected
    """
    # Get the last message from the log
    if not manager.log.messages:
        return

    last_msg = manager.log.messages[-1]

    # Only check assistant messages
    if last_msg.role != "assistant":
        return

    # Check for suspicious endings
    is_suspicious, pattern = check_markdown_ending(last_msg.content)

    if not is_suspicious:
        return

    # Yield warning message
    warning = f"""⚠️  **Potential markdown codeblock cut-off detected**

Your response {pattern}

This often happens when markdown codeblocks lack language tags, causing the parser to misinterpret closing backticks and cut content early.

**Fix**: Add explicit language tags to all codeblocks:
```txt
Plain text content
```

```python
# Python code
```

```shell
# Shell commands
```

**See**: lessons/tools/markdown-codeblock-syntax.md for complete guidance.
"""

    yield Message("system", warning, hide=False)


# Tool specification
tool = ToolSpec(
    name="markdown_validation",
    desc="Validates markdown content in assistant messages for potential codeblock cut-offs",
    instructions="This tool automatically checks assistant messages for suspicious endings that indicate incomplete content",
    available=True,
    hooks={
        "markdown_validation": (
            HookType.MESSAGE_POST_PROCESS.value,
            validate_markdown_on_message_complete,
            # Low priority (1) to run after most other hooks
            1,
        )
    },
)

__all__ = ["tool"]
