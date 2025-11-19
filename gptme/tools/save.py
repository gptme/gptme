"""
Gives the assistant the ability to save whole files, or append to them.
"""

import re
from collections.abc import Generator
from pathlib import Path
from typing import Literal

from ..message import Message
from ..util.ask_execute import execute_with_confirmation
from .base import (
    ConfirmFunc,
    Parameter,
    ToolSpec,
    ToolUse,
    get_path,
)
from .patch import Patch

instructions = """
Create or overwrite a file with the given content.

The path can be relative to the current directory, or absolute.
If the current directory changes, the path will be relative to the new directory.
""".strip()

instructions_format = {
    "markdown": "To write to a file, use a code block with the language tag: `save <path>`",
}

instructions_append = """
Append the given content to a file.`.
""".strip()

instructions_format_append = {
    "markdown": """
Use a code block with the language tag: `append <path>`
to append the code block content to the file at the given path.""".strip(),
}


def examples(tool_format):
    return f"""
> User: write a hello world script to hello.py
> Assistant:
{ToolUse("save", ["hello.py"], 'print("Hello world")').to_output(tool_format)}
> System: Saved to `hello.py`
> User: make it all-caps
> Assistant:
{ToolUse("save", ["hello.py"], 'print("HELLO WORLD")').to_output(tool_format)}
> System: Saved to `hello.py`
""".strip()


def examples_append(tool_format):
    return f"""
> User: append a print "Hello world" to hello.py
> Assistant:
{ToolUse("append", ["hello.py"], 'print("Hello world")').to_output(tool_format)}
> System: Appended to `hello.py`
""".strip()


def preview_save(content: str, path: Path | None) -> str | None:
    """Prepare preview content for save operation."""
    assert path
    if path.exists():
        current = path.read_text()
        p = Patch(current, content)
        diff_str = p.diff_minimal()
        return diff_str if diff_str.strip() else None
    return content


def preview_append(content: str, path: Path | None) -> str | None:
    """Prepare preview content for append operation."""
    assert path
    if path.exists():
        current = path.read_text()
        if not current.endswith("\n"):
            current += "\n"
    else:
        current = ""
    new = current + content
    return preview_save(new, path)


# Regex for detecting placeholder lines like "# ... rest of content" or "// ... content here"
re_placeholder = re.compile(r"^\s*(#|//|\"{3})\s*\.{3}.*$", re.MULTILINE)


def check_for_placeholders(content: str) -> bool:
    """Check if content contains placeholder lines."""
    return bool(re_placeholder.search(content))


def execute_save_impl(
    content: str, path: Path | None, confirm: ConfirmFunc
) -> Generator[Message, None, None]:
    """Actual save implementation."""
    from ..hooks import HookType, trigger_hook

    assert path
    path_display = path

    # Print full path to give agent feedback about where exactly the file is saved
    path = path.expanduser().resolve()

    # Trigger pre-save hooks
    if pre_save_msgs := trigger_hook(
        HookType.FILE_PRE_SAVE,
        log=None,
        workspace=None,
        path=path,
        content=content,
    ):
        yield from pre_save_msgs

    # Ensure content ends with newline
    if not content.endswith("\n"):
        content += "\n"

    # Check if file exists and store original content for comparison
    overwrite = False
    original_content = None
    if path.exists():
        original_content = path.read_text()
        if not confirm(f"File {path_display} exists, overwrite?"):
            yield Message("system", "Save aborted: user refused to overwrite the file.")
            return
        overwrite = True

    # Check if folder exists
    missing_parent_created = False
    if not path.parent.exists():
        if not confirm(f"Folder {path_display.parent} doesn't exist, create it?"):
            yield Message(
                "system", "Save aborted: user refused to create a missing folder."
            )
            return
        path.parent.mkdir(parents=True)
        missing_parent_created = True

    # Save the file
    with open(path, "w") as f:
        f.write(content)

    # Trigger post-save hooks
    if post_save_msgs := trigger_hook(
        HookType.FILE_POST_SAVE,
        log=None,
        workspace=None,
        path=path,
        content=content,
        created=not overwrite,
    ):
        yield from post_save_msgs

    # Check if this was an inefficient overwrite (minimal changes)
    hint = ""
    if overwrite and original_content:
        try:
            # Calculate how much actually changed
            p = Patch(original_content, content)
            diff = p.diff_minimal(strip_context=True)

            # Count changed lines vs total lines
            changed_lines = len(
                [line for line in diff.split("\n") if line.startswith(("+", "-"))]
            )
            total_lines = len(content.split("\n"))

            # Emit hint if changes are minimal relative to file size
            # Show hint if: (< 30% changed AND > 10 lines) OR (< 5 lines changed AND > 10 lines)
            if total_lines > 10 and (
                changed_lines < total_lines * 0.3 or changed_lines < 5
            ):
                hint = "\n💡 Hint: This save barely changed the file. Consider using the patch tool for small modifications to be more efficient."
        except Exception:
            # If diff calculation fails, don't emit hint
            pass

    yield Message(
        "system",
        f"Saved to {path_display}"
        + (" (overwritten)" if overwrite else "")
        + (" (created missing folder)" if missing_parent_created else "")
        + hint,
    )


