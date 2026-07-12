"""
Tools to reduce a log to a smaller size.

Typically used when the log exceeds a token limit and needs to be shortened.
"""

import logging
import re
from collections.abc import Generator
from typing import Literal

from ..codeblock import Codeblock
from ..llm.models import get_default_model, get_model
from ..message import Message, len_tokens
from . import console

logger = logging.getLogger(__name__)


def message_contains_tool_use(msg: Message) -> bool:
    """Return True when an assistant message contains a runnable tool call.

    Tool-call messages must stay parseable so later tool results do not become
    orphaned after compaction. We probe both formats explicitly because
    conversations may contain markdown, XML, or structured ``tool`` syntax.
    """
    if msg.role != "assistant":
        return False

    from ..tools.base import ToolUse

    tool_formats: tuple[Literal["tool", "xml"], ...] = ("tool", "xml")
    for tool_format in tool_formats:
        if any(
            ToolUse.iter_from_content(msg.content, tool_format_override=tool_format)
        ):
            return True
    return False


def reduce_log(
    log: list[Message],
    limit=None,
    prev_len=None,
    _initial_tokens: int | None = None,
    _truncations: int = 0,
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

    if prev_len is None:
        # First call — notify the user that reduction is starting
        _initial_tokens = tokens
        console.log(
            f"[context] Log too long ({tokens // 1000}k tokens,"
            f" limit ~{int(limit) // 1000}k), reducing..."
        )

    # Filter out pinned messages and assistant tool-call messages. Tool-call
    # messages must remain parseable so paired tool results keep their anchor.
    non_pinned = [
        (i, m)
        for i, m in enumerate(log)
        if not m.pinned and not message_contains_tool_use(m)
    ]
    if not non_pinned:
        logger.warning(
            "Cannot reduce log: all messages are pinned or protected tool calls"
        )
        if _initial_tokens is not None:
            blocks_str = f", truncated {_truncations} block(s)" if _truncations else ""
            console.log(
                "[context] Could not reduce log further: all remaining messages "
                f"are pinned or protected tool calls ({tokens // 1000}k tokens "
                f"still exceeds limit ~{int(limit) // 1000}k{blocks_str})"
            )
        yield from log
        return

    i, longest_msg = max(
        non_pinned,
        key=lambda t: len_tokens(t[1].content, model.model),
    )

    # attempt to truncate the longest message
    truncated = truncate_msg(longest_msg)

    # if unchanged after truncate, attempt summarize
    if truncated:
        summary_msg = truncated
        _truncations += 1
    else:
        summary_msg = longest_msg

    log = log[:i] + [summary_msg] + log[i + 1 :]

    tokens = len_tokens(log, model.model)
    if tokens <= limit:
        if _initial_tokens is not None:
            saved = _initial_tokens - tokens
            blocks_str = f", truncated {_truncations} block(s)" if _truncations else ""
            console.log(
                f"[context] Reduced log by ~{saved // 1000}k tokens"
                f" ({_initial_tokens // 1000}k → {tokens // 1000}k){blocks_str}"
            )
        yield from log
    else:
        # recurse until we are below the limit
        # but if prev_len == tokens, we are not making progress, so just return the log as-is
        if prev_len == tokens:
            logger.warning("Not making progress, returning log as-is")
            if _initial_tokens is not None:
                blocks_str = (
                    f", truncated {_truncations} block(s)" if _truncations else ""
                )
                console.log(
                    f"[context] Could not reduce log further ({tokens // 1000}k tokens"
                    f" still exceeds limit ~{int(limit) // 1000}k{blocks_str})"
                )
            yield from log
        else:
            yield from reduce_log(
                log,
                limit,
                prev_len=tokens,
                _initial_tokens=_initial_tokens,
                _truncations=_truncations,
            )


def truncate_msg(msg: Message, lines_pre=10, lines_post=10) -> Message | None:
    """Truncates message codeblocks and <details> blocks to the first and last `lines_pre` and `lines_post` lines, keeping the rest as `[...]`."""
    if message_contains_tool_use(msg):
        logger.debug("truncate_msg: preserving tool-call message")
        return None

    content_staged = msg.content

    # Truncate long codeblocks
    for codeblock in msg.get_codeblocks():
        # Reformatted codeblock must be present in content for .replace() to work.
        # Round-trip parsing may produce a slightly different rendering than the
        # original (e.g. mixed fence styles, nested fences). Skip rather than crash
        # the whole reduction pass — other codeblocks may still be truncatable.
        full_block = codeblock.to_markdown()
        if full_block not in content_staged:
            logger.warning(
                "truncate_msg: reformatted codeblock not found in content; "
                "skipping. lang=%s lines=%d",
                codeblock.lang,
                codeblock.content.count("\n") + 1,
            )
            continue

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
            full_block,
            Codeblock(codeblock.lang, content, fence=codeblock.fence).to_markdown(),
        )
        assert content_staged != content_staged_prev
        assert full_block not in content_staged

    # Truncate long <details> blocks (common in GitHub issue comments)
    content_staged = _truncate_details_blocks(
        content_staged, lines_pre=lines_pre, lines_post=lines_post
    )

    if content_staged != msg.content:
        return msg.replace(content=content_staged)
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
    events: list[tuple[int, str, int]] = [
        (m.start(), "open", m.end()) for m in _DETAILS_OPEN_RE.finditer(content)
    ]
    events.extend(
        (m.start(), "close", m.end()) for m in _DETAILS_CLOSE_RE.finditer(content)
    )
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


