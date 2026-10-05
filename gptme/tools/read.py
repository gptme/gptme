"""
Read the contents of one or more files, or list the contents of a directory.

Provides a sandboxed file reading capability that works without shell access.
Useful for restricted tool sets (e.g., ``--tools read,patch,save``).

Multiple paths can be passed in the code block (one per line) to read several
files in a single tool call, reducing roundtrips when exploring a codebase.
"""

import codecs
import os
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
    get_current_tool_use,
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
For files over 1 MiB, always supply start_line and end_line to read a section at a
time; advance start_line to the next line after the shown range to paginate through
the file. hashline_edit is not available for large-file ranged reads; use the save
or patch tool to edit large files.
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
# Files above this size are never loaded whole: a whole-file read needs an
# explicit line range, which is streamed and capped at the same size.
_MAX_READ_BYTES = 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_READ_ROOT_ENV = "GPTME_READ_ROOT"


def _configured_read_root() -> tuple[Path | None, str | None]:
    """Return the optional root that confines every read-tool path.

    The normal read tool remains unrestricted for backwards compatibility.
    Security-sensitive child sessions can opt into confinement by setting
    ``GPTME_READ_ROOT`` to an absolute directory.  Invalid configuration fails
    closed rather than silently widening access.
    """
    value = os.environ.get(_READ_ROOT_ENV)
    if not value:
        return None, None
    configured = Path(value).expanduser()
    if not configured.is_absolute():
        return None, f"{_READ_ROOT_ENV} must be an absolute path: {value!r}"
    try:
        root = configured.resolve(strict=True)
    except OSError as exc:
        return None, f"Invalid {_READ_ROOT_ENV} {value!r}: {exc}"
    if not root.is_dir():
        return None, f"{_READ_ROOT_ENV} is not a directory: {root}"
    return root, None


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


def _read_text_bounded(path: Path) -> str | None:
    """Return UTF-8 text up to the read limit, or ``None`` if it exceeds it."""
    with path.open("rb") as f:
        data = f.read(_MAX_READ_BYTES + 1)
    if len(data) > _MAX_READ_BYTES:
        return None
    return data.decode("utf-8")


