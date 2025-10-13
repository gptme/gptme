"""
Lesson system tool for gptme.

Provides structured lessons with metadata that can be automatically included in context.
Similar to .cursorrules but with keyword-based triggering.
"""

import logging
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

    # Build match context
    context = MatchContext(message=user_msg.content)

    # Find matching lessons
    try:
        index = LessonIndex()
        if not index.lessons:
            logger.debug("No lessons found in index")
            return

        matcher = LessonMatcher()
        matches = matcher.match(index.lessons, context)

        if not matches:
            logger.debug("No matching lessons found")
            return

        # Limit to top N
        matches = matches[:max_lessons]

        # Format lessons for inclusion
        lesson_content = _format_lessons(matches)

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
