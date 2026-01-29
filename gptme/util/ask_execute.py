"""
Utilities for tool execution and preview display.

Confirmation is now handled by cli_confirm_hook and server_confirm_hook
via the hook system. Tools use `from ..hooks import confirm` for secondary
confirmation questions.
"""

from collections.abc import Callable, Generator
from pathlib import Path

from rich import print
from rich.console import Console
from rich.syntax import Syntax

from ..message import Message
from .clipboard import set_copytext

console = Console(log_path=False)


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
    _deprecated_confirm_fn=None,  # Deprecated - confirmation now via hooks
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
    """
    from ..hooks import ConfirmAction, get_confirmation

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

        # Get confirmation via hook system
        # The hook will show preview, allow editing, and return result
        result = get_confirmation(
            preview=preview_content or content,
            default_confirm=True,
        )

        if result.action == ConfirmAction.SKIP:
            msg = result.message or "Operation aborted: user chose not to run."
            yield Message("system", msg)
            return

        # Handle edited content from confirmation result
        was_edited = False
        if result.action == ConfirmAction.EDIT and result.edited_content:
            was_edited = content != result.edited_content
            content = result.edited_content

        # Execute
        try:
            ex_result = execute_fn(content, path)
            if isinstance(ex_result, Generator):
                yield from ex_result
            else:
                yield ex_result
        except Exception as e:
            if "pytest" in globals():
                raise
            yield Message("system", f"Error during execution: {e}")
            return

        # Add edit notification if content was edited
        if was_edited:
            yield Message("system", "(content was edited by user)")

    except Exception as e:
        if "pytest" in globals():
            raise
        yield Message("system", f"Error during execution: {e}")
