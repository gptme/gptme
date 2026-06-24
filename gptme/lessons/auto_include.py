"""Automatic lesson inclusion based on context."""

import json
import logging
import os
import random
import time
import uuid
from pathlib import Path
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


def _get_dropout_epsilon() -> float:
    """Get the randomized lesson-dropout probability from the environment.

    Controlled by ``LESSON_DROPOUT_EPSILON`` (float in [0, 1]). When > 0, each
    otherwise-matched lesson is independently withheld with this probability and
    the withheld set is logged for causal leave-one-out analysis. Default 0.0
    means no dropout (fully backwards compatible).
    """
    raw = os.environ.get("LESSON_DROPOUT_EPSILON")
    if not raw:
        return 0.0
    try:
        epsilon = float(raw)
    except (ValueError, TypeError):
        logger.warning("Invalid LESSON_DROPOUT_EPSILON=%r, ignoring", raw)
        return 0.0
    if epsilon <= 0.0:
        return 0.0
    if epsilon > 1.0:
        logger.warning("LESSON_DROPOUT_EPSILON=%s clamped to 1.0", epsilon)
        return 1.0
    return epsilon


def _get_dropout_session_id() -> str:
    """Resolve the session id used to correlate dropout logs with outcomes.

    Prefers ``GPTME_SESSION_ID`` / ``CC_SESSION_ID`` (the same id used in lesson
    trajectory logs) so causal analysis can join withheld lessons to session
    outcomes. Falls back to a random id when neither is set.
    """
    for key in ("GPTME_SESSION_ID", "CC_SESSION_ID"):
        value = os.environ.get(key)
        if value:
            return value
    return uuid.uuid4().hex


def _get_dropout_log_dir() -> Path:
    """Directory for randomized-dropout logs (``state/lesson-dropout`` default).

    Overridable via ``LESSON_DROPOUT_LOG_DIR``. The default is relative to the
    current working directory so analysis tooling that reads
    ``state/lesson-dropout/*.jsonl`` works without extra configuration.
    """
    return Path(os.environ.get("LESSON_DROPOUT_LOG_DIR", "state/lesson-dropout"))


def _apply_lesson_dropout(matches: list) -> list:
    """Randomly withhold matched lessons for causal LOO measurement.

    For each match, flips a coin with probability ``LESSON_DROPOUT_EPSILON`` to
    withhold it. Withheld lessons are logged to
    ``<log dir>/<session-id>.jsonl`` and removed from the returned list so they
    are not injected. When epsilon is 0 (default), the input list is returned
    unchanged and nothing is logged. When epsilon is > 0, a log record is
    always written (even if no lessons were withheld), so analysis can
    distinguish treatment-group sessions from control.

    Args:
        matches: Match results (already truncated to the injection cap).

    Returns:
        The matches that survived the dropout roll (to be injected).
    """
    epsilon = _get_dropout_epsilon()
    if epsilon <= 0.0:
        return matches

    kept: list = []
    withheld: list[dict] = []
    for match in matches:
        if random.random() < epsilon:
            lesson = match.lesson
            withheld.append({"path": str(lesson.path), "title": lesson.title})
        else:
            kept.append(match)

    _log_dropout(epsilon, withheld)

    return kept


def _log_dropout(epsilon: float, withheld: list[dict]) -> None:
    """Append a randomized-dropout record for causal LOO analysis.

    Failures are logged and swallowed — dropout logging must never break lesson
    injection.
    """
    try:
        session_id = _get_dropout_session_id()
        log_dir = _get_dropout_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "session_id": session_id,
            "epsilon": epsilon,
            "withheld": withheld,
        }
        with open(log_dir / f"{session_id}.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
        logger.debug(
            "Lesson dropout: withheld %d lesson(s) at epsilon=%s (session %s)",
            len(withheld),
            epsilon,
            session_id,
        )
    except Exception as e:
        logger.warning("Failed to log lesson dropout: %s", e)


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

        # Optionally withhold a random subset for causal LOO measurement.
        # No-op unless LESSON_DROPOUT_EPSILON > 0.
        matches = _apply_lesson_dropout(matches)
        if not matches:
            logger.debug("All matched lessons withheld by dropout")
            return messages

        for match in matches:
            if match.lesson.is_stub:
                match.lesson = index.materialize_lesson(match.lesson)

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
