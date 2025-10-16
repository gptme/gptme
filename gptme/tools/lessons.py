"""
Lesson system tool for gptme.

Provides structured lessons with metadata that can be automatically included in context.
Similar to .cursorrules but with keyword-based triggering.
"""

import logging
import threading
from collections.abc import Generator

from ..commands import CommandContext
from ..config import get_config
from ..hooks import HookType
from ..message import Message
from .base import ToolSpec

logger = logging.getLogger(__name__)

# Import lesson utilities
try:
    from ..lessons import LessonIndex, LessonMatcher, MatchContext

    HAS_LESSONS = True
except ImportError:
    HAS_LESSONS = False
    logger.warning("Lessons module not available")

# Thread-local storage for lesson index
_thread_local = threading.local()


def _get_lesson_index() -> LessonIndex:
    """Get thread-local lesson index, creating it if needed."""
    if not hasattr(_thread_local, "index"):
        _thread_local.index = LessonIndex()
    return _thread_local.index


def _get_included_lessons_from_log(log: list[Message]) -> set[str]:
    """Extract lesson paths that have already been included in the conversation.

    Args:
        log: Conversation log

    Returns:
        Set of lesson paths (as strings) that have been included
    """
    included = set()

    for msg in log:
        if msg.role == "system" and "# Relevant Lessons" in msg.content:
            # Extract lesson paths from formatted lessons
            # Format: *Path: /some/path/lesson.md*
            lines = msg.content.split("\n")
            for line in lines:
                if line.startswith("*Path: ") and line.endswith("*"):
                    # Extract path between "*Path: " and final "*"
                    path_str = line[7:-1]  # Remove "*Path: " prefix and "*" suffix
                    included.add(path_str)

    return included


def _extract_recent_tools(log: list[Message], limit: int = 10) -> list[str]:
    """Extract tools used in recent messages.

    Args:
        log: Conversation log
        limit: Number of recent messages to check

    Returns:
        List of unique tool names used
    """
    tools = []

    # Check recent messages for tool use
    for msg in reversed(log[-limit:]):
        # Check for tool use in assistant messages
        if msg.role == "assistant":
            # Extract tool names from ToolUse/ToolResult patterns
            for block in msg.get_codeblocks():
                if block.lang and block.lang not in ("text", "markdown"):
                    # Extract just the tool name (first word) from lang
                    # e.g., "patch file.py" -> "patch"
                    tool_name = block.lang.split()[0]
                    tools.append(tool_name)

    # Return unique tools, preserving order
    seen = set()
    unique_tools = []
    for tool in tools:
        if tool not in seen:
            seen.add(tool)
            unique_tools.append(tool)

    return unique_tools


def auto_include_lessons_hook(
    log: list[Message],
    **kwargs,
) -> Generator[Message, None, None]:
    """Hook to automatically include relevant lessons before message processing."""
    if not HAS_LESSONS:
        return

    # Check if auto-include is enabled via environment variable
    config = get_config()
    if not config.get_env_bool("GPTME_LESSONS_AUTO_INCLUDE", True):
        return

    # Get max lessons from environment or use default
    max_lessons_str = config.get_env("GPTME_LESSONS_MAX_INCLUDED") or "5"
    try:
        max_lessons = int(max_lessons_str)
    except (ValueError, TypeError):
        max_lessons = 5

    # Get last user message
    user_msg = None
    for msg in reversed(log):
        if msg.role == "user":
            user_msg = msg
            break

    if not user_msg:
        logger.debug("No user message found, skipping lesson inclusion")
        return

    # Extract recent tool usage
    recent_tools = _extract_recent_tools(log)

    # Build match context with tools
    context = MatchContext(message=user_msg.content, tools_used=recent_tools)

    # Find matching lessons
    try:
        index = _get_lesson_index()
        if not index.lessons:
            logger.debug("No lessons found in index")
            return

        matcher = LessonMatcher()
        matches = matcher.match(index.lessons, context)

        if not matches:
            logger.debug("No matching lessons found")
            return

        # Filter out already-included lessons by checking history
        included_paths = _get_included_lessons_from_log(log)
        new_matches = [m for m in matches if str(m.lesson.path) not in included_paths]

        if not new_matches:
            logger.debug("All matching lessons already included")
            return

        # Limit to top N
        new_matches = new_matches[:max_lessons]

        # Format lessons for inclusion
        lesson_content = _format_lessons(new_matches)

        # Yield system message with lessons
        yield Message(
            role="system",
            content=f"# Relevant Lessons\n\n{lesson_content}",
            hide=True,  # Don't show in UI by default
        )

    except Exception as e:
        logger.exception(f"Failed to include lessons: {e}")


def _format_lessons(matches: list) -> str:
    """Format matched lessons for inclusion."""
    parts = []

    for i, match in enumerate(matches, 1):
        lesson = match.lesson

        # Add separator between lessons
        if i > 1:
            parts.append("\n---\n")

        # Add lesson with context
        parts.append(f"## {lesson.title}\n")
        parts.append(f"*Path: {lesson.path}*\n")
        parts.append(f"*Category: {lesson.category}*\n")
        parts.append(f"*Matched by: {', '.join(match.matched_by)}*\n\n")
        parts.append(lesson.body)

    return "\n".join(parts)


def handle_lesson_command(ctx: CommandContext) -> Generator[Message, None, None]:
    """Handle /lesson command."""
    if not HAS_LESSONS:
        yield Message(
            role="system",
            content="Lessons module not available. Install PyYAML to enable lessons.",
        )
        return

    # Import command handler
    from ..lessons.commands import lesson

    # Delegate to the command handler
    yield from lesson(ctx)


# Tool specification
tool = ToolSpec(
    name="lessons",
    desc="Structured lessons with automatic context inclusion",
    instructions="""
The lesson system provides structured lessons that can be automatically included
in context based on keywords.

Lessons are stored as Markdown files with YAML frontmatter:

```yaml
---
match:
  keywords: [patch, file, editing]
---

# Lesson Title

Lesson content...
```

Lessons are automatically included when their keywords match the conversation context.
""".strip(),
    available=HAS_LESSONS,
    hooks={
        "auto_include_lessons": (
            HookType.MESSAGE_PRE_PROCESS.value,
            auto_include_lessons_hook,
            5,  # Medium priority
        )
    },
    commands={
        "lesson": handle_lesson_command,
    },
)

__all__ = ["tool"]
