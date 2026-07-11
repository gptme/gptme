"""
Read the contents of one or more files, or list the contents of a directory.

Provides a sandboxed file reading capability that works without shell access.
Useful for restricted tool sets (e.g., ``--tools read,patch,save``).

Multiple paths can be passed in the code block (one per line) to read several
files in a single tool call, reducing roundtrips when exploring a codebase.
"""

from collections.abc import Generator
from pathlib import Path

from ..message import Message
from ..util.context import md_codeblock
from ..util.context_savings import record_context_savings
from ..util.output_storage import save_large_output
from ..util.tokens import len_tokens
from .base import (
    Parameter,
    ToolSpec,
    ToolUse,
)
from .pruner import plan_tool_output_prune

instructions = """
Read the content of one or more files, or list the contents of a directory.
Paths can be relative or absolute.
For files, output includes line numbers for easy reference.
For directories, output shows a flat listing of immediate files and subdirectories.

### When to use read

Reading a file directly gives you its exact, current content with line numbers —
eliminating guesswork from memory, file names, or comments. Prefer `read` over
those shortcuts when the file itself is the source of truth.

To read multiple files in a single call, put one path per line in the code block.
Lines beginning with '#' are treated as comments and skipped.
The line-range parameters (start_line, end_line) only apply when reading a single file.
""".strip()

instructions_format = {
    "markdown": (
        "Use a code block with the language tag: `read <path>` to read a file. "
        "For multiple files, place one path per line inside the code block."
    ),
}


def examples(tool_format):
    batch_paths = "hello.py\ngoodbye.py"
    return f"""
> User: read hello.py
> Assistant:
{ToolUse("read", ["hello.py"], "").to_output(tool_format)}
> System: ```hello.py
>    1\tprint("Hello world")
>    2\tprint("Goodbye world")
> ```

> User: read both source files
> Assistant:
{ToolUse("read", [], batch_paths).to_output(tool_format)}
> System: ```hello.py
>    1\tprint("Hello world")
> ```
> ```goodbye.py
>    1\tprint("Goodbye world")
> ```
""".strip()


