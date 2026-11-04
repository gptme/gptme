"""Persistent memory tool backed by the shared layered memory store.

Use explicit user scope for cross-project preferences; project and CC scopes
remain workspace-local. Startup loading depends on the harness configuration.
"""

import os
import shlex
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path

from ..dirs import get_workspace
from ..memory import MemoryEntry, MemoryRoot, MemoryStore, slugify, update_index_line
from ..memory.roots import SCOPES
from ..memory.schema import MemoryParseError
from ..message import Message
from ..util.ask_execute import execute_with_confirmation
from .base import Parameter, ToolSpec, ToolUse, get_current_tool_use

instructions = """
Save preferences, feedback, decisions or project facts for future sessions.

Scopes:
- user: cross-project preferences and feedback.
- project: workspace facts; requires an existing memory/ directory.
- cc: this workspace's Claude Code memory directory.
- agent: configured agent workspace.
- explicit: GPTME_MEMORY_DIRS.
Unavailable scopes error without fallback. Omit scope for the default: explicit,
existing project memory/, else CC (NOT user-wide).

First content line: index summary. Remaining lines: detail body.
Scope and destination appear before approval and in the result.

gptme loads layered MEMORY.md indexes within its startup budget; recall retrieves
full entries. Claude Code natively loads only its project CC root; other roots
need configured recall hooks or explicit recall. Codex needs recall instructions
or an exported AGENTS.md index. Saving does not configure other harnesses.
""".strip()

instructions_format = {
    "markdown": "Use a code block tagged `memory <name> [--scope <scope>]`. The first line is the description; the rest is the body.",
    "xml": 'Use <tool-use><memory args="name --scope user">Summary and body</memory></tool-use>. The args attribute uses shell quoting.',
}


