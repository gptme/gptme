"""
Gives the assistant the ability to present multiple-choice options to the user for selection.
"""

from collections.abc import Generator

from ..message import Message
from .base import (
    ConfirmFunc,
    Parameter,
    ToolSpec,
    ToolUse,
)

instructions = """
Present multiple-choice options to the user for selection.

The options can be provided as:
1. Each option on a separate line in the code block
2. As a 'question' parameter and 'options' parameter (comma-separated)

The tool will present an interactive menu allowing the user to select an option using arrow keys and Enter, or by typing the number of the option.
""".strip()

instructions_format = {
    "markdown": "Use a code block with the language tag: `choice` followed by each option on a separate line, or use kwargs with 'question' and 'options' parameters.",
}


def examples(tool_format):
    return f"""
### Basic usage with options

> User: What should we do next?
> Assistant: Let me present you with some options:
{ToolUse("choice", [], '''What would you like to do next?
Write documentation
Fix bugs
Add new features
Run tests''').to_output(tool_format)}
> System: User selected: Add new features
""".strip()


def parse_options_from_content(content: str) -> tuple[str, list[str]]:
    """Parse options from content, returning (question, options)."""
    lines = [line.strip() for line in content.strip().split("\n") if line.strip()]

    if not lines:
        return "Please select an option:", []

    # If first line ends with '?', treat it as the question
    if lines[0].endswith("?"):
        question = lines[0]
        options = lines[1:]
    else:
        question = "Please select an option:"
        options = lines

    return question, options


def execute_choice(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Present multiple-choice options to the user and return their selection."""

    question = "Please select an option:"
    options: list[str] = []

    # Parse options from different input formats
    if code:
        question, options = parse_options_from_content(code)
    elif kwargs:
        question = kwargs.get("question", question)
        options_str = kwargs.get("options", "")
        if options_str:
            options = [opt.strip() for opt in options_str.split(",") if opt.strip()]

    if not options:
        yield Message("system", "No options provided for selection")
        return

    # Import questionary here to handle import errors gracefully
    try:
        import questionary
    except ImportError:
        yield Message(
            "system",
            "questionary library not available. Please install it with: pip install questionary",
        )
        return

    # Strip out 1., 2., 3., etc numbers from options if they are present
    options = [
        opt
        if not (opt[0].isdigit() and "." in opt.split()[0])
        else " ".join(opt.split()[1:])
        for opt in options
    ]

    # Create the interactive selection
    try:
        # Use questionary to create interactive selection
        selection = questionary.select(
            question,
            choices=options,
            use_shortcuts=True,  # Allow number shortcuts
        ).ask()

        if selection is None:
            yield Message("system", "Selection cancelled")
            return

        yield Message("system", f"User selected: {selection}")

    except (KeyboardInterrupt, EOFError):
        yield Message("system", "Selection cancelled")
    except Exception as e:
        yield Message("system", f"Error during selection: {e}")


tool_choice = ToolSpec(
    name="choice",
    desc="Present multiple-choice options to the user for selection",
    instructions=instructions,
    instructions_format=instructions_format,
    examples=examples,
    execute=execute_choice,
    block_types=["choice"],
    parameters=[
        Parameter(
            name="options",
            type="string",
            description="The question to ask and a comma-separated list of options to choose from",
            required=True,
        ),
    ],
)

__doc__ = tool_choice.get_doc(__doc__)
