"""
Gives the LLM agent the ability to edit files using Morph Fast Apply v2.

Morph is a specialized code-patching LLM that applies edits at 4000+ tokens per second.
It uses a different format than the patch tool: <code>original</code><update>changes</update>

Environment Variables:
    OPENROUTER_API_KEY: Required for accessing Morph via OpenRouter
"""

import difflib
from collections.abc import Generator
from pathlib import Path

from ..llm import list_available_providers
from ..llm import reply as llm_reply
from ..message import Message
from ..util.ask_execute import execute_with_confirmation
from .base import (
    ConfirmFunc,
    Parameter,
    ToolSpec,
    ToolUse,
    get_path,
)

# Prompt from Morph Fast Apply v2 documentation
# https://docs.morphllm.com/api-reference/endpoint/apply
instructions = """
Use this tool to propose an edit to an existing file.

This will be read by a less intelligent model, which will quickly apply the edit. You should make it clear what the edit is, while also minimizing the unchanged code you write.

When writing the edit, you should specify each edit in sequence, with the special comment // ... existing code ... to represent unchanged code in between edited lines.

For example:

```morph example.py
// ... existing code ...
FIRST_EDIT
// ... existing code ...
SECOND_EDIT
// ... existing code ...
THIRD_EDIT
// ... existing code ...
```

This will be read by a less intelligent model, which will quickly apply the edit. You should make it clear what the edit is, while also minimizing the unchanged code you write.
When writing the edit, you should specify each edit in sequence, with the special comment // ... existing code ... to represent unchanged code in between edited lines.

You should bias towards repeating as few lines of the original file as possible to convey the change.
NEVER show unmodified code in the edit, unless sufficient context of unchanged lines around the code you're editing is needed to resolve ambiguity.
If you plan on deleting a section, you must provide surrounding context to indicate the deletion.
DO NOT omit spans of pre-existing code without using the // ... existing code ... comment to indicate its absence.
"""

instructions_format = {
    "markdown": f"""
The $PATH parameter MUST be on the same line as the code block start, not on the line after.

{ToolUse("morph", ["$PATH"], "$EDIT_INSTRUCTIONS").to_output("markdown")}
""",
    "tool": "The `edit` parameter must contain your edit instructions as a string.",
}

examples = """
```morph example.py
// ... existing code ...
FIRST_EDIT
// ... existing code ...
SECOND_EDIT
// ... existing code ...
THIRD_EDIT
// ... existing code ...
```
"""


def is_openrouter_available() -> bool:
    """Check if OpenRouter is available for Morph tool."""
    available_providers = list_available_providers()
    return any(provider == "openrouter" for provider, _ in available_providers)


def preview_morph(content: str, path: Path | None) -> str | None:
    """Prepare preview content for morph operation."""
    if not path or not path.exists():
        return "File does not exist"

    try:
        # Read original content
        with open(path) as f:
            original_content = f.read()

        # Generate a diff between original and edited content
        diff_lines = list(
            difflib.unified_diff(
                original_content.splitlines(),
                content.splitlines(),
                fromfile=str(path),
                tofile=str(path),
                lineterm="",
            )
        )

        if not diff_lines:
            return "No changes would be made"

        return "\n".join(diff_lines)

    except Exception as e:
        return f"Preview failed: {str(e)}"


def execute_morph_impl(
    content: str, path: Path | None, confirm: ConfirmFunc
) -> Generator[Message, None, None]:
    """Actual morph implementation - writes the edited content to file."""
    if not path:
        raise ValueError("No file path provided")

    try:
        # Write the edited content back to file
        with open(path, "w") as f:
            f.write(content)

        yield Message("system", f"File successfully edited using Morph: {path}")

    except Exception as e:
        raise ValueError(f"Failed to write file: {str(e)}") from e


def execute_morph(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc = lambda _: True,
) -> Generator[Message, None, None]:
    """Applies the morph edit."""
    if code is None and kwargs is not None:
        code = kwargs.get("edit", code)

    if not code:
        yield Message("system", "Error: No edit instructions provided")
        return

    # Get the file path
    file_path = get_path(code, args, kwargs)
    if not file_path:
        yield Message("system", "Error: No file path provided")
        return

    try:
        # Read the original file
        with open(file_path) as f:
            original_content = f.read()
    except FileNotFoundError:
        yield Message("system", f"Error: File not found: {file_path}")
        return

    # Format the prompt for Morph
    morph_prompt = f"<code>{original_content}</code><update>{code}</update>"

    # Create message for Morph
    messages = [Message("user", morph_prompt)]

    # Call Morph via OpenRouter
    try:
        # Use the openrouter/morph/morph-v2 model
        response = llm_reply(
            messages, "openrouter/morph/morph-v2", stream=False, tools=None
        )
        edited_content = response.content.strip()

        # Use execute_with_confirmation with the edited content
        yield from execute_with_confirmation(
            edited_content,
            args,
            kwargs,
            confirm,
            execute_fn=execute_morph_impl,
            get_path_fn=lambda *_: file_path,
            preview_fn=preview_morph,
            preview_lang="diff",
            confirm_msg=f"Apply Morph edit to {file_path}?",
            allow_edit=False,  # Don't allow editing the computed result
        )

    except Exception as e:
        yield Message("system", f"Morph API call failed: {str(e)}")


tool = ToolSpec(
    name="morph",
    desc="Edit files using Morph Fast Apply v2 - an AI specialized for fast, precise code edits",
    instructions=instructions,
    examples=examples,
    execute=execute_morph,
    block_types=["morph"],
    available=is_openrouter_available,
    parameters=[
        Parameter(
            name="path",
            type="string",
            description="The path of the file to edit.",
            required=True,
        ),
        Parameter(
            name="edit",
            type="string",
            description="Updated content, using '// ... existing code ...' markers.",
            required=True,
        ),
    ],
)
__doc__ = tool.get_doc(__doc__)
