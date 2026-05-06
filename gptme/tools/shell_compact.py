"""
Compact shell wrapper for known high-output command shapes.

Currently supports:
- ``git log --oneline`` — first 20 commits + total count
- ``gh <noun> list`` / ``gh <noun> status`` — first 20 items + count + grep hint

Unsupported commands fall back to the regular shell tool.
"""

from __future__ import annotations

import logging
import shlex
from typing import TYPE_CHECKING

from ..message import Message
from ..util.context import md_codeblock
from ..util.context_savings import record_context_savings
from ..util.output_storage import save_large_output
from ..util.tokens import len_tokens
from .base import Parameter, ToolSpec, ToolUse
from .shell import (
    _format_block_smart,
    _format_shell_output,
    _get_timeout,
    _terminate_interrupted_shell,
    execute_shell,
    get_path_fn,
    get_shell,
    get_shell_command,
    strip_ansi_codes,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_PREVIEW_LINES = 20
_COMPACTOR_SOURCE = "shell_compact"

instructions = """
Use this tool for known high-output shell commands that have a compact preview.

Supported commands:
- `git log --oneline` — shows first 20 commits, total count, and saved path
- `gh issue list`, `gh pr list`, `gh pr status`, `gh run list` etc. — shows
  first 20 items with total count and a grep hint

Unsupported commands fall back to the regular shell tool.
If you need the full raw output, use the `shell` tool instead.
""".strip()

instructions_format: dict[str, str] = {}


def examples(tool_format):
    git_preview = """abc1234 fix: wire context savings to current conversation logdir
def5678 feat: add shell_compact git log preview
... (23 more commits omitted) ..."""
    gh_list_preview = """2334	Settings modal squished on mobile	bug		2026-05-05
2333	Add GPTME_DISABLE_PATH_INCLUDE env var		2026-05-05
... (4 more items omitted) ..."""
    return f"""
> User: show recent commits compactly
> Assistant:
{ToolUse("shell_compact", [], "git log --oneline").to_output(tool_format)}
> System:
> Ran allowlisted compact command: `git log --oneline`
>
> Showing first 20 of 43 commits. Full output saved to /tmp/.../tool-outputs/shell/...
> Use `shell` for a raw rerun or `git show <sha>` for a specific commit.
>
> {md_codeblock("stdout", git_preview)}

> User: show open issues
> Assistant:
{ToolUse("shell_compact", [], "gh issue list --repo gptme/gptme").to_output(tool_format)}
> System:
> Ran allowlisted compact command: `gh issue list --repo gptme/gptme`
>
> Showing first 20 of 24 items. Full output saved to /tmp/.../tool-outputs/shell/...
> Use `shell` for a raw rerun or pipe to `grep` to narrow results.
>
> {md_codeblock("stdout", gh_list_preview)}
""".strip()


def _compact_command_display(cmd: str) -> str:
    if len(cmd) > 100 or cmd.count("\n") >= 1:
        first_line = cmd.split("\n")[0][:80]
        line_count = cmd.count("\n") + 1
        return (
            f"{first_line}... ({line_count} {'line' if line_count == 1 else 'lines'})"
        )
    return cmd


def _default_model_name() -> str:
    from ..llm.models import get_default_model  # fmt: skip

    model = get_default_model()
    return model.model if model else "cl100k_base"


def _matches_git_log_oneline(cmd: str) -> bool:
    if any(ch in cmd for ch in "\n|;&<>`$"):
        return False

    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False

    if len(tokens) < 3 or tokens[:2] != ["git", "log"]:
        return False

    return any(token == "--oneline" for token in tokens[2:])


def _format_git_log_preview(cmd: str, stdout: str, logdir: Path | None) -> str | None:
    lines = [line for line in strip_ansi_codes(stdout).splitlines() if line.strip()]
    if len(lines) <= _PREVIEW_LINES:
        return None

    saved_path: Path | None = None
    if logdir:
        _, saved_path = save_large_output(
            content=stdout,
            logdir=logdir,
            output_type="shell",
            command_info=cmd,
        )

    omitted = len(lines) - _PREVIEW_LINES
    preview = "\n".join(
        lines[:_PREVIEW_LINES] + [f"... ({omitted} more commits omitted) ..."]
    )

    detail = f"Showing first {_PREVIEW_LINES} of {len(lines)} commits."
    if saved_path:
        detail += f" Full output saved to {saved_path}."
    else:
        detail += " Full output was not saved because no conversation logdir is active."
    detail += " Use `shell` for a raw rerun or `git show <sha>` for a specific commit."

    body = detail + "\n\n" + md_codeblock("stdout", preview)

    if logdir and saved_path:
        model_name = _default_model_name()
        try:
            record_context_savings(
                logdir=logdir,
                source=_COMPACTOR_SOURCE,
                original_tokens=len_tokens(stdout, model_name),
                kept_tokens=len_tokens(body, model_name),
                command_info=f"git_log_oneline: {cmd}",
                saved_path=saved_path,
            )
        except OSError as e:
            logger.warning("Failed to record compact shell telemetry: %s", e)

    return body


_GH_LIST_PATTERNS: list[list[str]] = [
    ["issue", "list"],
    ["pr", "list"],
    ["pr", "status"],
    ["run", "list"],
    ["release", "list"],
    ["repo", "list"],
    ["repo", "list", "--json", "nameWithOwner"],
]


def _matches_gh_list(cmd: str) -> bool:
    """Check if a command is a `gh <noun> list|status` pattern."""
    if any(ch in cmd for ch in "\n|;&<>`$"):
        return False

    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False

    if len(tokens) < 3 or tokens[0] != "gh":
        return False

    # Match: gh <noun> list|status (with optional flags/args after)
    for pattern in _GH_LIST_PATTERNS:
        match_len = len(pattern)
        if len(tokens) >= match_len and tokens[1 : 1 + match_len] == pattern:
            # Ensure we don't match when gh is being used with subcommands
            # like `gh issue view` which isn't a list
            return True

    return False


def _compact_body_via_save(
    cmd: str,
    stdout: str,
    logdir: Path | None,
    compactor_name: str,
    line_label: str,  # e.g. "commits", "items", "issues"
    detail_suffix: str,  # e.g. 'Use `shell` for a raw rerun.'
) -> str | None:
    """Build a compact preview body, save full output, and record telemetry.

    Returns None if the output is short enough that no compacting is needed.
    """
    lines = [line for line in strip_ansi_codes(stdout).splitlines() if line.strip()]
    if len(lines) <= _PREVIEW_LINES:
        return None

    saved_path: Path | None = None
    if logdir:
        _, saved_path = save_large_output(
            content=stdout,
            logdir=logdir,
            output_type="shell",
            command_info=cmd,
        )

    omitted = len(lines) - _PREVIEW_LINES
    preview = "\n".join(
        lines[:_PREVIEW_LINES] + [f"... ({omitted} more {line_label} omitted) ..."]
    )

    detail = f"Showing first {_PREVIEW_LINES} of {len(lines)} {line_label}."
    if saved_path:
        detail += f" Full output saved to {saved_path}."
    else:
        detail += " Full output was not saved because no conversation logdir is active."
    detail += " " + detail_suffix

    body = detail + "\n\n" + md_codeblock("stdout", preview)

    if logdir and saved_path:
        model_name = _default_model_name()
        try:
            record_context_savings(
                logdir=logdir,
                source=_COMPACTOR_SOURCE,
                original_tokens=len_tokens(stdout, model_name),
                kept_tokens=len_tokens(body, model_name),
                command_info=f"{compactor_name}: {cmd}",
                saved_path=saved_path,
            )
        except OSError as e:
            logger.warning("Failed to record compact shell telemetry: %s", e)

    return body


def _format_gh_list_preview(cmd: str, stdout: str, logdir: Path | None) -> str | None:
    """Compact gh list output: first 20 items + count + grep hint."""
    return _compact_body_via_save(
        cmd=cmd,
        stdout=stdout,
        logdir=logdir,
        compactor_name="gh_list",
        line_label="items",
        detail_suffix="Use `shell` for a raw rerun or pipe to `grep` to narrow results.",
    )


def _dispatch_compactor(cmd: str, stdout: str, logdir: Path | None) -> str | None:
    """Route to the right compactor based on command shape.

    Returns the compact body, or None if no compactor matched or output was
    already short enough.
    """
    if _matches_git_log_oneline(cmd):
        return _format_git_log_preview(cmd, stdout, logdir)
    if _matches_gh_list(cmd):
        return _format_gh_list_preview(cmd, stdout, logdir)
    return None  # no matching compactor


def shell_compact_allowlist_hook(
    tool_use: ToolUse,
    preview: str | None = None,
    workspace: Path | None = None,
):
    """Auto-confirm compact shell commands with an explicitly supported shape."""
    del preview, workspace

    from ..hooks.confirm import ConfirmationResult

    if tool_use.tool != "shell_compact":
        return None

    cmd = tool_use.content.strip() if tool_use.content else ""
    if not cmd:
        return None

    if _matches_git_log_oneline(cmd) or _matches_gh_list(cmd):
        return ConfirmationResult.confirm()

    return None


def _execute_compacted(
    cmd: str, logdir: Path | None, timeout: float | None
) -> Generator[Message, None, None]:
    """Execute a command and return compacted preview if the shape is supported.

    Falls back to raw shell output if:
    - The command fails
    - No compactor matches the command shape
    - The compactor returns None (output already short enough)
    - The compactor raises OSError
    """
    shell = get_shell()

    try:
        returncode, stdout, stderr = shell.run(cmd, timeout=timeout)
        interrupted = False
        timed_out = returncode == -124
    except KeyboardInterrupt as e:
        stdout = stderr = ""
        if e.args and isinstance(e.args[0], tuple) and len(e.args[0]) == 2:
            stdout, stderr = e.args[0]

        _terminate_interrupted_shell(shell, "Shell compact command")

        returncode = shell.process.returncode
        interrupted = True
        timed_out = False
    except Exception as e:
        raise ValueError(f"Shell error: {e}") from None

    compact_body = None
    if returncode == 0 and not stderr and not interrupted and not timed_out:
        try:
            compact_body = _dispatch_compactor(cmd, stdout, logdir)
        except OSError as e:
            logger.warning("Failed to format compact shell output: %s", e)

    if compact_body is None:
        yield Message(
            "system",
            _format_shell_output(
                cmd,
                stdout,
                stderr,
                returncode,
                interrupted,
                allowlisted=True,
                timed_out=timed_out,
                timeout_value=timeout,
                logdir=logdir,
            ),
        )
    else:
        cmd_display = _compact_command_display(cmd)
        msg = (
            _format_block_smart(
                "Ran allowlisted compact command", cmd_display, lang="bash"
            )
            + "\n\n"
            + compact_body
            + "\n"
        )
        yield Message("system", msg)

    if interrupted:
        raise KeyboardInterrupt from None


def execute_shell_compact(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Execute a compact shell command when the command shape is supported."""
    cmd = get_shell_command(code, args, kwargs)

    if not _matches_git_log_oneline(cmd) and not _matches_gh_list(cmd):
        yield from execute_shell(code, args, kwargs)
        return

    yield from _execute_compacted(
        cmd,
        logdir=get_path_fn(),
        timeout=_get_timeout(),
    )


tool = ToolSpec(
    name="shell_compact",
    desc="Executes compact previews for known high-output shell commands.",
    instructions=instructions,
    instructions_format=instructions_format,
    examples=examples,
    execute=execute_shell_compact,
    block_types=["shell_compact"],
    parameters=[
        Parameter(
            name="command",
            type="string",
            description="The shell command with arguments to execute.",
            required=True,
        ),
    ],
    hooks={
        "allowlist": ("tool.confirm", shell_compact_allowlist_hook, 10),
    },
)
__doc__ = tool.get_doc(__doc__)