def examples(tool_format):
    content = "User prefers short, direct answers without preamble."
    call = ToolUse(
        "memory",
        ["prefer-short-answers", "--scope", "user"],
        content,
        kwargs={"name": "prefer-short-answers", "content": content, "scope": "user"},
    )
    return f"""
> User: remember that I prefer short answers across projects
> Assistant:
{call.to_output(tool_format)}
> System: Memory 'prefer-short-answers' saved (scope: user).
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


def _save_to_root(name: str, content: str, root: MemoryRoot) -> str:
    lines = content.strip().splitlines()
    description = lines[0].strip() if lines else name
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else content.strip()
    return str(MemoryStore([root]).save(name, description, body, scope=root.scope))


def save_memory(
    name: str,
    content: str,
    workspace: Path | None = None,
    *,
    scope: str | None = None,
) -> str:
    """Save through the layered store, returning the selected file path.

    Omitted scope follows the CLI's default (explicit, project, else CC).
    Explicit scopes must be available in the resolved roots; no fallback occurs.
    """
    store = MemoryStore.from_workspace(workspace or get_workspace())
    return _save_to_root(name, content, store.root(scope))


def prepare_pending_memory(call: ToolUse, workspace: Path | None = None) -> ToolUse:
    """Resolve a server's initial approval without creating directories or files.

    The same normalization as execution is used for native, Markdown and XML
    calls. Invalid calls keep their normal execution-time error reporting.
    """
    try:
        name, _content, scope, _args = _parse_memory_call(
            call.content, call.args, call.kwargs, call._format == "xml"
        )
        from dotenv import dotenv_values

        workspace = (workspace or get_workspace()).resolve()
        # Match the worker's dotenv precedence without loading another
        # conversation's settings into the server's process environment.
        env = dict(os.environ)
        settings = dotenv_values(workspace / ".env")
        for key in ("GPTME_MEMORY_DIRS", "GPTME_AGENT_WORKSPACE"):
            if key not in env and settings.get(key) is not None:
                env[key] = settings[key] or ""
        selected = MemoryStore.from_workspace(workspace, env=env).root(scope)
        root = MemoryRoot(selected.scope, selected.path.resolve())
        target = root.path / f"{slugify(name)}.md"
    except (KeyError, OSError, ValueError):
        return call
    return replace(
        call,
        confirmation_header=f"Save memory '{name}' (scope: {root.scope}) to `{target}`?",
        _memory_root=(root.scope, root.path),
    )


def _parse_memory_call(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    xml: bool,
) -> tuple[str, str, str | None, list[str]]:
    if kwargs and any(
        not isinstance(value, str)
        for key, value in kwargs.items()
        if key in ("name", "content", "scope")
    ):
        raise ValueError("Error: memory name, content and scope must be strings.")
    scope = kwargs.get("scope") if kwargs else None
    if not args and kwargs:
        name = kwargs.get("name", "").strip()
        if name:
            args = [name]
        code = code or kwargs.get("content", "")
    if xml and args and len(args) == 1 and "--scope" in args[0].split():
        try:
            args = shlex.split(args[0])
        except ValueError as e:
            raise ValueError(f"Error parsing memory arguments: {e}") from e
    if not args:
        raise ValueError(
            "Error: memory tool requires a name argument, e.g. `memory my-memory-name`"
        )
    if len(args) != 1:
        if len(args) == 3 and args[1] == "--scope" and scope is None:
            scope = args[2]
        else:
            raise ValueError("Error: expected `memory <name> [--scope <scope>]`.")
    content = (code or "").strip()
    if not content:
        raise ValueError(
            "Error: memory content is empty. Provide the memory text in the code block body."
        )
    return args[0], content, scope, args


def execute_memory(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None = None,
) -> Generator[Message, None, None]:
    """Save a memory after confirming its exact scope, path and content."""
    current_call = get_current_tool_use()
    try:
        name, code, scope, args = _parse_memory_call(
            code, args, kwargs, bool(current_call and current_call._format == "xml")
        )
    except ValueError as e:
        yield Message("system", str(e))
        return

    try:
        # Resolve once: environment/workspace changes during confirmation must not
        # redirect the approved write to another root.
        if current_call and current_call._memory_root is not None:
            root = MemoryRoot(*current_call._memory_root)
        else:
            selected = MemoryStore.from_workspace(get_workspace()).root(scope)
            root = MemoryRoot(selected.scope, selected.path.resolve())
        target_path = root.path / f"{slugify(name)}.md"
    except (KeyError, OSError, ValueError) as e:
        yield Message("system", f"Error selecting memory scope: {e}")
        return

    def _get_path(
        _code: str | None,
        _args: list[str] | None,
        _kwargs: dict[str, str] | None,
    ) -> Path:
        return target_path

    # Retain the destination in the transcript as well as confirmation metadata.
    # Native calls buffer this message, so it cannot substitute for preview_header.
    yield Message(
        "system", f"Save memory '{name}' (scope: {root.scope}) to `{target_path}`?"
    )

    def _do_save(
        save_content: str, path: Path | None
    ) -> Generator[Message, None, None]:
        try:
            file_path = _save_to_root(name, save_content, root)
            yield Message(
                "system",
                f"Memory '{name}' saved (scope: {root.scope}) to `{file_path}`.",
            )
        except (OSError, ValueError, MemoryParseError) as e:
            yield Message("system", f"Error saving memory '{name}': {e}")

    yield from execute_with_confirmation(
        code,
        args,
        kwargs,
        execute_fn=_do_save,
        get_path_fn=_get_path,
        preview_header=f"Save memory '{name}' (scope: {root.scope}) to `{target_path}`?",
        preview_lang="markdown",
    )


tool = ToolSpec(
    name="memory",
    desc="Save a persistent memory with explicit workspace, agent or user-wide scope.",
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
            description="Write scope: user for cross-project feedback; project/cc for workspace facts. Omit for the layered store default (not user-wide).",
            enum=list(SCOPES),
        ),
    ],
)
