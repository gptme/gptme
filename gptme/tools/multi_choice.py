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
    "markdown": "Use a code block with the language tag: `multi_choice` followed by each option on a separate line, or use kwargs with 'question' and 'options' parameters.",
}


def examples(tool_format):
    return f"""
### Basic usage with options in code block

> User: What should we do next?
> Assistant: Let me present you with some options:
{ToolUse("multi_choice", [], '''What would you like to do next?
Write documentation
Fix bugs
Add new features
Run tests''').to_output(tool_format)}
> System: User selected: Add new features

### Using with structured parameters

> User: Help me choose a programming language
> Assistant: I'll help you choose:
{ToolUse("multi_choice", [], None, {"question": "Which programming language would you like to learn?", "options": "Python,JavaScript,Rust,Go"}).to_output(tool_format)}
> System: User selected: Python

### Quick selection with numbers

> User: I want to select option 2 directly
> Assistant: You can select options using numbers:
{ToolUse("multi_choice", [], '''Choose your preferred editor:
VS Code
Vim
Emacs
Sublime Text''').to_output(tool_format)}
> System: User selected: Vim
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


def execute_multi_choice(
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


tool_multi_choice = ToolSpec(
    name="multi_choice",
    desc="Present multiple-choice options to the user for selection",
    instructions=instructions,
    instructions_format=instructions_format,
    examples=examples,
    execute=execute_multi_choice,
    block_types=["multi_choice"],
    parameters=[
        Parameter(
            name="question",
            type="string",
            description="The question to ask the user",
            required=False,
        ),
        Parameter(
            name="options",
            type="string",
            description="Comma-separated list of options to choose from",
            required=False,
        ),
    ],
)

__doc__ = tool_multi_choice.get_doc(__doc__)
