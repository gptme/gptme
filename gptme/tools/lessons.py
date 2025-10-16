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


def _detect_conversation_mode(log: list[Message], recent_limit: int = 20) -> str:
    """Detect conversation mode by analyzing message patterns.

    In autonomous mode, there are few/no user messages - the conversation
    is mostly assistant messages with occasional system prompts.
    In interactive mode, there's regular back-and-forth between user and assistant.

    Args:
        log: Conversation log
        recent_limit: Number of recent messages to analyze

    Returns:
        "autonomous" if few user messages (< 30% of recent messages)
        "interactive" if regular back-and-forth
    """
    if not log:
        return "interactive"

    # Analyze recent messages (exclude system messages from the count)
    recent_messages = [
        msg for msg in log[-recent_limit:] if msg.role in ("user", "assistant")
    ]

    if not recent_messages:
        return "interactive"

    # Count user messages
    user_count = sum(1 for msg in recent_messages if msg.role == "user")
    total_count = len(recent_messages)

    if total_count == 0:
        return "interactive"

    # If < 30% user messages, it's autonomous mode
    user_ratio = user_count / total_count
    mode = "autonomous" if user_ratio < 0.3 else "interactive"

    logger.debug(
        f"Detected conversation mode: {mode} "
        f"(user_ratio: {user_ratio:.2f}, recent_messages: {total_count})"
    )

    return mode


def _extract_keywords_for_mode(
    log: list[Message], mode: str, limit: int = 10
) -> list[str]:
    """Extract keywords based on conversation mode.

    In autonomous mode, extracts keywords from both user AND assistant messages
    since there are few user messages to trigger lessons from.
    In interactive mode, only extracts from user messages.

    Args:
        log: Conversation log
        mode: "autonomous" or "interactive"
        limit: Number of recent messages to check

    Returns:
        List of unique keywords (lowercased)
    """
    keywords = []

    # In autonomous mode, check both user and assistant messages
    # In interactive mode, only check user messages
    roles_to_check = ["user", "assistant"] if mode == "autonomous" else ["user"]

    for msg in reversed(log[-limit:]):
        if msg.role in roles_to_check:
            # Extract keywords from message content
            # Simple approach: extract significant words (>4 chars, alphabetic)
            words = msg.content.lower().split()
            for word in words:
                # Keep only alphabetic characters
                cleaned_word = "".join(c for c in word if c.isalpha())

                # Filter for meaningful keywords
                if len(cleaned_word) > 4 and cleaned_word not in (
                    "about",
                    "could",
                    "would",
                    "should",
                    "these",
                    "those",
                ):
                    keywords.append(cleaned_word)

    # Return unique keywords, preserving order
    seen = set()
    unique_keywords = []
    for keyword in keywords:
        if keyword not in seen:
            seen.add(keyword)
            unique_keywords.append(keyword)

    logger.debug(
        f"Extracted {len(unique_keywords)} keywords for {mode} mode: {unique_keywords[:10]}"
    )

    return unique_keywords[:20]  # Limit to top 20


def _extract_message_content_for_mode(
    log: list[Message], mode: str, limit: int = 10
) -> str:
    """Extract message content based on conversation mode.

    In autonomous mode, combines content from both user AND assistant messages
    since there are few user messages to trigger lessons from.
    In interactive mode, uses only the last user message.

    Args:
        log: Conversation log
        mode: "autonomous" or "interactive"
        limit: Number of recent messages to check

    Returns:
        Combined message content string
    """
    if mode == "interactive":
        # Interactive mode: find last user message
        for msg in reversed(log):
            if msg.role == "user":
                return msg.content
        return ""

    else:  # autonomous mode
        # Autonomous mode: combine recent user and assistant messages
        messages = []
        for msg in reversed(log[-limit:]):
            if msg.role in ("user", "assistant"):
                messages.append(msg.content)

        # Combine messages (most recent first, so reverse to get chronological)
        combined = " ".join(reversed(messages))

        logger.debug(
            f"Combined {len(messages)} messages for {mode} mode "
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
) -> list[Message] | None:
    """Hook to automatically include relevant lessons in context.

    Detects conversation mode (autonomous vs interactive) by analyzing message patterns:
    - Autonomous mode: Few user messages, mostly assistant messages
    - Interactive mode: Regular back-and-forth conversation

    In autonomous mode, lessons are triggered by keywords from both user and assistant
    messages, with fewer lessons included to conserve tokens.
    In interactive mode, lessons are triggered only by user message keywords.

    Args:
        log: Current conversation log
        workspace: Optional workspace directory path
        **kwargs: Additional hook arguments (unused)

    Returns:
        New messages to prepend (lessons as system message), or None if disabled
    """
    if not HAS_LESSONS:
        logger.debug("Lessons module not available, skipping auto-inclusion")
        return []

    # Detect conversation mode
    mode = _detect_conversation_mode(log)

    # Get mode-specific configuration
    config = get_config()
    if mode == "autonomous":
        auto_include = config.get_env_bool(
            "GPTME_LESSONS_AUTO_INCLUDE_AUTONOMOUS", True
        )
        try:
            max_lessons = int(
                config.get_env("GPTME_LESSONS_MAX_INCLUDED_AUTONOMOUS") or "3"
            )
        except (ValueError, TypeError):
            max_lessons = 3
    else:  # interactive
        auto_include = config.get_env_bool("GPTME_LESSONS_AUTO_INCLUDE", True)
        try:
            max_lessons = int(config.get_env("GPTME_LESSONS_MAX_INCLUDED") or "5")
        except (ValueError, TypeError):
            max_lessons = 5

    if not auto_include:
        logger.debug(f"Auto-inclusion disabled for {mode} mode")
        return []

    # Get lessons already included
    included_lessons = _get_included_lessons_from_log(log)

    # Extract message content based on conversation mode
    message_content = _extract_message_content_for_mode(log, mode)
    tools = _extract_recent_tools(log)

    if not message_content and not tools:
        logger.debug("No message content or tools to match, skipping lesson inclusion")
        return []

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
            logger.debug(
                f"Limiting lessons from {len(new_matches)} to {max_lessons} for {mode} mode"
            )
            new_matches = new_matches[:max_lessons]

        if not new_matches:
            logger.debug(f"No new lessons to include for {mode} mode")
            return []

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

        logger.info(f"Auto-included {len(new_matches)} lessons for {mode} mode")

        return [lesson_msg]

    except Exception as e:
        logger.warning(f"Error during lesson auto-inclusion: {e}")
        return []


# Tool specification (for /tools command)
tool = ToolSpec(
    name="lessons",
    desc="Lesson system for structured guidance",
    instructions="""
The lesson system provides structured guidance through context-aware lessons.

Lessons are automatically included based on:
- Keywords from recent messages (in interactive mode: user messages only,
  in autonomous mode: both user and assistant messages)
- Tools used in the conversation

Lessons are matched by keywords and tools, with a limit on the number included.

Configuration:
- GPTME_LESSONS_AUTO_INCLUDE: Enable/disable auto-inclusion (default: true)
- GPTME_LESSONS_MAX_INCLUDED: Max lessons in interactive mode (default: 5)
- GPTME_LESSONS_AUTO_INCLUDE_AUTONOMOUS: Enable/disable in autonomous mode (default: true)
- GPTME_LESSONS_MAX_INCLUDED_AUTONOMOUS: Max lessons in autonomous mode (default: 3)

Conversation mode is automatically detected:
- Interactive: Regular back-and-forth (>= 30% user messages)
- Autonomous: Few user messages (< 30% user messages)
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
