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


def _extract_message_content(log: list[Message], limit: int = 10) -> str:
    """Extract message content from recent user and assistant messages.

    Args:
        log: Conversation log
        limit: Number of recent messages to check

    Returns:
        Combined message content string
    """
    messages = []
    for msg in reversed(log[-limit:]):
        if msg.role in ("user", "assistant"):
            messages.append(msg.content)

    # Combine messages (most recent first, so reverse to get chronological)
    combined = " ".join(reversed(messages))

    logger.debug(
        f"Extracted content from {len(messages)} messages "
        f"(content length: {len(combined)} chars)"
    )

    return combined


def _format_lessons(matches: list) -> str:
    """Format matched lessons for inclusion.

    Args:
        matches: List of MatchResult objects

    Returns:
        Formatted lessons as string
    """
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


def auto_include_lessons_hook(
    log: list[Message], workspace: str | None = None, **kwargs
) -> Generator[Message, None, None]:
    """Hook to automatically include relevant lessons in context.

    Extracts keywords from both user and assistant messages to trigger lessons.
    This enables lessons to be relevant in both interactive and autonomous contexts.

    Args:
        log: Current conversation log
        workspace: Optional workspace directory path
        **kwargs: Additional hook arguments (unused)

    Returns:
        Generator of messages to prepend (lessons as system message)
    """
    if not HAS_LESSONS:
        logger.debug("Lessons module not available, skipping auto-inclusion")
        return

    # Get configuration
    config = get_config()
    auto_include = config.get_env_bool("GPTME_LESSONS_AUTO_INCLUDE", True)

    if not auto_include:
        logger.debug("Auto-inclusion disabled")
        return

    try:
        max_lessons = int(config.get_env("GPTME_LESSONS_MAX_INCLUDED") or "5")
    except (ValueError, TypeError):
        max_lessons = 5

    # Get lessons already included
    included_lessons = _get_included_lessons_from_log(log)

    # Extract message content from recent user and assistant messages
    message_content = _extract_message_content(log)
    tools = _extract_recent_tools(log)

    if not message_content and not tools:
        logger.debug("No message content or tools to match, skipping lesson inclusion")
        return

    # Create match context
    context = MatchContext(
        message=message_content,
        tools_used=tools,
    )

    # Get lesson index and find matching lessons
    try:
        index = _get_lesson_index()
        matcher = LessonMatcher()
        match_results = matcher.match(index.lessons, context)

        # Filter out already included lessons (MatchResult has .lesson attribute)
        new_matches = [
            match
            for match in match_results
            if str(match.lesson.path) not in included_lessons
        ]

        # Limit number of lessons
        if len(new_matches) > max_lessons:
            logger.debug(f"Limiting lessons from {len(new_matches)} to {max_lessons}")
            new_matches = new_matches[:max_lessons]

        if not new_matches:
            logger.debug("No new lessons to include")
            return

        # Format lessons as system message
        content_parts = ["# Relevant Lessons\n"]
        for match in new_matches:
            lesson = match.lesson
            content_parts.append(f"\n## {lesson.title}\n")
            content_parts.append(f"\n*Path: {lesson.path}*\n")
            content_parts.append(f"\n*Category: {lesson.category}*\n")
            content_parts.append(f"\n*Matched by: {', '.join(match.matched_by)}*\n")
            content_parts.append(f"\n{lesson.body}\n")

        lesson_msg = Message(
            role="system",
            content="".join(content_parts),
            hide=True,  # Hide from user-facing output
        )

        titles = [str(match.lesson.title) for match in new_matches]
        newline = "\n- "
        logger.info(f"Auto-included {len(new_matches)} lessons: \n{newline.join(titles)}")

        yield lesson_msg

    except Exception as e:
        logger.warning(f"Error during lesson auto-inclusion: {e}")
        return


# Tool specification (for /tools command)
tool = ToolSpec(
    name="lessons",
    desc="Lesson system for structured guidance",
    instructions="""
Use lessons to improve your performance and avoid known failure modes.

How lessons help you:
- Automatically included when relevant keywords or tools match
- Extracted from both user and assistant messages in the conversation
- Limited to 5 most relevant lessons to conserve context

Commands available:
- /lesson list - View all available lessons
- /lesson search <query> - Find lessons matching query
- /lesson show <id> - Display a specific lesson
- /lesson refresh - Reload lessons from disk

Leverage lessons for self-improvement:
- Pay attention to lessons included in context
- Apply patterns and avoid anti-patterns
- Reference lessons when making decisions
- Learn from past failures documented in lessons
""".strip(),
    examples="",
    functions=[],
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
