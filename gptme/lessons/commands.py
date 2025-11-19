"""CLI commands for lesson management."""

import logging
from collections.abc import Generator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..commands import CommandContext
    from ..message import Message

from .index import LessonIndex

logger = logging.getLogger(__name__)


def lesson(ctx: "CommandContext") -> Generator["Message", None, None]:
    """Lesson system commands. Use /lesson without args for help."""
    from ..message import Message

    args = ctx.full_args.strip()

    if not args:
        yield Message(role="system", content=_lesson_help())
        return

    parts = args.split(maxsplit=1)
    subcommand = parts[0]
    subargs = parts[1] if len(parts) > 1 else ""

    result = ""
    if subcommand == "list":
        result = _lesson_list(subargs)
    elif subcommand == "search":
        if not subargs:
            result = "Usage: /lesson search <query>"
        else:
            result = _lesson_search(subargs)
    elif subcommand == "show":
        if not subargs:
            result = "Usage: /lesson show <lesson-name>"
        else:
            result = _lesson_show(subargs)
    elif subcommand == "refresh":
        result = _lesson_refresh()
    else:
        result = f"Unknown subcommand: {subcommand}\n\n{_lesson_help()}"

    yield Message(role="system", content=result)


def _lesson_help() -> str:
    """Show lesson command help."""
    return """# Lesson Commands

Available commands:
- `/lesson list [category]` - List available lessons
- `/lesson search <query>` - Search lessons
- `/lesson show <name>` - Show specific lesson
- `/lesson refresh` - Refresh lesson index

Examples:
- `/lesson list` - List all lessons
- `/lesson list tools` - List lessons in 'tools' category
- `/lesson search patch` - Search for 'patch'
- `/lesson show patch-placeholders` - Show specific lesson
"""


def _lesson_list(category: str = "") -> str:
    """List available lessons."""
    try:
        index = LessonIndex()

        if not index.lessons:
            return "No lessons found. Check that lesson directories exist."

        # Filter by category if specified
        lessons = index.get_by_category(category) if category else index.lessons

        if not lessons:
            return f"No lessons found in category: {category}"

        # Group by category
        by_category: dict[str, list] = {}
        for lesson in lessons:
            by_category.setdefault(lesson.category, []).append(lesson)

        # Format output
        lines = ["# Available Lessons\n"]

        for cat in sorted(by_category.keys()):
            cat_lessons = sorted(by_category[cat], key=lambda lesson: lesson.title)
            lines.append(f"## {cat.title()}\n")

            for lesson in cat_lessons:
                keywords = ", ".join(lesson.metadata.keywords[:3])
                if keywords:
                    keywords = f" ({keywords})"
                lines.append(f"- **{lesson.title}**{keywords}")
                if lesson.description:
                    lines.append(f"  {lesson.description}")
                lines.append("")

        lines.append(f"\nTotal: {len(lessons)} lessons")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Failed to list lessons: {e}")
        return f"Error listing lessons: {e}"


def _lesson_search(query: str) -> str:
    """Search for lessons."""
    try:
        index = LessonIndex()

        if not index.lessons:
            return "No lessons found."

        results = index.search(query)

        if not results:
            return f"No lessons found matching '{query}'"

        lines = [f"# Lessons matching '{query}'\n"]

        for lesson in results[:10]:  # Top 10
            keywords = ", ".join(lesson.metadata.keywords)
            lines.append(f"## {lesson.title}")
            lines.append(f"**Category**: {lesson.category}")
            if keywords:
                lines.append(f"**Keywords**: {keywords}")
            if lesson.description:
                lines.append(f"{lesson.description}")
            lines.append("")

        if len(results) > 10:
            lines.append(f"\n... and {len(results) - 10} more")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Failed to search lessons: {e}")
        return f"Error searching lessons: {e}"


def _lesson_show(lesson_name: str) -> str:
    """Show a specific lesson."""
    try:
        index = LessonIndex()

        if not index.lessons:
            return "No lessons found."

        # Try to find by title or filename
        lesson_name_lower = lesson_name.lower()

        for lesson in index.lessons:
            title_match = lesson_name_lower in lesson.title.lower()
            file_match = lesson_name_lower in lesson.path.stem.lower()

            if title_match or file_match:
                return f"# {lesson.title}\n\n{lesson.body}"

        return f"Lesson not found: {lesson_name}"

    except Exception as e:
        logger.error(f"Failed to show lesson: {e}")
        return f"Error showing lesson: {e}"


def _lesson_refresh() -> str:
    """Refresh the lesson index."""
    try:
        index = LessonIndex()
        index.refresh()
        return f"✓ Refreshed lesson index ({len(index.lessons)} lessons)"
    except Exception as e:
        logger.error(f"Failed to refresh lessons: {e}")
        return f"Error refreshing lessons: {e}"


def register_lesson_commands():
    """Register lesson commands with gptme."""
    from ..commands import register_command

    register_command("lesson", lesson)
