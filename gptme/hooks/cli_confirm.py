"""CLI confirmation hook for terminal-based tool confirmation.

This hook provides the interactive terminal confirmation experience
that was previously implemented in ask_execute.py.
"""

import logging
import os
import sys
from pathlib import Path

try:
    import termios
except ImportError:
    termios = None  # type: ignore[assignment]

_msvcrt: object = None
if os.name == "nt":
    try:
        import msvcrt as _msvcrt  # type: ignore[assignment]
    except ImportError:
        pass
from typing import TYPE_CHECKING

from rich import print
from rich.console import Console

from ..util.ask_execute import print_confirmation_help, print_preview
from ..util.clipboard import copy
from ..util.prompt import prompt_alert
from ..util.sound import print_bell
from ..util.terminal import terminal_state_title
from ..util.useredit import edit_text_with_editor
from .confirm import (
    ConfirmationResult,
    check_auto_confirm,
)
from .confirm import (
    reset_auto_confirm as _reset_auto_confirm,
)
from .confirm import (
    set_auto_confirm as _set_auto_confirm,
)

if TYPE_CHECKING:
    from ..tools.base import ToolUse

logger = logging.getLogger(__name__)
console = Console(log_path=False)


# Re-export centralized auto-confirm functions for backward compatibility
def reset_auto_confirm():
    """Reset auto-confirm state."""
    _reset_auto_confirm()


def set_auto_confirm(count: int | None = None):
    """Set auto-confirm mode.

    Args:
        count: Number of auto-confirms, or None for infinite
    """
    _set_auto_confirm(count)


def cli_confirm_hook(
    tool_use: "ToolUse",
    preview: str | None = None,
    workspace: Path | None = None,
) -> ConfirmationResult:
    """CLI confirmation hook for terminal-based confirmation.

    This provides the interactive terminal confirmation experience:
    - Shows a preview of the tool execution (always, even in auto-confirm mode)
    - Asks for user confirmation (y/n/e/c)
    - Supports auto-confirm mode
    - Supports editing content before execution
    - Supports copying content to clipboard
    """
    # Get preview content - use provided preview or generate from tool_use
    content = preview or tool_use.content
    lang = _get_lang_for_tool(tool_use.tool, content)

    # Determine if content is editable/copiable
    editable = bool(content)
    copiable = bool(content)

    # Show preview if we have content (always, even in auto-confirm mode)
    # This ensures rich diffs are visible for monitoring what's being executed
    if content:
        print_preview(content, lang, copy=copiable)

    # Check auto-confirm (after showing preview)
    should_auto, message = check_auto_confirm()
    if should_auto:
        if message:
            console.log(message)
        return ConfirmationResult.confirm()

    # Build the confirmation prompt
    print_bell()  # Ring the bell before asking
    if termios:
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    elif _msvcrt:
        while _msvcrt.kbhit():  # type: ignore[attr-defined]
            _msvcrt.getch()  # type: ignore[attr-defined]

    # Build choice string with available options
    choicestr = "[Y/n"
    if copiable:
        choicestr += "/c"
    if editable:
        choicestr += "/e"
    choicestr += "/a/?]"

    question = f"Execute {tool_use.tool}?"

    with terminal_state_title("❓ waiting for confirmation"):
        prompt = f"{question} {choicestr}"
        answer = prompt_alert(prompt)

    # Handle the response
    return _handle_response(answer, content, editable, copiable, tool_use)


