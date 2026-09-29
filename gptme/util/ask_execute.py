"""
Utilities for tool execution and preview display.

Confirmation is now handled by cli_confirm_hook and server_confirm_hook
via the hook system. Tools use `from ..hooks import confirm` for secondary
confirmation questions.
"""

import os
from collections.abc import Callable, Generator
from pathlib import Path

from rich import print
from rich.console import Console
from rich.syntax import Syntax

from ..message import Message
from .clipboard import set_copytext

console = Console(log_path=False)

_FAILED_EDIT_PREFIXES = (
    "append aborted:",
    "atomic patch aborted:",
    "error:",
    "failed ",
    "save aborted:",
    "warning: morph edit resulted in no changes",
)


def _failed_edit_message(message: Message | None) -> str | None:
    """Return the failure text from an edit executor's terminal message."""
    if message is None:
        return None
    text = message.content.strip()
    return text if text.lower().startswith(_FAILED_EDIT_PREFIXES) else None


def print_confirmation_help(copiable: bool, editable: bool, default: bool = True):
    """Print help text for confirmation options.

    This is shared with cli_confirm_hook.
    """
    lines = [
        "Options:",
        " y - execute the code",
        " n - do not execute the code",
    ]
    if copiable:
        lines.append(" c - copy the code to the clipboard")
    if editable:
        lines.append(" e - edit the code before executing")
    lines.extend(
        [
            " auto - stop asking for the rest of the session",
            " auto N - auto-confirm next N operations",
            f"Default is '{'y' if default else 'n'}' if answer is empty.",
        ]
    )
    print("\n".join(lines))


def print_preview(
    code: str, lang: str, copy: bool = False, header: str | None = None
):  # pragma: no cover
    """Print a preview of code with syntax highlighting.

    Args:
        code: The code to preview
        lang: Language for syntax highlighting
        copy: Whether to set up code for clipboard copying
        header: Optional header to display above the preview
    """
    print()
    print(f"[bold white]{header or 'Preview'}[/bold white]")

    if copy:
        set_copytext(code)

    # NOTE: we can set background_color="default" to remove background
    print(Syntax(code.strip("\n"), lang))
    print()


