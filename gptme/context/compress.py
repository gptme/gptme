"""Context compression utilities.

Provides core compression utilities that can be used via hooks,
shell tool integration, or direct invocation.

This module implements two complementary compression analysis approaches:

1. **Independent Compression** (analyze_log_compression):
   Compresses each message independently to measure inherent compressibility.
   - High ratio (>0.7): Unique content, high information density
   - Low ratio (<0.3): Repetitive content, low information density

   Use cases:
   - Identify highly repetitive tool outputs
   - Detect messages with low information value
   - Classify message types by compression patterns

2. **Incremental Compression** (analyze_incremental_compression):
   Measures marginal information contribution of each message to the
   conversation context. For message at position n:

   ```python
   size_before = compress(messages[0:n-1])
   size_after = compress(messages[0:n])
   marginal_contribution = size_after - size_before
   ratio = marginal_contribution / len(messages[n])
   ```

   - Low ratio (<0.3): Message is redundant with existing context
   - High ratio (>0.7): Message adds novel information

   Use cases:
   - Identify where to draw compression boundaries for auto-compact
   - Detect when tool outputs duplicate previous context
   - Prioritize high-novelty messages in context windows
   - Track information density trajectory over conversation

Example:
    >>> from gptme.context.compress import analyze_incremental_compression
    >>> from gptme.logmanager import Log
    >>>
    >>> log = Log.read_jsonl("conversation.jsonl")
    >>> trajectory = analyze_incremental_compression(log.messages)
    >>>
    >>> # Find low-novelty messages
    >>> redundant = [
    ...     (i, msg, stats)
    ...     for i, (msg, stats) in enumerate(trajectory)
    ...     if stats.ratio < 0.3
    ... ]
    >>>
    >>> # These messages are good candidates for aggressive summarization
    >>> for i, msg, stats in redundant[:5]:
    ...     print(f"Message {i}: {stats}")
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


def analyze_incremental_compression(
    messages: list[Message], level: int = 6, min_size: int = 50
) -> list[tuple[Message, CompressionStats]]:
    """
    Analyze marginal information contribution of each message to the conversation.

    This measures how much new information each message adds by comparing
    compression with and without the message in context.

    For each message at position n:
    - size_before = compress(messages[0:n-1])
    - size_after = compress(messages[0:n])
    - marginal_contribution = size_after - size_before
    - ratio = marginal_contribution / len(messages[n])

    Low ratio (<0.3): Message is redundant with existing context (low novelty)
    High ratio (>0.7): Message adds unique information (high novelty)

    Args:
        messages: List of messages in conversation
        level: Compression level
        min_size: Minimum message size in bytes to analyze (default: 50).
                  Short messages naturally don't compress well and can skew results.

    Returns:
        List of (message, stats) tuples showing incremental contribution.
        The stats show the marginal compression contribution, not independent compression.
        Short messages (< min_size) are excluded from analysis.

    Use cases:
    - Identify where to draw compression boundaries for auto-compact
    - Detect when tool outputs duplicate previous context
    - Prioritize high-novelty messages in context windows
    - Track information density trajectory over conversation

    Note:
        Short messages are filtered because they naturally have less compression
        opportunity, which can inflate novelty ratios and skew statistics.
    """
    trajectory: list[tuple[Message, CompressionStats]] = []

    if not messages:
        return trajectory

    # Filter messages by size - build mapping to maintain positions
    sized_messages = [
        (i, msg) for i, msg in enumerate(messages) if len(msg.content) >= min_size
    ]

    if not sized_messages:
        return trajectory

    # First message has no context, so use independent compression
    first_idx, first_msg = sized_messages[0]
    first_stats = analyze_message_compression(first_msg, level=level)
    trajectory.append((first_msg, first_stats))

    # For subsequent messages, measure marginal contribution
    # Use original message list for context, but only analyze sized messages
    for idx, msg in sized_messages[1:]:
        # Compress context before this message (using all messages up to idx)
        context_before = "\n\n".join(m.content for m in messages[:idx])
        size_before = len(zlib.compress(context_before.encode("utf-8"), level=level))

        # Compress context including this message
        context_after = "\n\n".join(m.content for m in messages[: idx + 1])
        size_after = len(zlib.compress(context_after.encode("utf-8"), level=level))

        # Calculate marginal contribution
        marginal_contribution = size_after - size_before
        msg_size = len(msg.content.encode("utf-8"))

        # Calculate ratio (marginal / original)
        ratio = marginal_contribution / msg_size if msg_size > 0 else 0.0
        savings = (1 - ratio) * 100

        stats = CompressionStats(
            original_size=msg_size,
            compressed_size=marginal_contribution,
            ratio=ratio,
            savings_pct=savings,
        )

        trajectory.append((msg, stats))

    return trajectory


_FENCE_OPEN_RE = re.compile(r"[ \t]*(`{3,}(?![^\n]*`)|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"[ \t]*(`{3,}|~{3,})[ \t]*")
_REASONING_OPEN_RE = re.compile(r"<(think|thinking)>")


def _line_end(content: str, pos: int) -> int:
    end = content.find("\n", pos)
    return len(content) if end == -1 else end + 1


def _fence_end(content: str, pos: int, fence: str) -> int | None:
    """Return the end of the fenced block whose body starts at ``pos``.

    A closer uses the opener's character, at least as many of them, and
    nothing but whitespace (including a CRLF ending). Returns ``None`` for an
    unclosed fence.
    """
    while pos < len(content):
        end = _line_end(content, pos)
        m = _FENCE_CLOSE_RE.fullmatch(content[pos:end].rstrip("\r\n"))
        if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
            return end
        pos = end
    return None


def _reasoning_segments(
    content: str, drop_unclosed: bool = False
) -> list[tuple[str, str]]:
    """Split content into ``("text" | "code" | "reasoning", text)`` segments.

    Fences are only recognised outside reasoning, so a fence opened inside
    ``<think>`` and never closed cannot hide the ``</think>`` after it.
    Reasoning tags inside a fence outside reasoning are code, not reasoning.
    Inline backticks do not mask tags: keep the existing stripping behaviour
    outside fences rather than guessing whether a backtick belongs to reasoning.
    """
    segments: list[tuple[str, str]] = []
    pos = 0
    line_start = True

    while pos < len(content):
        if line_start:
            fence = _FENCE_OPEN_RE.match(content, pos)
            if fence:
                body = _line_end(content, pos)
                # An unclosed fence runs to the end of the content.
                end = _fence_end(content, body, fence.group(1)) or len(content)
                segments.append(("code", content[pos:end]))
                pos = end
                continue
        line_end = _line_end(content, pos)
        opener = _REASONING_OPEN_RE.search(content, pos, line_end)
        if not opener:
            segments.append(("text", content[pos:line_end]))
            pos, line_start = line_end, True
            continue
        segments.append(("text", content[pos : opener.start()]))
        close = f"</{opener.group(1)}>"
        close_at = content.find(close, opener.end())
        if close_at == -1:
            if drop_unclosed:
                segments.append(("reasoning", content[opener.start() :]))
                break
            segments.append(("text", opener.group(0)))
            pos, line_start = opener.end(), False
            continue
        end = close_at + len(close)
        segments.append(("reasoning", content[opener.start() : end]))
        pos = end
        line_start = content[pos - 1] == "\n"
    return segments


def remove_reasoning(
    content: str, drop_unclosed: bool = False, preserve_edges: bool = False
) -> str:
    """Remove reasoning blocks, leaving fenced code byte-for-byte intact.

    Blank-line runs are collapsed and edges stripped outside fences only.
    With ``drop_unclosed``, an opener without a closer drops the rest of the
    content (an interrupted reasoning block). ``preserve_edges`` retains the
    original outside whitespace for replay without adding it to code twice.
    """
    return _render_reasoning_segments(
        _reasoning_segments(content, drop_unclosed), preserve_edges
    )


def _render_reasoning_segments(
    segments: list[tuple[str, str]], preserve_edges: bool = False
) -> str:
    """Render the original scan once; removing tags must not create new fences."""
    leading = trailing = ""
    if preserve_edges:
        edge: list[str] = []
        for kind, text in segments:
            if kind != "text":
                break
            edge.append(text)
        text = "".join(edge)
        leading = text[: len(text) - len(text.lstrip())]
        edge = []
        for kind, text in reversed(segments):
            if kind != "text":
                break
            edge.append(text)
        text = "".join(reversed(edge))
        trailing = text[len(text.rstrip()) :]
        if all(kind == "text" and not text.strip() for kind, text in segments):
            trailing = ""  # The same whitespace is already in leading.
    chunks: list[tuple[bool, list[str]]] = []  # (is_code, pieces)
    for kind, text in segments:
        if kind == "reasoning" or not text:
            continue
        is_code = kind == "code"
        if chunks and not is_code and not chunks[-1][0]:
            chunks[-1][1].append(text)
        else:
            chunks.append((is_code, [text]))
    joined = [(is_code, "".join(pieces)) for is_code, pieces in chunks]
    for i, (is_code, text) in enumerate(joined):
        if is_code:
            continue
        if i == 0:
            text = text.lstrip()
        if i == len(joined) - 1:
            text = text.rstrip()
        joined[i] = (
            False,
            (leading if i == 0 else "")
            + re.sub(r"\n\n\n+", "\n\n", text)
            + (trailing if i == len(joined) - 1 else ""),
        )
    return "".join(text for _, text in joined)


def strip_reasoning(content: str, model: str = "gpt-4") -> tuple[str, int]:
    """
    Strip reasoning tags from message content.

    Removes <think>...</think> and <thinking>...</thinking> blocks
    while preserving the rest of the content. Fenced code outside reasoning
    is left intact, including tag-like text and blank lines inside it.

    Args:
        content: Message content potentially containing reasoning tags
        model: Model name for token counting

    Returns:
        Tuple of (stripped_content, tokens_saved)
    """
    original_tokens = len_tokens(content, model)
    stripped = remove_reasoning(content)
    tokens_saved = original_tokens - len_tokens(stripped, model)
    return stripped, tokens_saved
