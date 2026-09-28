"""Prompt arguments shared by the CLI and the TUI.

``gptme "first" - "second"`` chains prompts: each is submitted after the
previous turn finishes.
"""

from ..constants import MULTIPROMPT_SEPARATOR


def group_prompt_args(prompts: list[str] | tuple[str, ...]) -> list[str]:
    """Group prompt arguments on exact standalone separator arguments.

    Only a ``-`` that is its own argument splits: splitting joined text on
    ``"\\n\\n-"`` would also match Markdown list items and truncate turns.
    """
    if len(prompts) == 1:
        return [prompts[0].strip()] if prompts[0].strip() else []

    grouped: list[str] = []
    current: list[str] = []
    for prompt in prompts:
        if prompt == MULTIPROMPT_SEPARATOR:
            grouped.append("\n\n".join(current))
            current = []
        else:
            current.append(prompt)
    grouped.append("\n\n".join(current))
    return [stripped for group in grouped if (stripped := group.strip())]