def execute_with_confirmation(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    *,
    # Required parameters
    execute_fn: Callable[[str, Path | None], Generator[Message, None, None]],
    get_path_fn: Callable[
        [str | None, list[str] | None, dict[str, str] | None], Path | None
    ],
    # Optional parameters
    preview_fn: Callable[[str, Path | None], str | None] | None = None,
    preview_header: str | None = None,
    preview_lang: str | None = None,
    confirm_msg: str | None = None,
    allow_edit: bool = True,
    confirmation_workspace: Path | None = None,
) -> Generator[Message, None, None]:
    """Helper function to handle common patterns in tool execution.

    Uses the hook system for confirmation. Tools that need secondary
    confirmations should use `from ..hooks import confirm`.

    Args:
        code: The code/content to execute
        args: List of arguments
        kwargs: Dictionary of keyword arguments
        execute_fn: Function that performs the actual execution
        get_path_fn: Function to get the path from args/kwargs
        preview_fn: Optional function to prepare preview content
        preview_lang: Language for syntax highlighting
        confirm_msg: Custom confirmation message
        allow_edit: Whether to allow editing the content
        confirmation_workspace: Effective cwd passed to confirmation hooks
    """
    from ..hooks import ConfirmAction, get_confirmation
    from ..tools.base import get_current_tool_use
    from .diff_suggestions import (
        confirmation_is_automatic,
        diff_suggestion_line_ranges,
        record_diff_suggestion,
    )

    try:
        # Get the path and content
        path = get_path_fn(code, args, kwargs)
        content = (
            code if code is not None else (kwargs.get("content", "") if kwargs else "")
        )

        # Prepare preview content
        preview_content = None
        if preview_fn and content:
            preview_content = preview_fn(content, path)

        # Get confirmation via hook system.
        result = get_confirmation(
            preview=preview_content or content,
            workspace=confirmation_workspace,
            default_confirm=True,
        )
        confirmation_automatic = confirmation_is_automatic()

        if result.action == ConfirmAction.SKIP:
            record_diff_suggestion(
                get_current_tool_use(),
                result,
                preview_content or content,
                confirmation_automatic=confirmation_automatic,
                execution_status="not_run",
            )
            msg = result.message or "Operation aborted: user chose not to run."
            yield Message("system", msg)
            return

        # Handle edited content from confirmation result.  An edit is a new
        # execution request: route it through the hook chain again so guardrails
        # inspect the exact content that will execute, not only the original.
        was_edited = False
        edited_result = result
        edited_confirmation_automatic = confirmation_automatic
        final_preview = preview_content or content
        if result.action == ConfirmAction.EDIT:
            if not allow_edit:
                record_diff_suggestion(
                    get_current_tool_use(),
                    result,
                    preview_content or content,
                    confirmation_automatic=confirmation_automatic,
                    decision_override="skipped",
                    execution_status="not_run",
                )
                # Editing is not supported for this command type (e.g. bg with surrounding
                # commands). Abort rather than execute unedited content the user tried to modify.
                yield Message(
                    "system",
                    "Editing is not supported for this command; execution aborted.",
                )
                return
            if not result.edited_content:
                record_diff_suggestion(
                    get_current_tool_use(),
                    result,
                    preview_content or content,
                    confirmation_automatic=confirmation_automatic,
                    decision_override="skipped",
                    execution_status="not_run",
                )
                yield Message(
                    "system", "Editing returned no content; execution aborted."
                )
                return

            was_edited = content != result.edited_content
            content = result.edited_content
            if was_edited:
                edited_preview = preview_fn(content, path) if preview_fn else None
                final_preview = edited_preview or content
                edited_result = get_confirmation(
                    preview=edited_preview or content,
                    workspace=confirmation_workspace,
                    default_confirm=True,
                )
                edited_confirmation_automatic = confirmation_is_automatic()
                if edited_result.action != ConfirmAction.CONFIRM:
                    record_diff_suggestion(
                        get_current_tool_use(),
                        edited_result,
                        edited_preview or content,
                        edited_by_user=True,
                        confirmation_automatic=edited_confirmation_automatic,
                        decision_override="skipped",
                        execution_status="not_run",
                    )
                    msg = (
                        edited_result.message
                        or "Edited content was not confirmed; execution aborted."
                    )
                    yield Message("system", msg)
                    return

        tool_use = get_current_tool_use()
        line_ranges = diff_suggestion_line_ranges(tool_use, final_preview)

        # Execute
        try:
            ex_result = execute_fn(content, path)
            last_message: Message | None = None
            if isinstance(ex_result, Generator):
                for message in ex_result:
                    if isinstance(message, Message):
                        last_message = message
                    yield message
            else:
                if isinstance(ex_result, Message):
                    last_message = ex_result
                yield ex_result
        except Exception as e:
            record_diff_suggestion(
                tool_use,
                edited_result if was_edited else result,
                final_preview,
                edited_by_user=was_edited,
                confirmation_automatic=(
                    edited_confirmation_automatic
                    if was_edited
                    else confirmation_automatic
                ),
                execution_status="failed",
                execution_error=str(e),
                line_ranges_override=line_ranges,
            )
            if os.getenv("PYTEST_CURRENT_TEST") and not isinstance(e, ValueError):
                raise
            yield Message("system", f"Error during execution: {e}")
            return

        if execution_error := _failed_edit_message(last_message):
            record_diff_suggestion(
                tool_use,
                edited_result if was_edited else result,
                final_preview,
                edited_by_user=was_edited,
                confirmation_automatic=(
                    edited_confirmation_automatic
                    if was_edited
                    else confirmation_automatic
                ),
                execution_status="failed",
                execution_error=execution_error,
                line_ranges_override=line_ranges,
            )
            return

        record_diff_suggestion(
            tool_use,
            edited_result if was_edited else result,
            final_preview,
            edited_by_user=was_edited,
            confirmation_automatic=(
                edited_confirmation_automatic if was_edited else confirmation_automatic
            ),
            execution_status="applied",
            line_ranges_override=line_ranges,
        )

        # Add edit notification if content was edited
        if was_edited:
            yield Message("system", "(content was edited by user)")

    except Exception as e:
        if os.getenv("PYTEST_CURRENT_TEST") and not isinstance(e, ValueError):
            raise
        yield Message("system", f"Error during execution: {e}")
