"""
Persistent memory tool for gptme.

Saves memories to the shared Claude Code memory path so they are readable
by future sessions on any runtime (CC, gptme, Codex).

Memory files are written to:
  ~/.claude/projects/<workspace-hash>/memory/<name>.md

And an entry is added to MEMORY.md (the index file), which is auto-loaded
by gptme (via prompt_workspace) and by Claude Code in every session.

Usage:
  memory save <name>
  <description line>
  <body content>
"""

import logging
import re
from collections.abc import Generator
from pathlib import Path

from ..dirs import get_cc_memory_dir, get_workspace
from ..message import Message
from .base import ToolSpec, ToolUse

logger = logging.getLogger(__name__)

instructions = """
Save a persistent memory that will be automatically loaded in future sessions
across all runtimes (gptme, Claude Code, Codex).

Memories are stored in the shared Claude Code memory directory and loaded
automatically when a session starts in this workspace.

Use memories to remember:
- Important facts about the user or project
- Patterns and preferences learned from the conversation
- Decisions and their rationale

The first line of the content is treated as the one-line description
(shown in the MEMORY.md index); the rest is the memory body.
""".strip()

instructions_format = {
    "markdown": "Use a code block with the language tag `memory <name>` to save a memory. The first line is the description; the rest is the body.",
}


def examples(tool_format):
    return f"""
> User: remember that I prefer short answers
> Assistant:
{ToolUse("memory", ["prefer-short-answers"], "User prefers short, direct answers without preamble.").to_output(tool_format)}
> System: Memory 'prefer-short-answers' saved.
""".strip()


def _slugify(name: str) -> str:
    """Convert a name to a safe filename slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "memory"


def _update_memory_index(
    memory_dir: Path, name: str, filename: str, description: str
) -> None:
    """Add or update an entry in MEMORY.md."""
    index_path = memory_dir / "MEMORY.md"
    entry = f"- [{name}]({filename}) — {description}\n"

    if not index_path.exists():
        index_path.write_text(f"# Persistent Memory\n\n{entry}", encoding="utf-8")
        return

    content = index_path.read_text(encoding="utf-8")
    # Update existing entry for this name/file if present
    pattern = rf"^- \[{re.escape(name)}\]\({re.escape(filename)}\).*$"
    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(pattern, entry.rstrip(), content, flags=re.MULTILINE)
        index_path.write_text(content, encoding="utf-8")
    else:
        # Append new entry
        if not content.endswith("\n"):
            content += "\n"
        index_path.write_text(content + entry, encoding="utf-8")


def save_memory(name: str, content: str, workspace: Path | None = None) -> str:
    """Save a memory to the shared CC memory directory.

    Args:
        name: Memory name (used as filename slug and index key).
        content: Memory content. First line is the description; rest is the body.
        workspace: Workspace root (defaults to get_workspace()).

    Returns:
        Path to the saved memory file as a string.
    """
    if workspace is None:
        workspace = get_workspace()

    memory_dir = get_cc_memory_dir(workspace)
    memory_dir.mkdir(parents=True, exist_ok=True)

    slug = _slugify(name)
    filename = f"{slug}.md"
    file_path = memory_dir / filename

    lines = content.strip().splitlines()
    description = lines[0].strip() if lines else name
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else content.strip()

    frontmatter = f"---\nname: {slug}\ndescription: {description}\nmetadata:\n  type: general\n---\n\n"
    file_path.write_text(frontmatter + body + "\n", encoding="utf-8")

    _update_memory_index(memory_dir, name, filename, description)

    logger.debug(f"Saved memory '{name}' to {file_path}")
    return str(file_path)


def execute_memory(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None = None,
) -> Generator[Message, None, None]:
    """Execute the memory tool — save a memory entry."""
    if not args:
        yield Message(
            "system",
            "Error: memory tool requires a name argument, e.g. `memory my-memory-name`",
        )
        return

    name = args[0]

    content = (code or "").strip()
    if not content:
        yield Message(
            "system",
            "Error: memory content is empty. Provide the memory text in the code block body.",
        )
        return

    try:
        file_path = save_memory(name, content)
        yield Message("system", f"Memory '{name}' saved to `{file_path}`.")
    except OSError as e:
        yield Message("system", f"Error saving memory '{name}': {e}")


tool = ToolSpec(
    name="memory",
    desc="Save a persistent memory to the shared cross-runtime memory store (readable by CC, gptme, Codex).",
    instructions=instructions,
    instructions_format=instructions_format,
    examples=examples,
    execute=execute_memory,
    block_types=["memory"],
    parameters=[],
)
