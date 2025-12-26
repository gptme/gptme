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


def skills(ctx: "CommandContext") -> Generator["Message", None, None]:
    """Skills system commands. Use /skills without args for help."""
    from ..message import Message

    args = ctx.full_args.strip()

    if not args:
        yield Message(role="system", content=_skills_help())
        return

    parts = args.split(maxsplit=1)
    subcommand = parts[0]
    subargs = parts[1] if len(parts) > 1 else ""

    result = ""
    if subcommand == "list":
        result = _skills_list()
    elif subcommand == "read":
        if not subargs:
            result = "Usage: /skills read <skill-name>"
        else:
            result = _skills_read(subargs)
    elif subcommand == "all":
        result = _skills_all()
    else:
        result = f"Unknown subcommand: {subcommand}\n\n{_skills_help()}"

    yield Message(role="system", content=result)


def _skills_help() -> str:
    """Show skills command help."""
    return """# Skills Commands

Browse and read available skills (Anthropic format) and lessons.

Available commands:
- `/skills` - Show this help
- `/skills list` - List available skills
- `/skills read <name>` - Read a specific skill or lesson
- `/skills all` - List both skills and lessons

Skills have `name` and `description` in frontmatter (Anthropic format).
Lessons have `match.keywords` for auto-inclusion.

Examples:
- `/skills list` - Show all skills
- `/skills read python-repl` - Read the python-repl skill
- `/skills all` - Show skills and lessons together
"""


def _skills_list() -> str:
    """List available skills (Anthropic format with name/description)."""
    try:
        index = LessonIndex()

        if not index.lessons:
            return "No skills or lessons found."

        # Filter to skills only (have metadata.name)
        skills = [item for item in index.lessons if item.metadata.name]

        if not skills:
            return "No skills found. Skills have `name` and `description` in frontmatter.\n\nUse `/skills all` to see lessons too."

        # Sort by name
        skills = sorted(skills, key=lambda s: s.metadata.name or "")

        lines = ["# Available Skills\n"]

        for skill in skills:
            name = skill.metadata.name
            desc = skill.metadata.description or skill.description or ""
            lines.append(f"- **{name}**: {desc}")

        lines.append(f"\nTotal: {len(skills)} skills")
        lines.append("\nUse `/skills read <name>` to view a skill.")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Failed to list skills: {e}")
        return f"Error listing skills: {e}"


def _skills_read(name: str) -> str:
    """Read a specific skill or lesson by name."""
    try:
        index = LessonIndex()

        if not index.lessons:
            return "No skills or lessons found."

        name_lower = name.lower()

        # First check skills (by metadata.name)
        for item in index.lessons:
            if item.metadata.name and name_lower in item.metadata.name.lower():
                return f"# {item.metadata.name}\n\n{item.body}"

        # Then check lessons (by title or filename)
        for item in index.lessons:
            title_match = name_lower in item.title.lower()
            file_match = name_lower in item.path.stem.lower()

            if title_match or file_match:
                return f"# {item.title}\n\n{item.body}"

        return f"Skill or lesson not found: {name}"

    except Exception as e:
        logger.error(f"Failed to read skill: {e}")
        return f"Error reading skill: {e}"


def _skills_all() -> str:
    """List both skills and lessons."""
    try:
        index = LessonIndex()

        if not index.lessons:
            return "No skills or lessons found."

        # Separate skills and lessons
        skills_list = [item for item in index.lessons if item.metadata.name]
        lessons_list = [item for item in index.lessons if not item.metadata.name]

        lines = []

        # Skills section
        if skills_list:
            lines.append("# Skills\n")
            skills_list = sorted(skills_list, key=lambda s: s.metadata.name or "")
            for skill in skills_list:
                name = skill.metadata.name
                desc = skill.metadata.description or skill.description or ""
                lines.append(f"- **{name}**: {desc}")
            lines.append("")

        # Lessons section (grouped by category)
        if lessons_list:
            lines.append("# Lessons\n")
            by_category: dict[str, list] = {}
            for lesson in lessons_list:
                by_category.setdefault(lesson.category, []).append(lesson)

            for cat in sorted(by_category.keys()):
                cat_lessons = sorted(by_category[cat], key=lambda x: x.title)
                lines.append(f"## {cat.title()}\n")
                for lesson in cat_lessons:
                    keywords = lesson.metadata.keywords[:3]
                    kw_str = f" ({', '.join(keywords)})" if keywords else ""
                    lines.append(f"- **{lesson.title}**{kw_str}")
                lines.append("")

        lines.append(f"\nTotal: {len(skills_list)} skills, {len(lessons_list)} lessons")
        lines.append("Use `/skills read <name>` to view content.")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Failed to list all: {e}")
        return f"Error listing all: {e}"


def register_lesson_commands():
    """Register lesson and skills commands with gptme."""
    from ..commands import register_command

    register_command("lesson", lesson)
    register_command("skills", skills)