def _read_line_range(
    path: Path, start_idx: int, end_line: int | None
) -> tuple[list[str], int, bool, bool, bool]:
    """Stream lines ``start_idx:end_line`` with bounded input and output.

    Line boundaries match ``str.splitlines()`` on the full content. Reading
    stops as soon as ``end_line`` or the output byte cap is reached, so neither
    a huge physical line nor the unselected remainder is buffered. The byte cap
    includes rendered line-number prefixes and separators. Invalid UTF-8 bytes
    inside selected lines are shown as U+FFFD and reported.
    Returns (selected, total, truncated, total_exact, has_invalid_utf8).
    """
    selected: list[str] = []
    selected_content_bytes = 0
    total = 0
    has_invalid_utf8 = False
    pending: list[str] = []
    pending_bytes = 0
    line_has_content = False
    skip_lf = False
    separators = "\n\v\f\r\x1c\x1d\x1e\x85\u2028\u2029"

    def rendered_size(content_bytes: int, count: int, last_line_no: int) -> int:
        """Bytes used by numbered lines, including tabs and joining newlines."""
        if count == 0:
            return 0
        width = len(str(last_line_no))
        return content_bytes + count * (width + 1) + (count - 1)

    def consume() -> tuple[bool, bool]:
        """Consume one logical line; return (stop, truncated)."""
        nonlocal selected_content_bytes, total, has_invalid_utf8, pending_bytes
        nonlocal line_has_content
        if total >= start_idx:
            count = len(selected) + 1
            if (
                rendered_size(selected_content_bytes + pending_bytes, count, total + 1)
                > _MAX_READ_BYTES
            ):
                return True, True
            line = "".join(pending)
            if any("\udc80" <= char <= "\udcff" for char in line):
                has_invalid_utf8 = True
                line = "".join(
                    "�" if "\udc80" <= char <= "\udcff" else char for char in line
                )
            selected.append(line)
            selected_content_bytes += pending_bytes
        total += 1
        pending.clear()
        pending_bytes = 0
        line_has_content = False
        return end_line is not None and total >= end_line, False

    def process(text: str) -> tuple[bool, bool]:
        """Process decoded text; return (stop, truncated)."""
        nonlocal pending_bytes, line_has_content, skip_lf
        for char in text:
            if skip_lf:
                skip_lf = False
                if char == "\n":
                    continue
            if char in separators:
                stop, truncated = consume()
                if stop:
                    return stop, truncated
                skip_lf = char == "\r"
                continue
            line_has_content = True
            if total >= start_idx:
                char_bytes = (
                    3 if "\udc80" <= char <= "\udcff" else len(char.encode("utf-8"))
                )
                count = len(selected) + 1
                if (
                    rendered_size(
                        selected_content_bytes + pending_bytes + char_bytes,
                        count,
                        total + 1,
                    )
                    > _MAX_READ_BYTES
                ):
                    return True, True
                pending.append(char)
                pending_bytes += char_bytes
        return False, False

    decoder = codecs.getincrementaldecoder("utf-8")(errors="surrogateescape")
    with path.open("rb") as f:
        while chunk := f.read(_READ_CHUNK_BYTES):
            stop, truncated = process(decoder.decode(chunk))
            if stop:
                return selected, total, truncated, False, has_invalid_utf8
        stop, truncated = process(decoder.decode(b"", final=True))
        if stop:
            return selected, total, truncated, False, has_invalid_utf8

    if line_has_content:
        stop, truncated = consume()
        if stop:
            return selected, total, truncated, False, has_invalid_utf8
    return selected, total, False, True, has_invalid_utf8


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
    # Path traversal protection: relative paths stay within cwd. A caller may
    # additionally confine *all* reads (including absolute paths and symlink
    # targets) by setting GPTME_READ_ROOT for the session. In that mode, resolve
    # relative paths from the configured root so the process can run from a
    # separate, trusted directory without loading project configuration from the
    # untrusted readable tree.
    path_display = path
    read_root, read_root_error = _configured_read_root()
    if read_root_error is not None:
        yield Message("system", f"Read denied: {read_root_error}")
        return
    expanded_path = path.expanduser()
    if read_root is not None and not expanded_path.is_absolute():
        path = (read_root / expanded_path).resolve()
    else:
        path = expanded_path.resolve()
    containment_root = read_root or (
        Path.cwd().resolve() if not path_display.is_absolute() else None
    )
    if containment_root is not None:
        try:
            path.relative_to(containment_root)
        except ValueError:
            label = (
                "configured read root" if read_root is not None else "current directory"
            )
            yield Message(
                "system",
                f"Path traversal detected: {path_display} resolves to {path} "
                f"which is outside {label} {containment_root}",
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

    start_idx = max(0, start_line - 1)
    content: str | None = None
    range_truncated = False
    total_exact = True
    has_invalid_utf8 = False
    try:
        content = _read_text_bounded(path)
        if content is None:
            if start_line == 1 and end_line is None:
                size = path.stat().st_size
                yield Message(
                    "system",
                    f"File too large to read whole: {path} ({size} bytes, limit "
                    f"{_MAX_READ_BYTES}). Pass start_line/end_line to read a range.",
                )
                return
            selected, total_lines, range_truncated, total_exact, has_invalid_utf8 = (
                _read_line_range(path, start_idx, end_line)
            )
            if range_truncated and not selected:
                yield Message(
                    "system",
                    f"The first line in the requested range exceeds the "
                    f"{_MAX_READ_BYTES} byte read limit including line numbers "
                    f"(the file may be minified or binary). Use a text editor "
                    f"or binary tool to inspect it.",
                )
                return
        else:
            lines = content.splitlines()
            total_lines = len(lines)
    except UnicodeDecodeError:
        yield Message("system", f"Cannot read binary file: {path}")
        return
    except PermissionError:
        yield Message("system", f"Permission denied: {path}")
        return

    # Apply line range
    end_idx = min(total_lines, end_line) if end_line is not None else total_lines
    if content is not None:
        selected = lines[start_idx:end_idx]
    elif range_truncated:
        end_idx = start_idx + len(selected)
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
        range_info = (
            f" (lines {shown} of {total_lines})" if total_exact else f" (lines {shown})"
        )

    # Only store a snapshot and show [path#tag] when hashline_edit is active.
    # notify_file_read returns the tag when hashline_edit is loaded, else None,
    # so read.py never imports _hashline_snapshot directly.
    from . import notify_file_read

    # A streamed range of a large file is not the whole content, so it must not
    # become a hashline snapshot. Such a snapshot could not safely support
    # hashline_edit, whose stale-write check compares against the full file.
    tag = notify_file_read(str(path), content) if content is not None else None

    if tag is not None:
        body = md_codeblock(f"{path}{range_info}", f"[{path}#{tag}]\n" + numbered)
    else:
        body = md_codeblock(f"{path}{range_info}", numbered)
    if pruned_message_prefix:
        body = pruned_message_prefix + "\n\n" + body
    if range_truncated:
        body += (
            f"\n\nRange truncated at {_MAX_READ_BYTES} bytes; "
            f"continue with start_line={end_idx + 1}."
        )
    if has_invalid_utf8:
        body += (
            "\n\nWarning: the selected range contains invalid UTF-8 bytes "
            "(shown as \N{REPLACEMENT CHARACTER}). "
            "The displayed content does not exactly match the file."
        )

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

    # Built-in read skips execute_with_confirmation() (it is read_only). Invoke
    # the guardrail hook directly — not the full TOOL_CONFIRM chain. Falling
    # through to server_confirm/cli_confirm would prompt (and in server mode
    # wait up to an hour) in shadow/off, which those modes promise never to do.
    # Consult the registry first: a direct call would ignore HOOK_ALLOWLIST
    # exclusion and disable_hook, making reads disagree with shell.
    from ..hooks.confirm import ConfirmAction
    from ..hooks.guardrails import (
        _is_secret_path,
        guardrail_hook,
        is_guardrail_active,
    )

    if is_guardrail_active() and any(_is_secret_path(str(p)) for p in paths):
        tool_use = get_current_tool_use() or ToolUse(
            tool="read",
            args=[str(paths[0])] if len(paths) == 1 else None,
            content="\n".join(str(p) for p in paths) if len(paths) != 1 else "",
        )
        result = guardrail_hook(
            tool_use=tool_use,
            preview="\n".join(str(p) for p in paths),
        )
        if result is not None and result.action == ConfirmAction.SKIP:
            yield Message(
                "system",
                result.message or "Read blocked by guardrail",
            )
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

    # Reject non-positive range bounds instead of silently producing a wrong
    # slice: start_line <= 0 clamps to the first line (hiding the bad input),
    # and end_line <= 0 yields an empty or last-line-truncated codeblock
    # (e.g. end_line=0 -> lines[N:0] == [], end_line=-1 -> lines[N:-1] drops
    # the last line).  Same class of silent mislabel #3494 fixed for inverted
    # ranges; checked after the multi-path reset for the same reason.
    if start_line < 1:
        yield Message(
            "system",
            f"Invalid line range: start_line ({start_line}) must be >= 1.",
        )
        return
    if end_line is not None and end_line < 1:
        yield Message(
            "system",
            f"Invalid line range: end_line ({end_line}) must be >= 1.",
        )
        return

    # Reject an inverted line range instead of silently yielding an empty
    # codeblock mislabeled with the requested range (e.g. lines[7:3] == []).
    # Checked after the multi-path reset so an inverted range on a batch read
    # is still treated as "ignored" (ranges don't apply to multi-path reads).
    if end_line is not None and start_line > end_line:
        yield Message(
            "system",
            f"Invalid line range: start_line ({start_line}) is greater than "
            f"end_line ({end_line}).",
        )
        return

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
    read_only=True,
    disabled_by_default=True,
)
__doc__ = tool.get_doc(__doc__)
