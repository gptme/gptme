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
The options can be provided as a question on the first line and each option on a separate line.

The tool will present an interactive menu allowing the user to select an option using arrow keys and Enter, or by typing the number of the option.
""".strip()

instructions_format = {
    "markdown": "Use a code block with the language tag: `choice` followed by question and each option on a separate line.",
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

> User: What should we do next?
> Assistant: Let me present you with some options:
{ToolUse("choice", [], '''Example question?
1. Option one
2. Option two''').to_output(tool_format)}
> System: User selected: Option two
""".strip()


def parse_options_from_content(content: str) -> tuple[str | None, list[str]]:
    """Parse options from content, returning (question, options)."""
    lines = [line.strip() for line in content.strip().split("\n") if line.strip()]

    if not lines:
        return None, []

    # If first line ends with '?', treat it as the question
    if lines[0].endswith("?"):
        question = lines[0]
        options = lines[1:]
    else:
        question = None
        options = lines

    return question, options


def parse_options_from_kwargs(kwargs: dict[str, str]) -> tuple[str | None, list[str]]:
    """Parse options from args and kwargs, returning (question, options)."""
    question = kwargs.get("question", None)
    options_str = kwargs.get("options", "")
    options = [opt.strip() for opt in options_str.split("\n") if opt.strip()]
    if not question:
        if options and options[0].endswith("?"):
            question = options[0]
            options = options[1:]
    return (question, options)


def execute_choice(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Present multiple-choice options to the user and return their selection."""
    DEFAULT_QUESTION = "Please select an option:"

    question: str | None = None
    options: list[str] = []

    # Parse options from different input formats
    if code:
        question, options = parse_options_from_content(code)
    elif kwargs:
        question, options = parse_options_from_kwargs(kwargs)

    if not options:
        yield Message("system", "No options provided for selection")
        return

    # Import questionary here to handle import errors gracefully
    try:
        import questionary
    except ImportError:
        from ..util.install import get_package_install_command

        install_cmd = get_package_install_command("questionary")
        yield Message(
            "system",
            f"questionary library not available. Please install it with: {install_cmd}",
        )
        return

    # Strip out 1., 2., 3., etc numbers from options if they are present
    options = [
        opt
        if not opt
        or not opt.strip()
        or not (opt[0].isdigit() and opt.split() and "." in opt.split()[0])
        else " ".join(opt.split()[1:])
        for opt in options
    ]

    # Create the interactive selection
    try:
        # Use questionary to create interactive selection
        selection = questionary.select(
            question or DEFAULT_QUESTION,
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
    disabled_by_default=True,
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
