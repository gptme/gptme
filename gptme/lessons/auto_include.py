"""Automatic lesson inclusion based on context."""

import logging
from typing import TYPE_CHECKING

from .index import LessonIndex
from .matcher import LessonMatcher, MatchContext

if TYPE_CHECKING:
    from ..message import Message

logger = logging.getLogger(__name__)


def auto_include_lessons(
    messages: list["Message"],
    max_lessons: int = 5,
    enabled: bool = True,
) -> list["Message"]:
    """Automatically include relevant lessons in message context.

    Args:
        messages: List of messages
        max_lessons: Maximum number of lessons to include
        enabled: Whether auto-inclusion is enabled

    Returns:
        Updated message list with lessons included
    """
    if not enabled:
        return messages

    # Get last user message
    user_msg = None
    for msg in reversed(messages):
        if msg.role == "user":
            user_msg = msg
            break

    if not user_msg:
        logger.debug("No user message found, skipping lesson inclusion")
        return messages

    # Build match context
    context = MatchContext(message=user_msg.content)

    # Find matching lessons
    try:
        index = LessonIndex()
        if not index.lessons:
            logger.debug("No lessons found in index")
            return messages

        matcher = LessonMatcher()
        matches = matcher.match(index.lessons, context)

        if not matches:
            logger.debug("No matching lessons found")
            return messages

        # Limit to top N
        matches = matches[:max_lessons]

        # Format lessons for inclusion
        lesson_content = _format_lessons(matches)

        # Create system message with lessons
        from ..message import Message

        lesson_msg = Message(
            role="system",
            content=f"# Relevant Lessons\n\n{lesson_content}",
            hide=True,  # Don't show in UI by default
        )

        # Insert after initial system message
        # Assume first message is system prompt
        if messages and messages[0].role == "system":
            return [messages[0], lesson_msg] + messages[1:]
        else:
            return [lesson_msg] + messages

    except Exception as e:
        logger.warning(f"Failed to include lessons: {e}")
        return messages


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
        parts.append(f"*Category: {lesson.category}*\n")
        parts.append(f"*Matched by: {', '.join(match.matched_by)}*\n\n")
        parts.append(lesson.body)

    return "\n".join(parts)
