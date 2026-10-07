"""Context-overflow recovery using the existing rule-based compactor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import _get_keep_head
from .context_provider import CompressionConfig, get_context_provider

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from ...logmanager import LogManager
    from ...message import Message, MessageMetadata


def compact_for_overflow(manager: LogManager) -> list[Message]:
    """Build a compacted view for an over-window provider request.

    Overflow bypasses the normal savings decision: the original request already
    failed, so any smaller request is preferable to terminating the session.
    The caller persists the result as a view, preserving the master log.
    """
    config = CompressionConfig(
        logdir=manager.logdir,
        keep_head=_get_keep_head(),
    )
    from .engine import _has_reasoning_block

    # reduce_log's final fallback can truncate details inside thinking. Protect
    # those blocks during trim, then restore flags so whole-step dropping works.
    # Track originals by position rather than content-tuple to avoid collisions
    # when two reasoning messages share the same timestamp/content/role/call_id
    # (e.g. legacy messages whose timestamp is a shared file-mtime).
    keep_head = config.keep_head
    orig_pinned = [
        msg.pinned
        for i, msg in enumerate(manager.log.messages)
        if i >= keep_head and _has_reasoning_block(msg.content)
    ]
    trim_input = [
        message.replace(pinned=True)
        if _has_reasoning_block(message.content)
        else message
        for message in manager.log.messages
    ]
    compressed = get_context_provider("default").compress(trim_input, config).messages
    # Reasoning messages are pinned so the compressor preserves them in order;
    # restore each one's original pinned value by its ordinal position.
    reasoning_counter = 0
    result = []
    for index, message in enumerate(compressed):
        if index >= keep_head and _has_reasoning_block(message.content):
            if reasoning_counter < len(orig_pinned):
                result.append(message.replace(pinned=orig_pinned[reasoning_counter]))
                reasoning_counter += 1
            else:
                result.append(message)
        else:
            result.append(message)
    return result


def _carry_checkpoint_request(messages: list[Message], view_name: str) -> list[Message]:
    """Re-point the live checkpoint request at the recovery view.

    Overflow recovery builds a new view from the log, but the checkpoint
    request it carries is stamped with the view it was created on. Without
    this the request goes unrecognized on the recovery view: the hook would
    either leave the internal checkpoint transcript in place or start a
    second checkpoint instead of installing the response already received.
    Failure latches move with the request so rejected requests stay rejected.
    """
    last_request_idx = max(
        (
            i
            for i, msg in enumerate(messages)
            if "compaction_checkpoint_view" in (msg.metadata or {})
        ),
        default=None,
    )
    carried: list[Message] = []
    for index, msg in enumerate(messages):
        metadata = msg.metadata or {}
        if index == last_request_idx or "compaction_checkpoint_failed_view" in metadata:
            updated: MessageMetadata = {**metadata}
            if "compaction_checkpoint_view" in metadata:
                updated["compaction_checkpoint_view"] = view_name
            if "compaction_checkpoint_failed_view" in metadata:
                updated["compaction_checkpoint_failed_view"] = view_name
            msg = msg.replace(metadata=updated)
        carried.append(msg)
    return carried


def _drop_oldest_turn(messages: list[Message]) -> list[Message]:
    """Drop one old assistant/user step with all its following tool results.

    The protected head, pinned steps, newest user request, and final step stay
    verbatim. Grouping consecutive system results with their non-system anchor
    works for markdown, XML, and structured tool calls without parsing content.
    """
    starts = [i for i, message in enumerate(messages) if message.role != "system"]
    keep_head = _get_keep_head()
    last_user = max((i for i in starts if messages[i].role == "user"), default=-1)
    for index, start in enumerate(starts[:-1]):
        end = starts[index + 1]
        if start < keep_head or start == last_user:
            continue
        if any(message.pinned for message in messages[start:end]):
            continue
        return messages[:start] + messages[end:]
    return messages


def _drop_to_retry_target(messages: list[Message], model: str) -> list[Message]:
    """Remove whole old steps toward the same hysteresis target as budget trims."""
    from ...util.context_measurement import measure_context_tokens
    from .decision import TRIM_TARGET_RATIO

    target = measure_context_tokens(messages, model) * TRIM_TARGET_RATIO
    while measure_context_tokens(messages, model) > target:
        smaller = _drop_oldest_turn(messages)
        if len(smaller) == len(messages):
            break
        messages = smaller
    return messages


def recover_reply(
    manager: LogManager | None,
    messages: list[Message],
    model: str,
    generate: Callable[[list[Message]], Message],
    prepare: Callable[[list[Message]], list[Message]],
    *,
    retry_guard: Callable[[bool], AbstractContextManager[None]] | None = None,
) -> Message:
    """Retry atomic provider overflows using strictly smaller lossless views.

    Try the existing trim first, then drop old whole steps toward 0.7 of the
    previous request. At most eight smaller requests are attempted. Exhaustion,
    non-context errors, and partially emitted output restore the original view.
    A caller guard serializes view changes and distinguishes retry admission
    (False) from restoration (True), so revoked server epochs cannot clobber
    a replacement step's view.
    """
    from contextlib import nullcontext
    from time import monotonic

    from ...llm import did_llm_reply_emit_visible_output, is_context_length_error
    from ...message import len_tokens
    from ...util.context_measurement import measure_context_tokens
    from .events import append_compaction_event

    def guard(restoring: bool = False) -> AbstractContextManager[None]:
        return retry_guard(restoring) if retry_guard is not None else nullcontext()

    try:
        return generate(messages)
    except Exception as caught:
        if (
            manager is None
            or not is_context_length_error(caught)
            or did_llm_reply_emit_visible_output(caught)
        ):
            raise
        error = caught

    original_view = manager.current_view
    keep_view = False
    try:
        for attempt in range(8):
            started = monotonic()
            before_messages = manager.log.messages
            before_tokens = measure_context_tokens(before_messages, model)
            provider_before = len_tokens(messages, model)
            with guard():
                pass
            candidate = (
                compact_for_overflow(manager)
                if attempt == 0
                else _drop_to_retry_target(before_messages, model)
            )
            if attempt > 0 and len_tokens(candidate, model) >= len_tokens(
                before_messages, model
            ):
                raise error
            success = False
            provider_after = None
            method = "trim" if attempt == 0 else "drop_oldest"
            try:
                while True:
                    with guard():
                        view_name = manager.get_next_view_name()
                        candidate = _carry_checkpoint_request(candidate, view_name)
                        manager.create_view(view_name, candidate)
                        manager.switch_view(view_name)
                    retry_messages = prepare(manager.log.messages)
                    provider_after = len_tokens(retry_messages, model)
                    if provider_after < provider_before:
                        break
                    # Trimming a message already filtered from the request may
                    # save stored tokens without shrinking provider input.
                    smaller = _drop_to_retry_target(candidate, model)
                    if len(smaller) == len(candidate):
                        raise error
                    candidate = smaller
                    method = "drop_oldest"
                with guard():
                    pass
                try:
                    response = generate(retry_messages)
                except Exception as caught:
                    if not is_context_length_error(
                        caught
                    ) or did_llm_reply_emit_visible_output(caught):
                        raise
                    error = caught
                    messages = retry_messages
                    continue
                success = keep_view = True
                return response
            finally:
                append_compaction_event(
                    manager.logdir,
                    trigger="overflow",
                    method=method,
                    tokens_before=before_tokens,
                    tokens_after=len_tokens(candidate, model),
                    messages_before=len(before_messages),
                    messages_after=len(candidate),
                    elapsed_seconds=monotonic() - started,
                    retry_success=success,
                    provider_tokens_before=provider_before,
                    provider_tokens_after=provider_after,
                )
        raise error
    finally:
        if not keep_view:
            with guard(restoring=True):
                if original_view is None:
                    manager.switch_to_master()
                else:
                    manager.switch_view(original_view)
