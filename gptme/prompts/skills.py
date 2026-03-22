import logging
from collections.abc import Generator
from xml.sax.saxutils import escape as xml_escape
from xml.sax.saxutils import quoteattr

from ..message import Message
from ..tools import ToolFormat
from . import _xml_section

logger = logging.getLogger(__name__)


def prompt_skills_summary(
    tool_format: ToolFormat = "markdown",
) -> Generator[Message, None, None]:
    """Generate a compact skills summary for the system prompt.

    Lists available skills (lessons with name/description metadata) so the agent
    knows what skills are available without loading full content. Skills can be
    read on-demand using `cat <path>`.

    Note: This should only be included when tools are enabled, since loading
    skills on-demand requires tool access (e.g., the shell tool to run `cat`).
    """
    try:
        from ..lessons.index import LessonIndex

        index = LessonIndex()

        if not index.lessons:
            return

        # Filter to skills only (have metadata.name)
        skills = [item for item in index.lessons if item.metadata.name]

        if not skills:
            return

        # Sort by name
        skills = sorted(skills, key=lambda s: s.metadata.name or "")

        if tool_format == "xml":
            skill_entries = []
            for skill in skills:
                name = quoteattr(skill.metadata.name or "")
                raw_desc = skill.metadata.description or ""
                if len(raw_desc) > 80:
                    raw_desc = raw_desc[:77] + "..."
                desc = xml_escape(raw_desc)
                path = quoteattr(str(skill.path))
                depends_attr = ""
                if skill.metadata.depends:
                    depends_attr = (
                        f" depends={quoteattr(', '.join(skill.metadata.depends))}"
                    )
                skill_entries.append(
                    f"  <skill name={name} path={path}{depends_attr}>{desc}</skill>"
                )
            content = "\n".join(skill_entries)
            yield Message("system", _xml_section("skills", content))
        else:
            lines = ["## Available Skills\n"]
            lines.append("Load on-demand with `cat <path>`:\n")

            for skill in skills:
                name = skill.metadata.name or ""
                desc = skill.metadata.description or ""
                # Truncate description to keep it compact
                if len(desc) > 80:
                    desc = desc[:77] + "..."
                entry = f"- **{name}**: {desc}"
                if skill.metadata.depends:
                    deps_str = ", ".join(skill.metadata.depends)
                    entry += f" (depends: {deps_str})"
                lines.append(entry)
                lines.append(f"  `{skill.path}`")

            lines.append(f"\n*{len(skills)} skills available*")

            yield Message("system", "\n".join(lines))

    except Exception as e:
        logger.warning(f"Failed to generate skills summary: {e}")
        return
