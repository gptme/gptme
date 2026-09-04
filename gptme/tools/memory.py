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
from ..util.ask_execute import execute_with_confirmation
from .base import ToolSpec, ToolUse

try:
    import fcntl as _fcntl

    def _lock_exclusive(f) -> None:
        _fcntl.flock(f, _fcntl.LOCK_EX)

    def _unlock(f) -> None:
        _fcntl.flock(f, _fcntl.LOCK_UN)

except ImportError:
    # Windows: no flock — skip locking (best-effort)
    def _lock_exclusive(f) -> None:
        pass

    def _unlock(f) -> None:
        pass


logger = logging.getLogger(__name__)

instructions = """
Save a persistent memory to recall important context across future sessions.

Use this tool when you learn something worth remembering for future work:
- A user preference that should shape all future responses
- A decision or rationale the user wants preserved
- A fact about the project that isn't obvious from the code

Each memory is cross-runtime: future sessions on gptme, Claude Code, and Codex
all load it automatically at workspace startup.

The first line of the content is the one-line summary shown in the index;
subsequent lines are the full detail body.
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
    memory_dir: Path, slug: str, filename: str, description: str
) -> None:
    """Add or update an entry in MEMORY.md.

    Uses the slug (not the raw name) as the index key so that distinct names
    that normalise to the same slug always update the same single entry rather
    than creating duplicate lines pointing at the same file.

    An exclusive file lock serialises concurrent index updates so that parallel
    sessions cannot race and silently drop each other's entries.
    """
    index_path = memory_dir / "MEMORY.md"
    entry = f"- [{slug}]({filename}) — {description}\n"

    # Open 'a+': creates when absent, positions at EOF, allows read+write.
    with open(index_path, "a+", encoding="utf-8") as f:
        _lock_exclusive(f)
        try:
            f.seek(0)
            content = f.read()
            if not content:
                new_content = f"# Persistent Memory\n\n{entry}"
            else:
                pattern = rf"^- \[{re.escape(slug)}\]\({re.escape(filename)}\).*$"
                if re.search(pattern, content, re.MULTILINE):
                    replacement = entry.rstrip()
                    new_content = re.sub(
                        pattern, lambda _: replacement, content, flags=re.MULTILINE
                    )
                else:
                    if not content.endswith("\n"):
                        content += "\n"
                    new_content = content + entry
            f.seek(0)
            f.truncate()
            f.write(new_content)
        finally:
            _unlock(f)


def save_memory(name: str, content: str, workspace: Path | None = None) -> str:
    """Save a memory to the shared CC memory directory.

    Args:
        name: Memory name (slugified to a safe filename).
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

    safe_desc = description.replace("\\", "\\\\").replace('"', '\\"')
    frontmatter = f'---\nname: {slug}\ndescription: "{safe_desc}"\nmetadata:\n  type: general\n---\n\n'
    file_path.write_text(frontmatter + body + "\n", encoding="utf-8")

    # Use slug as the index key so colliding names update the same entry.
    _update_memory_index(memory_dir, slug, filename, description)

    logger.debug(f"Saved memory '{name}' to {file_path}")
    return str(file_path)


def execute_memory(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None = None,
) -> Generator[Message, None, None]:
    """Execute the memory tool — save a memory entry with user confirmation."""
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

    def _get_path(
        _code: str | None,
        _args: list[str] | None,
        _kwargs: dict[str, str] | None,
    ) -> Path:
        ws = get_workspace()
        mem_dir = get_cc_memory_dir(ws)
        return mem_dir / f"{_slugify(name)}.md"

    def _do_save(
        save_content: str, path: Path | None
    ) -> Generator[Message, None, None]:
        try:
            file_path = save_memory(name, save_content)
            yield Message("system", f"Memory '{name}' saved to `{file_path}`.")
        except OSError as e:
            yield Message("system", f"Error saving memory '{name}': {e}")

    target_path = _get_path(code, args, kwargs)
    confirm_msg = f"Save memory '{name}' to `{target_path}`?"

    yield from execute_with_confirmation(
        code,
        args,
        kwargs,
        execute_fn=_do_save,
        get_path_fn=_get_path,
        preview_lang="markdown",
        confirm_msg=confirm_msg,
    )


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
