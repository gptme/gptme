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
from .base import (
    Parameter,
    ToolSpec,
    ToolUse,
)

instructions = """
Read the content of one or more files, or list the contents of a directory.
Paths can be relative or absolute.
For files, output includes line numbers for easy reference.
For directories, output shows a flat listing of immediate files and subdirectories.

To read multiple files in a single call, put one path per line in the code block.
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
    if kwargs and "path" in kwargs:
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

    # Format with line numbers (cat -n style)
    width = len(str(end_idx)) if end_idx > 0 else 1
    numbered = "\n".join(
        f"{i:>{width}}\t{line}" for i, line in enumerate(selected, start=start_idx + 1)
    )

    range_info = ""
    if start_line > 1 or end_line is not None:
        shown = f"{start_idx + 1}-{end_idx}"
        range_info = f" (lines {shown} of {total_lines})"

    yield Message("system", md_codeblock(f"{path}{range_info}", numbered))


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
            description="The path of the file or directory to read",
            required=True,
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
    disabled_by_default=True,
)
__doc__ = tool.get_doc(__doc__)
