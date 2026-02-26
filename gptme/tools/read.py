"""
Read the contents of a file.

Provides a sandboxed file reading capability that works without shell access.
Useful for restricted tool sets (e.g., ``--tools read,patch,save``).
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
Read the content of a file, optionally specifying a line range.
The path can be relative or absolute.
Output includes line numbers for easy reference.
""".strip()

instructions_format = {
    "markdown": "Use a code block with the language tag: `read <path>` to read a file.",
}


def examples(tool_format):
    return f"""
> User: read hello.py
> Assistant:
{ToolUse("read", ["hello.py"], "hello.py").to_output(tool_format)}
> System: ```hello.py
>    1\tprint("Hello world")
>    2\tprint("Goodbye world")
> ```
""".strip()


def _get_read_path(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> Path | None:
    """Extract the file path from args or kwargs."""
    if kwargs and "path" in kwargs:
        return Path(kwargs["path"]).expanduser()
    if args:
        return Path(" ".join(args)).expanduser()
    if code and code.strip():
        return Path(code.strip()).expanduser()
    return None


def execute_read(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Read a file and return its contents with line numbers."""
    path = _get_read_path(code, args, kwargs)
    if not path:
        yield Message("system", "No path provided")
        return

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

    if not path.is_file():
        yield Message("system", f"Not a file: {path}")
        return

    # Parse optional line range from kwargs
    start_line = 1
    end_line = None
    if kwargs:
        if "start_line" in kwargs:
            try:
                start_line = int(kwargs["start_line"])
            except ValueError:
                pass
        if "end_line" in kwargs:
            try:
                end_line = int(kwargs["end_line"])
            except ValueError:
                pass

    try:
        content = path.read_text()
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
    end_idx = min(total_lines, end_line) if end_line else total_lines
    selected = lines[start_idx:end_idx]

    # Format with line numbers (cat -n style)
    width = len(str(end_idx)) if end_idx > 0 else 1
    numbered = "\n".join(
        f"{i:>{width}}\t{line}" for i, line in enumerate(selected, start=start_idx + 1)
    )

    range_info = ""
    if start_line > 1 or end_line:
        shown = f"{start_idx + 1}-{end_idx}"
        range_info = f" (lines {shown} of {total_lines})"

    yield Message("system", md_codeblock(f"{path}{range_info}", numbered))


tool = ToolSpec(
    name="read",
    desc="Read the content of a file",
    instructions=instructions,
    instructions_format=instructions_format,
    examples=examples,
    execute=execute_read,
    block_types=["read"],
    parameters=[
        Parameter(
            name="path",
            type="string",
            description="The path of the file to read",
            required=True,
        ),
        Parameter(
            name="start_line",
            type="integer",
            description="Line number to start reading from (1-indexed)",
            required=False,
        ),
        Parameter(
            name="end_line",
            type="integer",
            description="Line number to stop reading at (inclusive)",
            required=False,
        ),
    ],
    disabled_by_default=True,
)
__doc__ = tool.get_doc(__doc__)
