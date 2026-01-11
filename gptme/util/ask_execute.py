"""
Utilities for asking user confirmation and handling editable/copiable content.
"""

import re
import sys
import termios
from collections.abc import Callable, Generator
from pathlib import Path
from textwrap import wrap

from rich import print
from rich.console import Console
from rich.style import Style
from rich.syntax import Syntax

from ..message import Message
from ..tools import ConfirmFunc
from .clipboard import copy, set_copytext
from .prompt import get_prompt_session
from .sound import print_bell
from .terminal import terminal_state_title
from .useredit import edit_text_with_editor

console = Console(log_path=False)


# Global state
override_auto = False
auto_confirm_count = 0
copiable = False
editable = False

# Editable text state
_editable_text = None
_editable_ext = None


def set_copiable():
    """Mark content as copiable."""
    global copiable
    copiable = True


def clear_copiable():
    """Clear copiable state."""
    global copiable
    copiable = False


def set_editable_text(text: str, ext: str | None = None):
    """Set the text that can be edited and optionally its file extension."""
    global _editable_text, _editable_ext, editable
    _editable_text = text
    _editable_ext = ext
    editable = True


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


def set_edited_text(text: str):
    """Update the editable text after editing."""
    global _editable_text
    _editable_text = text


def clear_editable_text():
    """Clear the editable text and extension."""
    global _editable_text, _editable_ext, editable
    _editable_text = None
    _editable_ext = None
    editable = False


def ask_execute(question="Execute code?", default=True) -> bool:
    """Ask user for confirmation before executing code.

    Args:
        question: The question to ask
        default: The default answer if user just presses enter

    Returns:
        bool: True if user confirms execution, False otherwise
    """
    global override_auto, auto_confirm_count, copiable, editable

    if override_auto or auto_confirm_count > 0:
        if auto_confirm_count > 0:
            auto_confirm_count -= 1
            console.log(f"Auto-confirmed, {auto_confirm_count} left")
        return True

    print_bell()  # Ring the bell just before asking for input
    termios.tcflush(sys.stdin, termios.TCIFLUSH)  # flush stdin

    # Build choice string with available options
    choicestr = f"[{'Y' if default else 'y'}/{'n' if default else 'N'}"
    if copiable:
        choicestr += "/c"
    if editable:
        choicestr += "/e"
    choicestr += "/?"
    choicestr += "]"

    with terminal_state_title("❓ waiting for confirmation"):
        session = get_prompt_session()

        prompt = f"{question} {choicestr}"

        style_rich = "bold yellow on red"
        style_prompt_toolkit = "bold fg:ansiyellow bg:ansired"

        lines = wrap(prompt, width=console.width - 2)

        # print pre-prompt lines
        # we do this because of https://github.com/gptme/gptme/issues/498
        if len(lines) > 1:
            for line in lines[:-1]:
                console.print(" " + line.strip() + " ", style=Style.parse(style_rich))

        answer = (
            session.prompt(
                [
                    (style_prompt_toolkit, " " + lines[-1].strip() + " "),
                    ("", " "),
                ]
            )
            .lower()
            .strip()
        )

    if not override_auto:
        if copiable and answer == "c":
            if copy():
                print("Copied to clipboard.")
                return False
            clear_copiable()
        elif editable and answer == "e":
            edited = edit_text_with_editor(get_editable_text(), ext=get_editable_ext())
            if edited != get_editable_text():
                set_edited_text(edited)
                print("Content updated.")
                return ask_execute("Execute with changes?", default)
            return False

    re_auto = r"auto(?:\s+(\d+))?"
    match = re.match(re_auto, answer)
    if match:
        if num := match.group(1):
            auto_confirm_count = int(num)
            return True
        else:
            return (override_auto := True)

    # secret option to ask for help
    if answer in ["help", "h", "?"]:
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
                f"Default is '{'y' if default else 'n'}' if answer is empty.",
            ]
        )
        helptext = "\n".join(lines)
        print(helptext)
        return ask_execute(question, default)

    return answer in (["y", "yes"] + [""] if default else [])


def print_preview(
    code: str, lang: str, copy: bool = False, header: str | None = None
):  # pragma: no cover
    print()
    print(f"[bold white]{header or 'Preview'}[/bold white]")

    if copy:
        set_copiable()
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
