"""Persistent memory tool backed by the shared layered memory store.

Use explicit user scope for cross-project preferences; project and CC scopes
remain workspace-local. Startup loading depends on the harness configuration.
"""

from collections.abc import Generator
from pathlib import Path

from ..dirs import get_workspace
from ..memory import MemoryEntry, MemoryRoot, MemoryStore, slugify, update_index_line
from ..memory.roots import SCOPES
from ..memory.schema import MemoryParseError
from ..message import Message
from ..util.ask_execute import execute_with_confirmation
from .base import Parameter, ToolSpec, ToolUse

instructions = """
Save a persistent memory to recall important context across future sessions.

Use this tool for user preferences, feedback, decisions and project facts:
- Set scope="user" for preferences or feedback that should follow the user across projects.
- Use scope="project" for workspace-local facts (requires an existing memory/ directory),
  or scope="cc" for this workspace's Claude Code memory directory.
- Use scope="agent" for a configured agent workspace, or scope="explicit" for
  GPTME_MEMORY_DIRS. Unavailable scopes are errors, never silent fallbacks.
- Omit scope to use the shared store's default: explicit directories, project memory/
  when it exists, otherwise this workspace's CC directory. This is NOT user-wide.

The first content line is the index summary; subsequent lines are the detail body.
The selected scope and destination are shown before saving and in the result.

gptme loads layered MEMORY.md indexes at startup within its prompt budget; full
entries are available through recall. Claude Code's native auto-load covers its
project CC root, not arbitrary user roots. Other roots require configured recall
hooks or explicit recall. Codex needs recall instructions or an exported index
in AGENTS.md; saving a memory does not configure another harness's startup.
""".strip()

instructions_format = {
    "markdown": "Use a code block tagged `memory <name> [--scope <scope>]`. The first line is the description; the rest is the body.",
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


def execute_memory(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None = None,
) -> Generator[Message, None, None]:
    """Save a memory after confirming its exact scope, path and content."""
    scope = kwargs.get("scope") if kwargs else None
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
    if len(args) != 1:
        if len(args) == 3 and args[1] == "--scope" and scope is None:
            scope = args[2]
        else:
            yield Message(
                "system", "Error: expected `memory <name> [--scope <scope>]`."
            )
            return

    content = (code or "").strip()
    if not content:
        yield Message(
            "system",
            "Error: memory content is empty. Provide the memory text in the code block body.",
        )
        return

    try:
        # Resolve once: environment/workspace changes during confirmation must not
        # redirect the approved write to another root.
        root = MemoryStore.from_workspace(get_workspace()).root(scope)
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

    # Confirmation previews are also the editable text. Keep destination
    # metadata separate so editing a memory cannot save that header as a fact.
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
