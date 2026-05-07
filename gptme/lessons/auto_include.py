"""Automatic lesson inclusion based on context."""

import logging
import os
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

# Default token budget for lesson injection (50K tokens).
# Configurable via GPTME_LESSONS_TOKEN_BUDGET env var.
_DEFAULT_TOKEN_BUDGET = 50000


def _get_token_budget() -> int:
    """Get the lesson token budget from environment or default."""
    try:
        budget = int(
            os.environ.get("GPTME_LESSONS_TOKEN_BUDGET", str(_DEFAULT_TOKEN_BUDGET))
        )
        if budget <= 0:
            logger.warning(
                "GPTME_LESSONS_TOKEN_BUDGET=%d is non-positive, using default %d",
                budget,
                _DEFAULT_TOKEN_BUDGET,
            )
            return _DEFAULT_TOKEN_BUDGET
        return budget
    except (ValueError, TypeError):
        return _DEFAULT_TOKEN_BUDGET


def _estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Uses a simple character-based heuristic (~3 chars per token, conservative for
    code/markdown density). This is a rough estimate sufficient for budget
    enforcement — actual tokenization varies by model.
    """
    return max(1, len(text) // 3)


def auto_include_lessons(
    messages: list["Message"],
    max_lessons: int = 5,
    enabled: bool = True,
    use_hybrid: bool = False,
    hybrid_config: "HybridConfig | None" = None,
    max_tokens: int | None = None,
) -> list["Message"]:
    """Automatically include relevant lessons in message context.

    Args:
        messages: List of messages
        max_lessons: Maximum number of lessons to include
        enabled: Whether auto-inclusion is enabled
        use_hybrid: Use hybrid matching (semantic + effectiveness)
        hybrid_config: Configuration for hybrid matching
        max_tokens: Token budget for lessons beyond the first (default from env GPTME_LESSONS_TOKEN_BUDGET).
            The highest-scored lesson is always force-included regardless of this limit.

    Returns:
        Updated message list with lessons included
    """
    if not enabled:
        return messages

    # Resolve token budget
    if max_tokens is None:
        max_tokens = _get_token_budget()

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

        # Format lessons for inclusion, respecting token budget
        lesson_content, dropped_count, subsequent_tokens = _format_with_budget(
            matches, max_tokens
        )

        # Log if we dropped lessons due to budget
        if dropped_count > 0:
            logger.warning(
                "Lesson token budget exceeded: dropped %d/%d matched lessons"
                " (%dK/%dK subsequent-lesson budget used)",
                dropped_count,
                len(matches),
                subsequent_tokens // 1000,
                max_tokens // 1000,
            )

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
        return [lesson_msg] + messages

    except Exception as e:
        logger.warning(f"Failed to include lessons: {e}")
        return messages


def _format_with_budget(matches: list, max_tokens: int) -> tuple[str, int, int]:
    """Format matched lessons with token budget enforcement.

    The highest-scored lesson is always included regardless of size.
    Subsequent lessons are included only if their tokens fit within max_tokens
    counting only the non-first lessons — so an oversized first lesson does not
    consume the budget available to smaller subsequent ones.

    Args:
        matches: List of match results (already sorted by score, descending)
        max_tokens: Maximum token budget for non-first lessons

    Returns:
        Tuple of (formatted content, number of lessons dropped due to budget,
        tokens used by subsequent (non-first) lessons)
    """
    included: list[str] = []
    dropped = 0
    # Track tokens for budget enforcement separately from the first (forced) lesson.
    # This prevents an oversized first lesson from consuming the budget available
    # to smaller subsequent lessons.
    subsequent_tokens = 0

    for match in matches:
        lesson = match.lesson

        # Build individual lesson content (same format as _format_lessons)
        parts = []
        if included:
            parts.append("\n")
        parts.append(f"## {lesson.title}\n")
        parts.append(f"\n*Path: {lesson.path}*\n")
        parts.append(f"\n*Category: {lesson.category or 'general'}*\n")
        if match.matched_by:
            parts.append(f"\n*Matched by: {len(match.matched_by)} keyword(s)*\n")
        parts.append(f"\n{lesson.body}\n")

        lesson_str = "".join(parts)
        lesson_tokens = _estimate_tokens(lesson_str)

        if not included:
            # Always include the first (highest-scored) lesson regardless of size
            included.append(lesson_str)
        elif subsequent_tokens + lesson_tokens > max_tokens:
            dropped += 1
        else:
            included.append(lesson_str)
            subsequent_tokens += lesson_tokens

    return "".join(included), dropped, subsequent_tokens


def _format_lessons(matches: list) -> str:
    """Format matched lessons for inclusion.

    Note: This function is kept for backward compatibility.
    The token-budget-aware _format_with_budget is preferred.
    """
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

        # Add match info (count only — avoid injecting keyword text which
        # creates self-referential corpus matches in lesson effectiveness analysis)
        if match.matched_by:
            parts.append(f"\n*Matched by: {len(match.matched_by)} keyword(s)*\n")

        # Add lesson content
        parts.append(f"\n{lesson.body}\n")

    return "".join(parts)
