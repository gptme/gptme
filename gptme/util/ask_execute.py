"""
Utilities for tool execution and preview display.

Note: The ask_execute() function and related globals (copiable, editable)
were removed after migration to the hook-based confirmation system.
Confirmation is now handled by cli_confirm_hook and server_confirm_hook.
"""

from collections.abc import Callable, Generator
from pathlib import Path

from rich import print
from rich.console import Console
from rich.syntax import Syntax

from ..message import Message
from ..tools import ConfirmFunc
from .clipboard import set_copytext

console = Console(log_path=False)


# Editable text state for execute_with_confirmation
_editable_text = None
_editable_ext = None


def set_editable_text(text: str, ext: str | None = None):
    """Set the text that can be edited and optionally its file extension."""
    global _editable_text, _editable_ext
    _editable_text = text
    _editable_ext = ext


def get_editable_text() -> str:
    """Get the current editable text."""
    global _editable_text
    if _editable_text is None:
        raise RuntimeError("No editable text set")
    return _editable_text


def get_editable_ext() -> str | None:
    """Get the file extension for the editable text."""
    global _editable_ext
    return _editable_ext


def clear_editable_text():
    """Clear the editable text and extension."""
    global _editable_text, _editable_ext
    _editable_text = None
    _editable_ext = None


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
    confirm_fn: ConfirmFunc,
    *,
    # Required parameters
    execute_fn: Callable[
        [str, Path | None, ConfirmFunc], Generator[Message, None, None]
    ],
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

    Args:
        code: The code/content to execute
        args: List of arguments
        kwargs: Dictionary of keyword arguments
        confirm_fn: Function to get user confirmation
        execute_fn: Function that performs the actual execution
        get_path_fn: Function to get the path from args/kwargs
        preview_fn: Optional function to prepare preview content
        preview_lang: Language for syntax highlighting
        confirm_msg: Custom confirmation message
        allow_edit: Whether to allow editing the content
    """
    try:
        # Get the path and content
        path = get_path_fn(code, args, kwargs)
        content = (
            code if code is not None else (kwargs.get("content", "") if kwargs else "")
        )
        file_ext = path.suffix.lstrip(".") or "txt" if path else "txt"

        # Show preview if preview function is provided
        if preview_fn and content:
            preview_content = preview_fn(content, path)
            if preview_content:
                print_preview(
                    preview_content,
                    preview_lang or file_ext,
                    copy=True,
                    header=preview_header,
                )

        # Make content editable if allowed
        if allow_edit and content:
            ext = (
                Path(str(path)).suffix.lstrip(".")
                if isinstance(path, str | Path)
                else None
            )
            set_editable_text(content, ext)

        try:
            # Get confirmation
            if not confirm_fn(confirm_msg or f"Execute on {path}?"):
                yield Message(
                    "system", "Operation aborted: user chose not to run the operation."
                )
                return

            # Get potentially edited content
            if allow_edit and content:
                edited_content = get_editable_text()
                was_edited = edited_content != content
                content = edited_content
            else:
                was_edited = False

            # Execute
            result = execute_fn(content, path, confirm_fn)
            if isinstance(result, Generator):
                yield from result
            else:
                yield result

            # Add edit notification if content was edited
            if was_edited:
                yield Message("system", "(content was edited by user)")

        finally:
            if allow_edit:
                clear_editable_text()

    except Exception as e:
        if "pytest" in globals():
            raise
        yield Message("system", f"Error during execution: {e}")
