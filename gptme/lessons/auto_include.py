"""Automatic lesson inclusion based on context."""

import logging
from typing import TYPE_CHECKING

from .index import LessonIndex
from .matcher import LessonMatcher, MatchContext

if TYPE_CHECKING:
    from ..message import Message

logger = logging.getLogger(__name__)

# Optional hybrid matching support
try:
    from .hybrid_matcher import HybridConfig, HybridLessonMatcher

    HYBRID_AVAILABLE = True
except ImportError:
    HYBRID_AVAILABLE = False
    logger.info("Hybrid matching not available, using keyword-only matching")


def auto_include_lessons(
    messages: list["Message"],
    max_lessons: int = 5,
    enabled: bool = True,
    use_hybrid: bool = False,
    hybrid_config: "HybridConfig | None" = None,
) -> list["Message"]:
    """Automatically include relevant lessons in message context.

    Args:
        messages: List of messages
        max_lessons: Maximum number of lessons to include
        enabled: Whether auto-inclusion is enabled
        use_hybrid: Use hybrid matching (semantic + effectiveness)
        hybrid_config: Configuration for hybrid matching

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

        # Choose matcher based on configuration
        matcher: LessonMatcher
        if use_hybrid and HYBRID_AVAILABLE:
            logger.debug("Using hybrid lesson matcher")
            matcher = HybridLessonMatcher(config=hybrid_config)
        else:
            if use_hybrid:
                logger.warning(
                    "Hybrid matching requested but not available, falling back to keyword-only"
                )
            logger.debug("Using keyword-only lesson matcher")
            matcher = LessonMatcher()

        matches = matcher.match(index.lessons, context)

        if not matches:
            logger.debug("No matching lessons found")
            return messages

        # Limit to top N (matcher may already limit, but ensure it)
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
            parts.append("\n")

        # Add lesson header with metadata
        parts.append(f"## {lesson.title}\n")
        parts.append(f"\n*Path: {lesson.path}*\n")
        parts.append(f"\n*Category: {lesson.category or 'general'}*\n")

        # Add match info
        if match.matched_by:
            parts.append(f"\n*Matched by: {', '.join(match.matched_by[:3])}*\n")

        # Add lesson content
        parts.append(f"\n{lesson.body}\n")

    return "".join(parts)
