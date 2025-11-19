"""Context compression utilities.

Provides core compression utilities that can be used via hooks,
shell tool integration, or direct invocation.
"""

import re

from ..util.tokens import len_tokens


def strip_reasoning(content: str, model: str = "gpt-4") -> tuple[str, int]:
    """
    Strip reasoning tags from message content.

    Removes <think>...</think> and <thinking>...</thinking> blocks
    while preserving the rest of the content.

    Args:
        content: Message content potentially containing reasoning tags
        model: Model name for token counting

    Returns:
        Tuple of (stripped_content, tokens_saved)
    """
    original_tokens = len_tokens(content, model)

    # Remove <think>...</think> blocks (including newlines inside)
    stripped = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)

    # Remove <thinking>...</thinking> blocks (including newlines inside)
    stripped = re.sub(r"<thinking>.*?</thinking>", "", stripped, flags=re.DOTALL)

    # Clean up extra whitespace left by removals
    stripped = re.sub(r"\n\n\n+", "\n\n", stripped)  # Multiple blank lines -> two
    stripped = stripped.strip()

    tokens_saved = original_tokens - len_tokens(stripped, model)
    return stripped, tokens_saved