def execute_append_impl(
    content: str, path: Path | None, confirm: ConfirmFunc
) -> Generator[Message, None, None]:
    """Actual append implementation."""
    assert path
    path_display = path
    path = path.expanduser()

    # Check if folder exists first
    if not path.parent.exists():
        if not confirm(f"Folder {path_display.parent} doesn't exist, create it?"):
            yield Message(
                "system", "Append aborted: user refused to create a missing folder."
            )
            return
        path.parent.mkdir(parents=True)

    # Then check if file exists
    if not path.exists():
        if not confirm(f"File {path_display} doesn't exist, create it?"):
            yield Message(
                "system",
                "Append aborted: user refused to create the missing destination file.",
            )
            return
        path.touch()

    # Ensure content ends with newline
    if not content.endswith("\n"):
        content += "\n"

    # Add newline before content if existing file doesn't end with one
    before = path.read_text()
    if before and not before.endswith("\n"):
        content = "\n" + content

    with open(path, "a") as f:
        f.write(content)
    yield Message("system", f"Appended to {path_display}")


def _validate_and_execute(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
    operation: Literal["save", "append"],
) -> Generator[Message, None, None]:
    """Common validation and execution logic for save and append operations."""
    # Get content from kwargs if available (tool format), otherwise use code (markdown format)
    content = kwargs.get("content") if kwargs else None
    if not content:
        content = code

    if not content:
        yield Message("system", "No content provided")
        return

    # Use content instead of code for the rest of the function
    code = content

    if check_for_placeholders(code):
        action = "Save" if operation == "save" else "Append"
        yield Message(
            "system",
            f"{action} aborted: Content contains placeholder lines (e.g. '# ...' or '// ...'). "
            "Please provide the complete content"
            + (
                ""
                if operation == "append"
                else " or use the patch tool for partial changes"
            )
            + ".",
        )
        return

    path = get_path(code, args, kwargs)
    if not path:
        yield Message("system", "No path provided")
        return

    preview_lang = "diff" if path.exists() else None
    confirm_msg = f"Save to {path}?" if operation == "save" else f"Append to {path}?"
    execute_fn = execute_save_impl if operation == "save" else execute_append_impl
    preview_fn = preview_save if operation == "save" else preview_append

    yield from execute_with_confirmation(
        code,
        args,
        kwargs,
        confirm,
        execute_fn=execute_fn,
        get_path_fn=get_path,
        preview_fn=preview_fn,
        preview_lang=preview_lang,
        confirm_msg=confirm_msg,
        allow_edit=True,
    )


def execute_save(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Save code to a file."""
    yield from _validate_and_execute(code, args, kwargs, confirm, "save")


def execute_append(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Append code to a file."""
    yield from _validate_and_execute(code, args, kwargs, confirm, "append")


tool_save = ToolSpec(
    name="save",
    desc="Write text to file",
    instructions=instructions,
    instructions_format=instructions_format,
    examples=examples,
    execute=execute_save,
    block_types=["save"],
    parameters=[
        Parameter(
            name="path",
            type="string",
            description="The path of the file",
            required=True,
        ),
        Parameter(
            name="content",
            type="string",
            description="The content to save",
            required=True,
        ),
    ],
)
__doc__ = tool_save.get_doc(__doc__)

tool_append = ToolSpec(
    name="append",
    desc="Append text to file",
    instructions=instructions_append,
    instructions_format=instructions_format_append,
    examples=examples_append,
    execute=execute_append,
    block_types=["append"],
    parameters=[
        Parameter(
            name="path",
            type="string",
            description="The path of the file",
            required=True,
        ),
        Parameter(
            name="content",
            type="string",
            description="The content to append",
            required=True,
        ),
    ],
)
__doc__ = tool_append.get_doc(__doc__)
