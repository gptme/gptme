"""
Persistent memory tool for gptme.

Saves memories to the layered memory store so they are readable by future
sessions on any runtime (CC, gptme, Codex).

Memory files are written to the resolved write root:

  - project  (default when <workspace>/memory/ exists): <workspace>/memory/
  - cc       (default otherwise): ~/.claude/projects/<workspace-hash>/memory/
  - user     (explicit scope): <config-dir>/memory/  — shared across workspaces

An entry is added to MEMORY.md (the index file), which is auto-loaded
by gptme (via prompt_workspace) and by Claude Code in every session.

Usage:
  memory save <name>
  <description line>
  <body content>
"""

import logging
from collections.abc import Generator
from pathlib import Path

from ..dirs import get_workspace
from ..memory import MemoryEntry, MemoryStore, slugify, update_index_line
from ..memory.roots import SCOPES
from ..memory.schema import MemoryParseError
from ..message import Message
from ..util.ask_execute import execute_with_confirmation
from .base import Parameter, ToolSpec, ToolUse

logger = logging.getLogger(__name__)

instructions = """
Save a persistent memory to recall important context across future sessions.

Use this tool when you learn something worth remembering for future work:
- A user preference that should shape all future responses
- A decision or rationale the user wants preserved
- A fact about the project that isn't obvious from the code

Each memory is cross-runtime: future sessions on gptme, Claude Code, and Codex
all load it automatically at workspace startup.

Scope controls where the memory is written:
- (omit) — default: writes to the nearest writable root (project > cc).
  Use for project-specific facts, decisions, and local context.
- "user" — writes to the global user root, shared across every workspace.
  Use for cross-project preferences, workflow corrections, and user feedback
  that should apply regardless of which repo you are working in.
  Important: use "user" scope when invoked from a temporary feature worktree
  to avoid orphaning the memory under a throwaway workspace identity.

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


# Kept as module-level names for callers and tests; the implementation lives
# in gptme.memory so gptme-util and other harnesses share it.
_slugify = slugify


def _update_memory_index(
    memory_dir: Path, slug: str, filename: str, description: str
) -> None:
    """Add or update an entry line in MEMORY.md (delegates to gptme.memory)."""
    update_index_line(
        memory_dir,
        MemoryEntry(name=slug, description=description, path=memory_dir / filename),
    )


def save_memory(
    name: str,
    content: str,
    workspace: Path | None = None,
    scope: str | None = None,
) -> str:
    """Save a memory to the layered memory store.

    Args:
        name: Memory name (slugified to a safe filename).
        content: Memory content. First line is the description; rest is the body.
        workspace: Workspace root (defaults to get_workspace()).
        scope: Write root to target ('project', 'cc', 'user', …).
            Defaults to the nearest writable root (project > cc).

    Returns:
        Path to the saved memory file as a string.
    """
    if workspace is None:
        workspace = get_workspace()

    if scope is not None and scope not in SCOPES:
        valid = ", ".join(repr(s) for s in SCOPES)
        raise ValueError(f"invalid scope {scope!r}; valid values: {valid}")

    lines = content.strip().splitlines()
    description = lines[0].strip() if lines else name
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else content.strip()

    store = MemoryStore.from_workspace(workspace)
    return str(store.save(name, description, body, scope=scope))


def execute_memory(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None = None,
) -> Generator[Message, None, None]:
    """Execute the memory tool — save a memory entry with user confirmation."""
    # Normalize native kwargs → legacy positional form
    if not args and kwargs:
        name_from_kwargs = kwargs.get("name", "").strip()
        content_from_kwargs = kwargs.get("content", "").strip()
        if name_from_kwargs:
            args = [name_from_kwargs]
        if content_from_kwargs and not code:
            code = content_from_kwargs

    if not args:
        yield Message(
            "system",
            "Error: memory tool requires a name argument, e.g. `memory my-memory-name`",
        )
        return

    name = args[0]
    scope: str | None = (kwargs or {}).get("scope", "").strip() or None

    content = (code or "").strip()
    if not content:
        yield Message(
            "system",
            "Error: memory content is empty. Provide the memory text in the code block body.",
        )
        return

    if scope is not None and scope not in SCOPES:
        valid = ", ".join(repr(s) for s in SCOPES)
        yield Message(
            "system",
            f"Error: invalid memory scope {scope!r}. Valid values: {valid}.",
        )
        return

    def _get_path(
        _code: str | None,
        _args: list[str] | None,
        _kwargs: dict[str, str] | None,
    ) -> Path:
        ws = get_workspace()
        store = MemoryStore.from_workspace(ws)
        mem_dir = store.root(scope).path
        return mem_dir / f"{slugify(name)}.md"

    def _do_save(
        save_content: str, path: Path | None
    ) -> Generator[Message, None, None]:
        try:
            file_path = save_memory(name, save_content, scope=scope)
            scope_label = f" (scope: {scope})" if scope else ""
            yield Message(
                "system", f"Memory '{name}' saved to `{file_path}`{scope_label}."
            )
        except (OSError, ValueError, MemoryParseError) as e:
            yield Message("system", f"Error saving memory '{name}': {e}")

    target_path = _get_path(code, args, kwargs)
    scope_label = f" (scope: {scope})" if scope else ""
    confirm_msg = f"Save memory '{name}' to `{target_path}`{scope_label}?"

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
    parameters=[
        Parameter(
            name="name",
            type="string",
            description="Slug-friendly name for the memory (e.g. 'prefer-short-answers').",
            required=True,
        ),
        Parameter(
            name="content",
            type="string",
            description="The memory body text. First line is the one-line summary; rest is the detail.",
            required=True,
        ),
        Parameter(
            name="scope",
            type="string",
            description=(
                "Write root: 'user' for cross-project preferences/feedback (shared across all workspaces), "
                "'cc' for the current Claude Code workspace, 'project' for the project memory/ directory. "
                "Omit to use the default (project > cc). "
                "Always use 'user' when saving from a temporary worktree to avoid orphaning the memory."
            ),
            required=False,
        ),
    ],
)
