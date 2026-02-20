"""
Tools to reduce a log to a smaller size.

Typically used when the log exceeds a token limit and needs to be shortened.
"""

import logging
import re
from collections.abc import Generator

from ..codeblock import Codeblock
from ..llm.models import get_default_model, get_model
from ..message import Message, len_tokens

logger = logging.getLogger(__name__)


def reduce_log(
    log: list[Message],
    limit=None,
    prev_len=None,
) -> Generator[Message, None, None]:
    """Reduces log until it is below `limit` tokens by continually summarizing the longest messages until below the limit."""
    # get the token limit
    model = get_default_model() or get_model("gpt-4")
    if limit is None:
        # Use more conservative multiplier for Anthropic models due to tokenizer inaccuracy
        # tiktoken's cl100k_base fallback can undercount tokens for Claude models,
        # so we trigger reduction earlier to avoid hitting the API limit
        multiplier = 0.75 if model.provider == "anthropic" else 0.9
        limit = multiplier * model.context

    # if we are below the limit, return the log as-is
    tokens = len_tokens(log, model=model.model)
    if tokens <= limit:
        yield from log
        return

    logger.info(f"Log exceeded limit of {limit}, was {tokens}, reducing")
    # filter out pinned messages
    i, longest_msg = max(
        [(i, m) for i, m in enumerate(log) if not m.pinned],
        key=lambda t: len_tokens(t[1].content, model.model),
    )

    # attempt to truncate the longest message
    truncated = truncate_msg(longest_msg)

    # if unchanged after truncate, attempt summarize
    if truncated:
        summary_msg = truncated
    else:
        summary_msg = longest_msg

    log = log[:i] + [summary_msg] + log[i + 1 :]

    tokens = len_tokens(log, model.model)
    if tokens <= limit:
        yield from log
    else:
        # recurse until we are below the limit
        # but if prev_len == tokens, we are not making progress, so just return the log as-is
        if prev_len == tokens:
            logger.warning("Not making progress, returning log as-is")
            yield from log
        else:
            yield from reduce_log(log, limit, prev_len=tokens)


def truncate_msg(msg: Message, lines_pre=10, lines_post=10) -> Message | None:
    """Truncates message codeblocks and <details> blocks to the first and last `lines_pre` and `lines_post` lines, keeping the rest as `[...]`."""
    content_staged = msg.content

    # Truncate long codeblocks
    for codeblock in msg.get_codeblocks():
        # check that the reformatted codeblock is in the content
        full_block = codeblock.to_markdown()
        assert full_block in content_staged, f"{full_block} not in {content_staged}"

        # truncate the middle part of the codeblock, keeping the first and last n lines
        lines = codeblock.content.split("\n")
        if len(lines) > lines_pre + lines_post + 1:
            content = "\n".join([*lines[:lines_pre], "[...]", *lines[-lines_post:]])
        else:
            logger.warning("Not enough lines in codeblock to truncate")
            continue

        # replace the codeblock with the truncated version
        content_staged_prev = content_staged
        content_staged = content_staged.replace(
            full_block, Codeblock(codeblock.lang, content).to_markdown()
        )
        assert content_staged != content_staged_prev
        assert full_block not in content_staged

    # Truncate long <details> blocks (common in GitHub issue comments)
    content_staged = _truncate_details_blocks(
        content_staged, lines_pre=lines_pre, lines_post=lines_post
    )

    if content_staged != msg.content:
        return msg.replace(content=content_staged)
    else:
        return None


_DETAILS_OPEN_RE = re.compile(r"<details[^>]*>", re.IGNORECASE)
_DETAILS_CLOSE_RE = re.compile(r"</details>", re.IGNORECASE)
_SUMMARY_RE = re.compile(r"<summary>.*?</summary>", re.DOTALL | re.IGNORECASE)


def _find_details_blocks(content: str) -> list[tuple[int, int]]:
    """Find top-level <details> block spans using nesting-aware matching."""
    blocks: list[tuple[int, int]] = []
    depth = 0
    start = 0

    # Merge open/close tags into a sorted event list
    events: list[tuple[int, str, int]] = []  # (pos, type, end_pos)
    for m in _DETAILS_OPEN_RE.finditer(content):
        events.append((m.start(), "open", m.end()))
    for m in _DETAILS_CLOSE_RE.finditer(content):
        events.append((m.start(), "close", m.end()))
    events.sort(key=lambda e: e[0])

    for pos, kind, end_pos in events:
        if kind == "open":
            if depth == 0:
                start = pos
            depth += 1
        elif kind == "close":
            depth -= 1
            if depth == 0:
                blocks.append((start, end_pos))
            elif depth < 0:
                depth = 0  # malformed HTML, reset

    return blocks


def _truncate_details_blocks(
    content: str, lines_pre: int = 10, lines_post: int = 10
) -> str:
    """Truncate long <details> blocks, preserving <summary> and first/last lines.

    Handles nested <details> by only truncating top-level blocks.
    """
    blocks = _find_details_blocks(content)
    if not blocks:
        return content

    # Process blocks in reverse order so positions remain valid
    for block_start, block_end in reversed(blocks):
        block_text = content[block_start:block_end]

        # Extract header: opening tag + optional summary
        open_match = _DETAILS_OPEN_RE.match(block_text)
        if not open_match:
            continue
        header_end = open_match.end()

        # Check for summary immediately after the opening tag
        remaining = block_text[header_end:]
        summary_match = _SUMMARY_RE.match(remaining.lstrip())
        if summary_match:
            # Include whitespace between <details> and <summary>
            ws_len = len(remaining) - len(remaining.lstrip())
            header_end += ws_len + summary_match.end()

        header = block_text[:header_end]

        # Find closing tag position within block
        close_match = _DETAILS_CLOSE_RE.search(
            block_text, len(block_text) - len("</details>") - 5
        )
        if not close_match:
            continue
        footer = block_text[close_match.start() :]

        # Extract body between header and footer
        body = block_text[header_end : close_match.start()]
        lines = body.split("\n")

        # Strip leading/trailing blank lines for accurate counting
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()

        if len(lines) > lines_pre + lines_post + 1:
            truncated_body = "\n".join(
                [*lines[:lines_pre], "[...]", *lines[-lines_post:]]
            )
            replacement = f"{header}\n{truncated_body}\n{footer}"
            content = content[:block_start] + replacement + content[block_end:]

    return content


def limit_log(log: list[Message]) -> list[Message]:
    """
    Picks messages until the total number of tokens exceeds limit,
    then removes the last message to get below the limit.
    Will always pick the first few system messages.
    """
    model = get_default_model()
    assert model, "No model loaded"

    # Always pick the first system messages
    initial_system_msgs = []
    for msg in log:
        if msg.role != "system":
            break
        initial_system_msgs.append(msg)

    # Pick the messages in latest-first order
    msgs = []
    for msg in reversed(log[len(initial_system_msgs) :]):
        msgs.append(msg)
        if len_tokens(msgs, model.model) > model.context:
            break

    # Remove the message that put us over the limit
    if len_tokens(msgs, model.model) > model.context:
        # skip the last message
        msgs.pop()

    return initial_system_msgs + list(reversed(msgs))
