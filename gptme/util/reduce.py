"""
Tools to reduce a log to a smaller size.

Typically used when the log exceeds a token limit and needs to be shortened.
"""

import logging
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
        # NOTE: disabled because buggy
        # from . import summarize
        # summary_msg = summarize(longest_msg, preamble=False)
        # logger.info("Summary: %s", summary_msg.content)
        # summary_msg.content = f"This {summary_msg.role} message was summarized due to length:\n{summary_msg.content}"
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
    """Truncates message codeblocks to the first and last `lines_pre` and `lines_post` lines, keeping the rest as `[...]`."""
    # TODO: also truncate long <details> (as can be found in GitHub issue comments)
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

    if content_staged != msg.content:
        return msg.replace(content=content_staged)
    else:
        return None


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
