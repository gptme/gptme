"""
Lesson system tool for gptme.

Provides structured lessons with metadata that can be automatically included in context.
Similar to .cursorrules or "Claude Skills". Has keyword-based triggering.

Commands provided:

- ``/lesson list`` - View all available lessons
- ``/lesson search <query>`` - Find lessons matching query
- ``/lesson show <id>`` - Display a specific lesson
- ``/lesson refresh`` - Reload lessons from disk
"""

import logging
from collections.abc import Generator
from contextvars import ContextVar
from typing import TYPE_CHECKING

from ..commands import CommandContext
from ..config import get_config
from ..hooks import HookType, StopPropagation
from ..lessons import LessonIndex, LessonMatcher, MatchContext
from ..lessons.commands import lesson
from ..message import Message
from .base import ToolSpec

if TYPE_CHECKING:
    from ..logmanager import LogManager

# Try to import ACE integration for hybrid lesson matching
try:
    from ace.embedder import LessonEmbedder  # type: ignore[import-not-found]
    from ace.gptme_integration import (  # type: ignore[import-not-found]
        GptmeHybridMatcher,
    )

    ACE_AVAILABLE = True
except ImportError:
    ACE_AVAILABLE = False
    GptmeHybridMatcher = None  # type: ignore
    LessonEmbedder = None  # type: ignore

logger = logging.getLogger(__name__)

# Context-local storage for lesson index
_lesson_index_var: ContextVar[LessonIndex | None] = ContextVar(
    "lesson_index", default=None
)


def _get_lesson_index() -> LessonIndex:
    """Get context-local lesson index, creating it if needed."""
    index = _lesson_index_var.get()
    if index is None:
        index = LessonIndex()
        _lesson_index_var.set(index)
    return index


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


def handle_lesson_command(ctx: CommandContext) -> Generator[Message, None, None]:
    """Handle /lesson command."""
    # Delegate to the command handler
    yield from lesson(ctx)


def auto_include_lessons_hook(
    manager: "LogManager",
) -> Generator[Message | StopPropagation, None, None]:
    """Hook to automatically include relevant lessons in context.

    Extracts keywords from both user and assistant messages to trigger lessons.

    Args:
        manager: Conversation manager with log and workspace

    Returns:
        Generator of messages to prepend (lessons as system message)
    """
    # Get configuration
    config = get_config()
    auto_include = config.get_env_bool("GPTME_LESSONS_AUTO_INCLUDE", True)

    if not auto_include:
        logger.debug("Auto-inclusion disabled")
        return

    # Get hybrid matching configuration
    use_hybrid = config.get_env_bool("GPTME_LESSONS_USE_HYBRID", False)

    try:
        max_lessons = int(config.get_env("GPTME_LESSONS_MAX_INCLUDED") or "5")
    except (ValueError, TypeError):
        max_lessons = 5

    # Get messages from log
    messages = manager.log.messages

    # Get lessons already included
    included_lessons = _get_included_lessons_from_log(messages)

    # Extract message content from recent user and assistant messages
    message_content = _extract_message_content(messages)
    tools = _extract_recent_tools(messages)

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

        # Choose matcher based on configuration
        if use_hybrid and ACE_AVAILABLE:
            logger.info("Using ACE hybrid lesson matching")
            # Initialize embedder with lesson directories from LessonIndex
            from pathlib import Path

            lesson_dirs = index.lesson_dirs

            # Use first lesson directory for embedder (typically workspace lessons)
            # Embeddings stored in .gptme/embeddings/lessons/
            embeddings_dir = Path.cwd() / ".gptme" / "embeddings" / "lessons"

            try:
                embedder = LessonEmbedder(
                    lessons_dir=lesson_dirs[0]
                    if lesson_dirs
                    else Path.cwd() / "lessons",
                    embeddings_dir=embeddings_dir,
                )
                logger.info(
                    f"Initialized ACE embedder with lessons_dir={lesson_dirs[0] if lesson_dirs else 'lessons'}"
                )
                matcher = GptmeHybridMatcher(embedder=embedder)
            except Exception as e:
                logger.warning(
                    f"Failed to initialize embedder: {e}. Falling back to keyword matching."
                )
                matcher = GptmeHybridMatcher(embedder=None)
        else:
            if use_hybrid and not ACE_AVAILABLE:
                logger.warning(
                    "Hybrid matching requested but ACE not available, falling back to keyword-only"
                )
            logger.debug("Using keyword-only lesson matcher")
            matcher = LessonMatcher()

        # Generate session_id from chat_id for tracking (only for hybrid matcher)
        session_id = manager.chat_id if hasattr(manager, "chat_id") else None

        # Call matcher with appropriate parameters
        # Only GptmeHybridMatcher supports session_id parameter
        if ACE_AVAILABLE and isinstance(matcher, GptmeHybridMatcher):
            match_results = matcher.match(index.lessons, context, session_id=session_id)
        else:
            match_results = matcher.match(index.lessons, context)

        # Filter out already included lessons (MatchResult has .lesson attribute)
        new_matches = [
            match
            for match in match_results
            if str(match.lesson.path) not in included_lessons
        ]

        # Limit number of lessons (matcher may already limit, but ensure it)
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
        titles_list = "\n".join(f"- {title}" for title in titles)
        logger.info(f"Auto-included {len(new_matches)} lessons:\n{titles_list}")

        yield lesson_msg

    except Exception as e:
        logger.warning(f"Error during lesson auto-inclusion: {e}")
        return


# Tool specification (for /tools command)
tool = ToolSpec(
    name="lessons",
    desc="Lesson system for structured guidance",
    instructions="""
Use lessons to learn and remember skills/tools/workflows, improve your performance, and avoid known failure modes.

How lessons help you:
- Automatically included when relevant keywords or tools match
- Extracted from both user and assistant messages in the conversation
- Limited to 5 most relevant lessons to conserve context

Leverage lessons for self-improvement:
- Pay attention to lessons included in context
- Apply patterns and avoid anti-patterns
- Reference lessons when making decisions
- Learn from past failures documented in lessons
""".strip(),
    examples="",
    functions=[],
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