def _get_read_paths(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> list[Path]:
    """Extract one or more file paths from args or kwargs.

    The kwargs and args entry points always carry a single path (tool-format
    callers and CLI invocations). The markdown code-block entry point may
    contain multiple newline-separated paths for batch reading.
    """
    if kwargs and kwargs.get("path"):
        return [Path(kwargs["path"]).expanduser()]
    if args:
        return [Path(" ".join(args)).expanduser()]
    if code and code.strip():
        paths = [
            Path(line.strip()).expanduser()
            for line in code.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return paths
    return []


_MAX_DIR_ENTRIES = 100


def _list_directory(path: Path) -> Generator[Message, None, None]:
    """List directory contents in a tree-like format."""
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        yield Message("system", f"Permission denied: {path}")
        return

    if not entries:
        yield Message("system", md_codeblock(str(path), "(empty directory)"))
        return

    lines = []
    truncated = len(entries) > _MAX_DIR_ENTRIES
    for entry in entries[:_MAX_DIR_ENTRIES]:
        name = entry.name + ("/" if entry.is_dir() else "")
        lines.append(name)

    if truncated:
        lines.append(f"... and {len(entries) - _MAX_DIR_ENTRIES} more entries")

    summary = f"{len(entries)} entries"
    yield Message(
        "system",
        md_codeblock(f"{path} ({summary})", "\n".join(lines)),
    )


def _current_logdir() -> Path | None:
    from ..logmanager import LogManager

    manager = LogManager.get_current_log()
    return manager.logdir if manager and manager.logdir else None


def _read_one(
    path: Path,
    start_line: int = 1,
    end_line: int | None = None,
) -> Generator[Message, None, None]:
    """Read a single file or directory and yield messages with the result."""
    # Path traversal protection: validate relative paths stay within cwd
    path_display = path
    path = path.expanduser().resolve()
    if not path_display.is_absolute():
        cwd = Path.cwd().resolve()
        try:
            path.relative_to(cwd)
        except ValueError:
            yield Message(
                "system",
                f"Path traversal detected: {path_display} resolves to {path} "
                f"which is outside current directory {cwd}",
            )
            return

    if not path.exists():
        yield Message("system", f"File not found: {path}")
        return

    if path.is_dir():
        yield from _list_directory(path)
        return

    if not path.is_file():
        yield Message("system", f"Not a file: {path}")
        return

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        yield Message("system", f"Cannot read binary file: {path}")
        return
    except PermissionError:
        yield Message("system", f"Permission denied: {path}")
        return

    lines = content.splitlines()
    total_lines = len(lines)

    # Apply line range
    start_idx = max(0, start_line - 1)
    end_idx = min(total_lines, end_line) if end_line is not None else total_lines
    selected = lines[start_idx:end_idx]
    display_pairs = list(enumerate(selected, start=start_idx + 1))

    pruned_message_prefix = ""
    if display_pairs:
        display_text = "\n".join(line for _, line in display_pairs)
        plan = plan_tool_output_prune("read", display_text, context_label=str(path))
        if plan:
            display_pairs = [
                pair
                for start, end in plan.ranges
                for pair in display_pairs[start - 1 : end]
            ]
            logdir = _current_logdir()
            saved_path: Path | None = None
            if logdir:
                _, saved_path = save_large_output(
                    content=display_text,
                    logdir=logdir,
                    output_type="read",
                    command_info=str(path),
                )

            pruned_message_prefix = f"Pruned to {plan.kept_lines} of {plan.total_lines} lines for the current query."
            if saved_path:
                pruned_message_prefix += f" Full output saved to {saved_path}; read that file for the complete result."
            else:
                pruned_message_prefix += " Full output was not saved because no conversation logdir is active."

            if logdir:
                final_preview = md_codeblock(
                    str(path),
                    "\n".join(f"{line_no}\t{line}" for line_no, line in display_pairs),
                )
                try:
                    kept_tokens = len_tokens(
                        pruned_message_prefix + "\n\n" + final_preview, plan.model
                    )
                except Exception:
                    kept_tokens = plan.kept_tokens
                record_context_savings(
                    logdir=logdir,
                    source="read",
                    original_tokens=plan.original_tokens,
                    kept_tokens=kept_tokens,
                    command_info=str(path),
                    saved_path=saved_path,
                )

    # Format with line numbers (cat -n style)
    last_line_number = display_pairs[-1][0] if display_pairs else end_idx
    width = len(str(last_line_number)) if last_line_number > 0 else 1
    numbered = "\n".join(
        f"{line_no:>{width}}\t{line}" for line_no, line in display_pairs
    )

    range_info = ""
    if start_line > 1 or end_line is not None:
        shown = f"{start_idx + 1}-{end_idx}"
        range_info = f" (lines {shown} of {total_lines})"

    body = md_codeblock(f"{path}{range_info}", numbered)
    if pruned_message_prefix:
        body = pruned_message_prefix + "\n\n" + body

    yield Message("system", body)


def execute_read(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Read one or more files (or a directory) and return their contents."""
    paths = _get_read_paths(code, args, kwargs)
    if not paths:
        yield Message("system", "No path provided")
        return

    # Parse optional line range from kwargs (single-path only)
    start_line = 1
    end_line = None
    if kwargs:
        if "start_line" in kwargs:
            try:
                start_line = int(kwargs["start_line"])
            except ValueError:
                yield Message(
                    "system",
                    f"Invalid start_line: {kwargs['start_line']!r} (expected integer)",
                )
                return
        if "end_line" in kwargs:
            try:
                end_line = int(kwargs["end_line"])
            except ValueError:
                yield Message(
                    "system",
                    f"Invalid end_line: {kwargs['end_line']!r} (expected integer)",
                )
                return

    # Line ranges only meaningful for single-file reads.
    if len(paths) > 1 and (start_line != 1 or end_line is not None):
        yield Message(
            "system",
            "start_line/end_line ignored when reading multiple paths",
        )
        start_line, end_line = 1, None

    for path in paths:
        yield from _read_one(path, start_line=start_line, end_line=end_line)


tool = ToolSpec(
    name="read",
    desc="Read the content of one or more files, or list directory contents",
    instructions=instructions,
    instructions_format=instructions_format,
    examples=examples,
    execute=execute_read,
    block_types=["read"],
    parameters=[
        Parameter(
            name="path",
            type="string",
            description="The path of the file or directory to read. "
            "Optional when multiple paths are supplied in the code block (one per line).",
            required=False,
        ),
        Parameter(
            name="start_line",
            type="integer",
            description="Line number to start reading from (1-indexed). "
            "Ignored when reading multiple files.",
            required=False,
        ),
        Parameter(
            name="end_line",
            type="integer",
            description="Line number to stop reading at (inclusive). "
            "Ignored when reading multiple files.",
            required=False,
        ),
    ],
    hints=frozenset({"file-ops", "read-only"}),
    disabled_by_default=True,
)
__doc__ = tool.get_doc(__doc__)
