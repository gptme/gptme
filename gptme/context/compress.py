"""Context compression utilities.

Provides core compression utilities that can be used via hooks,
shell tool integration, or direct invocation.
"""

import logging
import re
import zlib
from dataclasses import dataclass

from ..message import Message
from ..util.tokens import len_tokens

logger = logging.getLogger(__name__)


@dataclass
class CompressionStats:
    """Statistics about compression of text."""

    original_size: int
    compressed_size: int
    ratio: float  # compressed / original (lower is more compressible)
    savings_pct: float  # (1 - ratio) * 100

    def __str__(self) -> str:
        return f"Compression: {self.original_size} → {self.compressed_size} bytes ({self.savings_pct:.1f}% savings, ratio: {self.ratio:.3f})"


def measure_compression(text: str, level: int = 6) -> CompressionStats:
    """
    Measure how compressible a text is using zlib.

    Args:
        text: Text to analyze
        level: Compression level (1-9, default 6 for balance)

    Returns:
        CompressionStats with compression metrics

    High compression ratio (>0.8) suggests unique/random content.
    Low compression ratio (<0.3) suggests highly repetitive content.
    """
    original = text.encode("utf-8")
    compressed = zlib.compress(original, level=level)

    ratio = len(compressed) / len(original) if len(original) > 0 else 0.0
    savings = (1 - ratio) * 100

    return CompressionStats(
        original_size=len(original),
        compressed_size=len(compressed),
        ratio=ratio,
        savings_pct=savings,
    )


def analyze_message_compression(msg: Message, level: int = 6) -> CompressionStats:
    """
    Analyze compression of a single message.

    Args:
        msg: Message to analyze
        level: Compression level

    Returns:
        CompressionStats for the message content
    """
    return measure_compression(msg.content, level=level)


def analyze_log_compression(
    messages: list[Message], level: int = 6
) -> tuple[CompressionStats, list[tuple[Message, CompressionStats]]]:
    """
    Analyze compression of an entire conversation log.

    Args:
        messages: List of messages in conversation
        level: Compression level

    Returns:
        Tuple of:
        - Overall log compression stats
        - List of (message, stats) pairs for individual messages

    This helps identify:
    - Overall conversation redundancy
    - Which messages are highly repetitive
    - Tool results that could be compressed/summarized
    """
    # Analyze entire log as one unit
    full_text = "\n\n".join(msg.content for msg in messages)
    overall_stats = measure_compression(full_text, level=level)

    # Analyze each message individually
    message_stats = [
        (msg, analyze_message_compression(msg, level=level)) for msg in messages
    ]

    return overall_stats, message_stats


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