def proactive_summarize_log(
    log: list[Message],
    threshold: float | None = None,
    recent_keep: int = 6,
) -> list[Message]:
    """
    Proactively summarize older conversation turns when approaching the context limit.

    Triggered when token usage exceeds ``threshold`` fraction of the active model's
    context (e.g. 0.8 = 80 %).  Enable via the ``GPTME_AUTO_SUMMARIZE_THRESHOLD``
    env var, e.g.::

        export GPTME_AUTO_SUMMARIZE_THRESHOLD=0.8

    Or pass ``threshold`` directly for programmatic use.  When ``threshold`` is 0
    or ``GPTME_AUTO_SUMMARIZE_THRESHOLD`` is unset, this function is a no-op.

    What is preserved (never summarized):
    - Initial system messages (the prompt header block at the start of the log).
    - The most recent ``recent_keep`` non-system turns and any system messages
      interleaved with them.
    - Pinned messages in the preserved sections.

    What gets summarized:
    - The "middle" section between the initial system block and the recent tail.
      A single summary ``system`` message replaces it.

    Returns:
        A (possibly shorter) list of messages.  Returns the original log unchanged
        when the threshold is not exceeded or when there is nothing to summarize.
    """
    import os

    if threshold is None:
        env_val = os.environ.get("GPTME_AUTO_SUMMARIZE_THRESHOLD")
        if not env_val:
            return log
        try:
            threshold = float(env_val)
        except ValueError:
            logger.warning(
                "Invalid GPTME_AUTO_SUMMARIZE_THRESHOLD value: %s (expected float 0–1)",
                env_val,
            )
            return log

    if threshold <= 0:
        return log

    model = get_default_model() or get_model("gpt-4")
    limit = threshold * model.context
    tokens = len_tokens(log, model=model.model)

    if tokens <= limit:
        return log

    # Collect initial system messages — the always-kept prompt header.
    initial_system: list[Message] = []
    for msg in log:
        if msg.role != "system":
            break
        initial_system.append(msg)

    rest = log[len(initial_system) :]
    if not rest:
        return log

    # Walk backwards to collect the recent tail: the last `recent_keep`
    # non-system turns plus any system messages adjacent to them.
    recent: list[Message] = []
    non_system_count = 0
    for msg in reversed(rest):
        recent.insert(0, msg)
        if msg.role != "system":
            non_system_count += 1
        if non_system_count >= recent_keep:
            break

    # Middle = everything between the initial system block and the recent tail.
    middle = rest[: len(rest) - len(recent)]
    if not middle:
        return log

    # Guard: push the cut backward until no tool-call/result pair straddles the boundary.
    # An assistant message with a tool call must stay together with its tool result
    # (the immediately-following system message).  A system message at the head of
    # `recent` whose anchor (nearest preceding non-system message) is still in `middle`
    # would become orphaned — so we absorb it into `recent` until the boundary is safe.
    while middle and (
        message_contains_tool_use(middle[-1]) or (recent and recent[0].role == "system")
    ):
        recent.insert(0, middle.pop())
        if not middle:
            break

    if not middle:
        return log

    # Separate pinned messages from the middle block — they must never be compressed.
    # When a pinned assistant message contains a tool use, its immediately following
    # system message is the tool result and must be preserved with it — otherwise the
    # result log contains a tool-use without its matching result, which provider APIs
    # reject.  We therefore walk the middle list and pull the paired result alongside
    # any pinned tool-use message.
    pinned_middle: list[Message] = []
    summarize_middle: list[Message] = []
    i = 0
    while i < len(middle):
        m = middle[i]
        if m.pinned:
            pinned_middle.append(m)
            if message_contains_tool_use(m):
                # Pull ALL consecutive system messages (tool results) so we don't
                # orphan any result from a multi-result tool call.
                while i + 1 < len(middle) and middle[i + 1].role == "system":
                    i += 1
                    pinned_middle.append(middle[i])
        else:
            # A non-pinned tool-use whose immediately following message is a pinned
            # tool result must travel to pinned_middle — the pinned result requires
            # its anchor, and the result IS pinned so it will be preserved.
            if (
                message_contains_tool_use(m)
                and i + 1 < len(middle)
                and middle[i + 1].pinned
                and middle[i + 1].role == "system"
            ):
                pinned_middle.append(m)
            else:
                summarize_middle.append(m)
        i += 1
    if not summarize_middle:
        return log

    # Lazy import avoids circular dependency at module load time.
    from ..llm import summarize as _llm_summarize  # fmt: skip

    logger.info(
        "Proactive summarize triggered: %dk tokens (threshold %d%% of %dk context)"
        " — summarizing %d middle messages (%d pinned preserved)",
        tokens // 1000,
        int(threshold * 100),
        model.context // 1000,
        len(summarize_middle),
        len(pinned_middle),
    )
    console.log(
        f"[context] Approaching context limit ({tokens // 1000}k / {int(limit) // 1000}k),"
        f" summarizing {len(summarize_middle)} older messages..."
    )

    try:
        summary_msg = _llm_summarize(summarize_middle)
    except Exception:
        logger.warning(
            "Proactive summarize failed; falling back to unreduced log", exc_info=True
        )
        return log

    result = initial_system + [summary_msg] + pinned_middle + recent
    new_tokens = len_tokens(result, model=model.model)
    saved = tokens - new_tokens
    console.log(
        f"[context] Proactive summarize complete:"
        f" {tokens // 1000}k → {new_tokens // 1000}k tokens (saved ~{saved // 1000}k)"
    )
    return result


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

    result = initial_system_msgs + list(reversed(msgs))

    # Ensure tool_use/tool_result atomicity: if a non-initial system message's
    # immediate predecessor in the original log was dropped by the context limit,
    # drop the system message too. System messages that follow a dropped message
    # are almost certainly orphaned tool results — keeping them without their
    # tool-use anchor produces an incoherent log.
    result_id_set = {id(m) for m in result}
    log_by_id = {id(m): i for i, m in enumerate(log)}
    initial_id_set = {id(m) for m in initial_system_msgs}

    def _is_orphaned(msg: Message) -> bool:
        if msg.role != "system" or id(msg) in initial_id_set:
            return False
        idx = log_by_id.get(id(msg))
        if idx is None or idx == 0:
            return False
        # Walk backward through the original log to find the nearest
        # non-system anchor message. System messages are tool results;
        # their anchor is the assistant (or user) message that produced
        # them. If that anchor was dropped by the context limit, this
        # result is orphaned.
        for j in range(idx - 1, -1, -1):
            if log[j].role != "system":
                return id(log[j]) not in result_id_set
        # No non-system predecessor found — shouldn't happen for a
        # non-initial system message, but treat as not orphaned.
        return False

    return [m for m in result if not _is_orphaned(m)]