def _handle_response(
    answer: str,
    content: str | None,
    editable: bool,
    copiable: bool,
    tool_use: "ToolUse",
) -> ConfirmationResult:
    """Handle user response to confirmation prompt."""

    # Copy option
    if copiable and answer == "c":
        if copy():
            print("Copied to clipboard.")
        return ConfirmationResult.skip("Copied to clipboard, execution skipped")

    # Edit option
    if editable and answer == "e" and content:
        ext = _get_ext_for_tool(tool_use)
        edited = edit_text_with_editor(content, ext=ext)
        if edited != content:
            print("Content updated.")
            return ConfirmationResult.edit(edited)
        return ConfirmationResult.skip("No changes made, execution skipped")

    # Auto-confirm options
    import re

    re_auto = r"^a(?:uto)?(?:\s+(\d+))?$"
    match = re.match(re_auto, answer)
    if match:
        if num := match.group(1):
            _set_auto_confirm(int(num))
        else:
            _set_auto_confirm(None)  # Infinite auto-confirm
        return ConfirmationResult.confirm()

    # Help option
    if answer in ["help", "h", "?"]:
        print_confirmation_help(copiable, editable, default=True)
        # Re-prompt (recursive call via hook system would be complex, so we skip for now)
        return ConfirmationResult.skip("Help shown, please re-run")

    # Yes/No options
    if answer in ["y", "yes", ""]:
        return ConfirmationResult.confirm()

    if answer in ["n", "no"]:
        return ConfirmationResult.skip("Declined by user")

    # Unknown - treat as no
    return ConfirmationResult.skip(f"Unknown response: {answer}")


def _looks_like_diff(content: str | None) -> bool:
    """Check if content looks like unified diff output.

    Save/append previews currently come from ``Patch.diff_minimal()``, which strips
    unified-diff headers (``---``, ``+++``, ``@@``). So we rely on the body shape:
    mostly diff-like lines plus either mixed +/- changes or context lines.
    """
    if not content:
        return False

    lines = content.splitlines()
    if not lines:
        return False

    # Most lines should look like diff body lines.
    diff_lines = sum(
        1 for line in lines if line.startswith((" ", "+", "-")) or line == ""
    )
    if diff_lines < len(lines) * 0.8:
        return False

    has_plus = any(line.startswith("+") for line in lines)
    has_minus = any(line.startswith("-") for line in lines)
    has_context = any(line.startswith(" ") for line in lines)

    # Must contain actual change markers.
    if not (has_plus or has_minus):
        return False

    # Avoid false positives on plain markdown/yaml lists (mostly '-' lines).
    # Real diff previews usually have either mixed +/- lines or context lines.
    if (has_plus and has_minus) or has_context:
        return True

    # Accept plus-only diffs (e.g. append to empty file produces all-'+' output
    # from Patch.diff_minimal). Require every non-empty line to start with '+'
    # and at least one non-"+ " line to avoid matching markdown/yaml '+' lists.
    if has_plus and not has_minus:
        non_empty = [line for line in lines if line.strip()]
        all_plus = non_empty and all(line.startswith("+") for line in non_empty)
        # Markdown '+ item' lists always have a space after '+'.  Real diffs from
        # diff_minimal() use '+content' (no space) for most lines.
        has_tight_plus = any(
            line.startswith("+") and (len(line) == 1 or line[1] != " ")
            for line in non_empty
        )
        if all_plus and has_tight_plus:
            return True

    return False


def _get_lang_for_tool(tool: str, content: str | None = None) -> str:
    """Get the syntax highlighting language for a tool.

    For save/append, detects if the preview content is a diff and
    highlights accordingly.
    """
    if tool in ("save", "append") and _looks_like_diff(content):
        return "diff"
    lang_map = {
        "python": "python",
        "ipython": "python",
        "shell": "bash",
        "save": "text",
        "append": "text",
        "patch": "diff",
    }
    return lang_map.get(tool, "text")


def _get_ext_for_tool(tool_use: "ToolUse") -> str | None:
    """Get file extension for editing based on tool."""
    if tool_use.tool in ("save", "append", "patch") and tool_use.args:
        path = Path(tool_use.args[0])
        return path.suffix.lstrip(".") or None
    if tool_use.tool in ("python", "ipython"):
        return "py"
    if tool_use.tool == "shell":
        return "sh"
    return None


def register():
    """Register the CLI confirmation hook."""
    from . import HookType, register_hook

    register_hook(
        name="cli_confirm",
        hook_type=HookType.TOOL_CONFIRM,
        func=cli_confirm_hook,
        priority=0,
        enabled=True,
    )
    logger.debug("Registered CLI confirmation hook")
