"""Rich helpers that don't pull in prompt_toolkit (gptme.message imports these)."""

import io
from typing import Any, cast

from rich.console import Console


def rich_to_str(text: str | Any, **kwargs) -> str:
    """Convert rich text to ANSI string.

    Args:
        text: The text to convert, can be any type that rich can print
        **kwargs: Additional arguments passed to Console

    Returns:
        str: The text converted to ANSI escape sequences
    """
    # kwargs.setdefault("color_system", "256")
    # kwargs.setdefault("force_terminal", True)  # Ensure ANSI codes are generated
    console = Console(file=io.StringIO(), **kwargs)
    console.print(text, end="")
    return cast(io.StringIO, console.file).getvalue()
