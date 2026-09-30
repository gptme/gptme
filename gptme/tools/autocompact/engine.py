"""Rule-based compaction engine — the 4-phase compaction algorithm.

Implements strategic removal of content from conversations:
0. Replace stale tool outputs in place with a short stub (relevance-scored)
1. Strip reasoning tags from older messages (age-based)
2. Truncate largest tool results first (oh-my-opencode strategy)
3. Extractive compression for long assistant messages

Phase 0 replaces messages rather than deleting them so tool-call/result pairs
stay provider-valid and later phases can still index into conversation.jsonl.
"""

import logging
from collections.abc import Generator
from pathlib import Path

from ...context import strip_reasoning
from ...llm.models import get_default_model, get_model
from ...message import Message, len_tokens
from ...util.master_context import (
    MessageByteRange,
    build_master_context_index,
    create_master_context_reference,
)
from ...util.output_storage import (
    create_tool_result_summary,
    large_output_path,
    save_large_output,
)
from ...util.reduce import message_contains_tool_use, reduce_log
from .events import append_phase0_shadow_event
from .scoring import (
    PruneDecision,
    compress_content,
    score_tool_output_relevance,
)

logger = logging.getLogger(__name__)

# Score threshold below which a tool output is eligible for pre-pass stubbing.
# Messages that score below this AND exceed the minimum size are replaced in
# place (never deleted — that would break tool-call pairing and master-context
# index mapping used by Phases 2–3).
_PRUNE_SCORE_THRESHOLD = 1.0

# Minimum token size before a tool output is eligible for pre-pass pruning.
# Small results are cheap; only stub results that actually reclaim meaningful
# budget.
_PRUNE_MIN_TOKENS = 200

# Representative recovery path used when estimating (no logdir). The estimate
# must use a stub as long as the persisted form auto_compact_log emits;
# a one-line stub overestimates savings and can select rule-based trim when
# the real recovery text would miss MIN_SAVINGS_RATIO.
_STUB_PATH_PLACEHOLDER = (
    "/home/user/.local/share/gptme/logs/"
    "workspace-name/conversation-XXXXXXXX/"
    "tool-outputs/autocompact/20260101_000000-deadbeef.txt"
)


def _estimate_recovery_path(logdir: Path | None, content: str = "") -> str:
    """Path used in the recovery stub. Does not write a file.

    When ``logdir`` is set, reuse :func:`large_output_path` so the shadow path
    shape (and therefore token accounting) tracks the live pass. Without a
    logdir, use the representative placeholder.
    """
    if logdir is None:
        return _STUB_PATH_PLACEHOLDER
    return str(large_output_path(logdir, content, output_type="autocompact"))


def _is_tool_output(msg: Message, prev: Message | None) -> bool:
    """True when ``msg`` is a tool result, not instructions or knowledge.

    Structured tool results carry ``call_id``. Markdown/XML tool format emits a
    system message immediately after an assistant tool-call. Other ``system``
    messages (agent instructions, knowledge files) must not be pruned.
    """
    if msg.role != "system":
        return False
    if msg.call_id:
        return True
    return prev is not None and message_contains_tool_use(prev)


def _format_stale_output_stub(msg_tokens: int, saved_path: str) -> str:
    """Canonical Phase 0 stub text — shared by the engine and the estimator."""
    return (
        f"[Stale tool output pruned - {msg_tokens} tokens]. "
        f"Full output saved to: {saved_path}\n"
        "You can read or grep this file if needed."
    )


def _stale_output_stub(
    msg: Message,
    msg_tokens: int,
    logdir: Path | None,
    *,
    for_estimate: bool = False,
) -> str:
    """In-place replacement for a pruned tool result.

    Persists the original content under ``logdir/tool-outputs/`` so recovery
    survives ``/compact trim`` rewriting conversation.jsonl. Byte-range
    references into that file are intentionally not used: trim overwrites it,
    and a dangling range would return the stub (or garbage) instead of the
    original result.

    ``for_estimate=True`` uses the recovery-path template without writing a
    file. When ``logdir`` is set the path matches :func:`save_large_output`;
    otherwise a representative placeholder is used so savings are not
    overstated relative to production ``auto_compact_log``.
    """
    if for_estimate:
        return _format_stale_output_stub(
            msg_tokens, _estimate_recovery_path(logdir, msg.content)
        )
    if logdir is not None:
        _, saved_path = save_large_output(
            content=msg.content,
            logdir=logdir,
            output_type="autocompact",
            original_tokens=msg_tokens,
        )
        return _format_stale_output_stub(msg_tokens, str(saved_path))
    return f"[Stale tool output pruned - {msg_tokens} tokens]"


def prune_stale_tool_outputs(
    log: list[Message],
    model_name: str,
    keep_head: int = 0,
    logdir: Path | None = None,
    *,
    for_estimate: bool = False,
) -> tuple[list[Message], int]:
    """Phase 0 pre-pass: stub stale tool outputs that are unlikely to be needed.

    Replaces tool-output messages in place (same list length, same indices)
    when they:
    1. Are actual tool results (``call_id`` or immediately after a tool-call).
    2. Are not pinned and not in the protected head.
    3. Score below ``_PRUNE_SCORE_THRESHOLD`` from ``score_tool_output_relevance``.
    4. Are at least ``_PRUNE_MIN_TOKENS`` tokens (small results are kept for free).

    The function never stubs:
    - Non-tool ``system`` messages (instructions, knowledge).
    - Pinned messages.
    - Messages within the protected ``keep_head`` prefix.
    - Messages within the last ``_PRUNE_MIN_AGE`` positions (very recent).
    - Tool results that contain error/failure content.
    - Tool results whose paths are still referenced by later messages.

    In-place replacement (not deletion) keeps tool-call/result pairs
    provider-valid and preserves 1:1 index mapping into conversation.jsonl
    for Phase 2/3 master-context references. Original content is saved under
    ``logdir/tool-outputs/`` when ``logdir`` is set, so recovery does not
    depend on conversation.jsonl surviving a later rewrite.

    When ``for_estimate`` is set, the stub uses the recovery-path template
    without writing a file (logdir-shaped path when ``logdir`` is set,
    placeholder otherwise) so savings are not overstated. Direct engine
    calls without ``logdir`` emit a short stub that does not advertise a path.

    Returns:
        (pruned_log, tokens_saved) — same length as ``log``, and tokens saved.
    """
    pruned: list[Message] = []
    tokens_saved = 0

    for idx, msg in enumerate(log):
        if msg.pinned or idx < keep_head:
            pruned.append(msg)
            continue

        prev = log[idx - 1] if idx > 0 else None
        if not _is_tool_output(msg, prev):
            pruned.append(msg)
            continue

        msg_tokens = len_tokens(msg.content, model_name)
        if msg_tokens < _PRUNE_MIN_TOKENS:
            pruned.append(msg)
            continue

        relevance = score_tool_output_relevance(msg, idx, log)
        if relevance < _PRUNE_SCORE_THRESHOLD:
            stub = _stale_output_stub(
                msg, msg_tokens, logdir, for_estimate=for_estimate
            )
            stub_tokens = len_tokens(stub, model_name)
            tokens_saved += max(0, msg_tokens - stub_tokens)
            logger.debug(
                f"Phase 0: stubbing stale tool output at idx {idx} "
                f"(score={relevance:.2f}, tokens={msg_tokens} -> {stub_tokens})"
            )
            pruned.append(msg.replace(content=stub))
        else:
            pruned.append(msg)

    return pruned, tokens_saved


def shadow_prune_stale_tool_outputs(
    log: list[Message],
    model_name: str,
    keep_head: int = 0,
    logdir: Path | None = None,
) -> list[PruneDecision]:
    """Shadow / dry-run pass: return per-message keep/drop decisions without modifying the log.

    Runs exactly the same eligibility and scoring logic as
    :func:`prune_stale_tool_outputs`, but the original ``log`` is never changed.
    Callers get a :class:`~.scoring.PruneDecision` for every message that was
    a candidate (actual tool result, not pinned, not in the protected head),
    recording what Phase 0 *would have* done.

    This is the evaluation harness called for in issue #3997:

    - **Tokens saved** = ``sum(d.tokens_saved for d in decisions)``. Savings
      use the persisted recovery-path stub (``for_estimate=True``). When
      ``logdir`` is set the stub path matches the live pass without writing
      files; without a logdir it uses the representative placeholder — not
      the short no-logdir stub, which overstates savings.
    - **False-drop candidates** = decisions where ``decision == "drop"`` and a
      later message re-reads the same *payload* (compare ``content_digest``
      after :func:`~.scoring.normalize_for_digest`).
    - **Coverage** = fraction of conversation token budget that would have been
      freed, enabling measurement before the LLM compaction trigger fires.

    When ``logdir`` is set, the decisions are appended to
    ``phase0-shadow.jsonl`` so recorded runs keep a pre-mutation ledger.

    Only tool-output messages (``_is_tool_output`` returns ``True``) produce a
    decision record; all other messages are silently skipped.

    Args:
        log: The conversation log to analyse (never mutated).
        model_name: Model name used for token counting.
        keep_head: Number of messages at the start of the log to protect;
            messages in this range are always kept and produce no decision.
        logdir: Conversation directory. When set, persist the ledger; the log
            is still not mutated.

    Returns:
        List of :class:`~.scoring.PruneDecision` objects, one per eligible
        tool-output message, in the same order as they appear in ``log``.
    """
    decisions: list[PruneDecision] = []

    for idx, msg in enumerate(log):
        if msg.pinned or idx < keep_head:
            continue

        prev = log[idx - 1] if idx > 0 else None
        if not _is_tool_output(msg, prev):
            continue

        msg_tokens = len_tokens(msg.content, model_name)

        if msg_tokens < _PRUNE_MIN_TOKENS:
            # Below size floor — always kept; record with score=None
            decisions.append(
                PruneDecision(
                    idx=idx,
                    decision="keep",
                    score=None,
                    tokens=msg_tokens,
                    stub_tokens=0,
                    tokens_saved=0,
                    content_digest=PruneDecision._digest(msg.content),
                )
            )
            continue

        relevance = score_tool_output_relevance(msg, idx, log)

        if relevance < _PRUNE_SCORE_THRESHOLD:
            # Match the persisted / estimate stub, not the no-logdir one-liner.
            # Pass logdir through so the path shape matches the live save
            # without writing a file. The short stub overstates savings
            # (see test_phase0_estimate_uses_recovery_stub_template).
            stub = _stale_output_stub(msg, msg_tokens, logdir, for_estimate=True)
            stub_tokens = len_tokens(stub, model_name)
            saved = max(0, msg_tokens - stub_tokens)
            decisions.append(
                PruneDecision(
                    idx=idx,
                    decision="drop",
                    score=relevance,
                    tokens=msg_tokens,
                    stub_tokens=stub_tokens,
                    tokens_saved=saved,
                    content_digest=PruneDecision._digest(msg.content),
                )
            )
        else:
            decisions.append(
                PruneDecision(
                    idx=idx,
                    decision="keep",
                    score=relevance,
                    tokens=msg_tokens,
                    stub_tokens=0,
                    tokens_saved=0,
                    content_digest=PruneDecision._digest(msg.content),
                )
            )

    if logdir is not None:
        append_phase0_shadow_event(logdir, [d.to_dict() for d in decisions])

    return decisions


_REASONING_TAGS = ("<think>", "<thinking>")


def _has_reasoning_block(content: str) -> bool:
    """True if content contains inline reasoning that must not be rewritten.

    Reasoning is persisted inline (``<think>…<!-- think-sig -->``). Editing it
    destroys the provider signature (Anthropic) or strips ``reasoning_content``
    from a retained tool-calling turn (DeepSeek/Kimi).
    """
    return any(tag in content for tag in _REASONING_TAGS)


def auto_compact_log(
    log: list[Message],
    limit: int | None = None,
    max_tool_result_tokens: int = 2000,
    reasoning_strip_age_threshold: int | None = None,
    logdir: Path | None = None,
    keep_head: int = 0,
    target_ratio: float = 1.0,
) -> Generator[Message, None, None]:
    """
    Auto-compact log for conversations with massive tool results.

    More aggressive than reduce_log - implements strategic removal:
    1. Strips reasoning tags from older messages (age-based)
    2. Removes massive tool results (largest-first strategy for efficiency)
    3. Extractive compression for long assistant messages (Phase 3)

    The "truncate largest first" strategy (inspired by oh-my-opencode) ensures we
    achieve target token reduction with minimal information loss by prioritizing
    the largest outputs for truncation.

    Master Context Architecture:
    The original conversation.jsonl serves as the master context - an append-only
    log that is never compacted. When truncating content, we include byte range
    references to the master context for exact recovery. This allows aggressive
    compaction while preserving full context accessibility.

    Args:
        log: List of messages to compact
        limit: Token limit (defaults to 80% of model context for direct callers)
        max_tool_result_tokens: Maximum tokens allowed in a tool result before removal
        reasoning_strip_age_threshold: Strip reasoning from messages >N positions
            back. Disabled by default (None): age-based stripping edits the
            provider-visible prefix before retained signed thinking blocks, which
            invalidates Anthropic signatures and drops DeepSeek/Kimi
            ``reasoning_content`` on retained tool turns. Pass an int to opt in.
        logdir: Path to conversation directory for saving removed outputs
        keep_head: Number of messages at the start of the log to protect from all
            compaction (default 0 = no protection, existing behavior). Protected
            head messages are yielded verbatim and count toward the token budget but
            are never reduced. If the head alone exceeds the limit, compaction accepts
            the overshoot rather than reducing the protected task context.
        target_ratio: Fraction of ``limit`` the Phase 2 trim aims for. ``1.0``
            (default) trims to the trigger itself and preserves the previous
            behaviour. Callers that compact on a budget trigger pass a lower
            value (see ``TRIM_TARGET_RATIO``) to leave hysteresis headroom so the
            next turns do not re-trigger immediately.
    """

    # Build master context index for byte-range references
    master_context_index: list[MessageByteRange] = []
    master_logfile: Path | None = None
    if logdir:
        master_logfile = logdir / "conversation.jsonl"
        if master_logfile.exists():
            master_context_index = build_master_context_index(master_logfile)
            logger.debug(
                f"Built master context index with {len(master_context_index)} entries"
            )

    # get the token limit
    model = get_default_model() or get_model("gpt-4")
    if limit is None:
        limit = int(0.8 * model.context)

    # If we are below the configured limit and no safe projection applies, return as-is.
    tokens = len_tokens(log, model=model.model)
    initial_tokens = tokens  # preserved for final reduction_pct even after Phase 0

    # Phase 0: Relevance-scored pruning of stale tool outputs.
    # Only when already over budget — same gate as estimate_compaction_savings —
    # so under-limit logs are returned unchanged. Stubs (does not delete) old,
    # large, unreferenced tool outputs in place so tool-call pairing and
    # master-context indices stay valid. Surviving content is kept verbatim.
    phase0_tokens_saved = 0
    if tokens >= limit:
        # Record keep/drop decisions before the live pass mutates the log.
        if logdir is not None:
            shadow_prune_stale_tool_outputs(
                log,
                model.model,
                keep_head=keep_head,
                logdir=logdir,
            )
        log, phase0_tokens_saved = prune_stale_tool_outputs(
            log,
            model.model,
            keep_head=keep_head,
            logdir=logdir,
        )
        if phase0_tokens_saved > 0:
            logger.info(
                f"Phase 0 pruned {phase0_tokens_saved:,} tokens of stale tool outputs "
                f"({len(log)} messages remaining)"
            )
            tokens = len_tokens(log, model=model.model)

    # Calculate message positions from end (for age-based reasoning stripping)
    log_length = len(log)

    # Check if any reasoning stripping is needed. Off by default (see
    # ``reasoning_strip_age_threshold``) because it violates provider thinking
    # constraints on the common path.
    needs_reasoning_strip = reasoning_strip_age_threshold is not None and any(
        (log_length - idx - 1) >= reasoning_strip_age_threshold
        and _has_reasoning_block(msg.content)
        for idx, msg in enumerate(log)
    )
    needs_compacting = tokens >= limit

    # Check if any messages need Phase 3 compression
    needs_phase3_compression = any(
        (log_length - idx - 1) >= 3  # Don't compress very recent messages
        and msg.role == "assistant"  # Only compress assistant responses
        and not message_contains_tool_use(msg)  # Keep tool-call pairs parseable
        and not _has_reasoning_block(msg.content)  # Never rewrite signed thinking
        and len_tokens(msg.content, model.model) > 1000  # Only compress long messages
        for idx, msg in enumerate(log)
    )

    # Only return early if nothing more is needed (Phase 0 may have been enough)
    if (
        not needs_reasoning_strip
        and not needs_compacting
        and not needs_phase3_compression
    ):
        # Pin protected head messages even on early return so downstream
        # limit_log (e.g. in prepare_messages) cannot drop them.
        yield from (
            m.replace(pinned=True) if keep_head > 0 and idx < keep_head else m
            for idx, m in enumerate(log)
        )
        return

    if needs_compacting:
        logger.info(f"Auto-compacting log: {tokens} tokens exceeds limit of {limit}")
    if needs_reasoning_strip:
        logger.info(
            f"Stripping reasoning from messages beyond threshold {reasoning_strip_age_threshold}"
        )

    # Phase 1: Strategic reasoning stripping for older messages
    # Process all messages first to strip reasoning
    compacted_log: list[Message] = []
    reasoning_tokens_saved = 0

    for idx, msg in enumerate(log):
        if msg.pinned or idx < keep_head:
            compacted_log.append(msg)
            continue

        # Strip reasoning from messages beyond the threshold (opt-in only).
        if reasoning_strip_age_threshold is not None:
            distance_from_end = log_length - idx - 1
            if distance_from_end >= reasoning_strip_age_threshold:
                stripped_content, reasoning_saved = strip_reasoning(
                    msg.content, model.model
                )
                if reasoning_saved > 0:
                    msg = msg.replace(content=stripped_content)
                    reasoning_tokens_saved += reasoning_saved
                    logger.debug(
                        f"Stripped reasoning from message {idx}: "
                        f"saved {reasoning_saved} tokens (distance from end: {distance_from_end})"
                    )

        compacted_log.append(msg)

    # Phase 2: Truncate largest tool results first (oh-my-opencode strategy)
    # Instead of truncating all large results in order, prioritize the largest
    # This achieves target reduction with minimal information loss
    tool_result_tokens_saved = 0
    compression_tokens_saved = 0
    current_tokens = len_tokens(compacted_log, model.model)
    # Hysteresis: aim below the trigger so a successful trim leaves headroom.
    # target_ratio=1.0 keeps the pre-hysteresis behaviour for direct callers.
    target_tokens = max(1, int(limit * target_ratio))

    if current_tokens >= target_tokens:
        # Identify all candidate tool results for truncation (with original indices)
        candidates: list[tuple[int, int, Message]] = []  # (idx, tokens, msg)
        for idx, msg in enumerate(compacted_log):
            if msg.pinned or idx < keep_head:
                continue
            if msg.role == "system":
                msg_tokens = len_tokens(msg.content, model.model)
                if msg_tokens > max_tool_result_tokens:
                    candidates.append((idx, msg_tokens, msg))

        # Sort by token count descending (largest first)
        candidates.sort(key=lambda x: x[1], reverse=True)

        # Truncate largest outputs until strictly below target. Equality still
        # counts as over-budget (same inclusive trigger as should_auto_compact).
        for idx, msg_tokens, msg in candidates:
            if current_tokens < target_tokens:
                logger.info(
                    f"Reached target tokens ({current_tokens} < {target_tokens}), "
                    f"stopping truncation early"
                )
                break

            # Replace with a brief summary message
            summary_content = create_tool_result_summary(
                content=msg.content,
                original_tokens=msg_tokens,
                logdir=logdir,
                tool_name="autocompact",
            )

            # Add master context reference for exact recovery
            # Note: idx must match the message position in conversation.jsonl
            # This is safe because Phases 0-2 preserve message positions (1:1 mapping)
            if master_logfile and idx < len(master_context_index):
                byte_range = master_context_index[idx]
                # Get first line as preview
                preview = msg.content.split("\n")[0] if msg.content else None
                master_ref = create_master_context_reference(
                    logfile=master_logfile,
                    byte_range=byte_range,
                    original_tokens=msg_tokens,
                    preview=preview,
                )
                summary_content += f"\n\n{master_ref}"
                logger.debug(
                    f"Added master context reference for idx {idx}: "
                    f"bytes {byte_range.byte_start}-{byte_range.byte_end}"
                )

            summary_msg = msg.replace(content=summary_content)
            compacted_log[idx] = summary_msg

            saved = msg_tokens - len_tokens(summary_content, model.model)
            tool_result_tokens_saved += saved
            current_tokens -= saved
            logger.debug(
                f"Truncated largest tool result at idx {idx}: "
                f"{msg_tokens} -> {len_tokens(summary_content, model.model)} tokens "
                f"(saved {saved}, now at {current_tokens} tokens)"
            )

    # Phase 3: Extractive compression for long assistant messages
    compacted_log_len = len(compacted_log)
    for idx, msg in enumerate(compacted_log):
        if msg.pinned or idx < keep_head:
            continue

        distance_from_end = compacted_log_len - idx - 1
        msg_tokens = len_tokens(msg.content, model.model)

        # Compress messages >1000 tokens that aren't very recent
        if (
            distance_from_end >= 3  # Don't compress very recent messages
            and msg.role == "assistant"  # Only compress assistant responses
            and not message_contains_tool_use(msg)  # Preserve tool-call pairs
            and not _has_reasoning_block(msg.content)  # Never rewrite signed thinking
            and msg_tokens > 1000  # Only compress long messages
        ):
            compressed_content = compress_content(msg.content, target_ratio=0.7)
            compressed_tokens = len_tokens(compressed_content, model.model)

            if compressed_tokens < msg_tokens:
                # Add master context reference for exact recovery
                # Note: idx must match the message position in conversation.jsonl
                # This is safe because Phases 0-3 preserve message positions (1:1 mapping)
                if master_logfile and idx < len(master_context_index):
                    byte_range = master_context_index[idx]
                    # Get first line as preview
                    preview = msg.content.split("\n")[0] if msg.content else None
                    master_ref = create_master_context_reference(
                        logfile=master_logfile,
                        byte_range=byte_range,
                        original_tokens=msg_tokens,
                        preview=preview,
                    )
                    compressed_content += f"\n\n{master_ref}"
                    logger.debug(
                        f"Added master context reference for compressed idx {idx}"
                    )

                compacted_log[idx] = msg.replace(content=compressed_content)
                compression_saved = msg_tokens - compressed_tokens
                compression_tokens_saved += compression_saved
                logger.debug(
                    f"Compressed message {idx}: {msg_tokens} -> {compressed_tokens} tokens "
                    f"({compression_saved} saved, {(compression_saved / msg_tokens) * 100:.1f}% reduction)"
                )

    # Check if we're now within limits
    final_tokens = len_tokens(compacted_log, model.model)
    total_saved = (
        phase0_tokens_saved
        + tool_result_tokens_saved
        + compression_tokens_saved
        + reasoning_tokens_saved
    )
    if final_tokens <= limit:
        # Calculate reduction percentage against the original token count
        reduction_pct = (
            ((initial_tokens - final_tokens) / initial_tokens * 100)
            if initial_tokens > 0
            else 0.0
        )

        # Build detailed breakdown message
        breakdown_parts = []
        if phase0_tokens_saved > 0:
            pct = (phase0_tokens_saved / total_saved * 100) if total_saved > 0 else 0
            breakdown_parts.append(f"stale-prune: {phase0_tokens_saved:,} ({pct:.0f}%)")
        if reasoning_tokens_saved > 0:
            pct = (reasoning_tokens_saved / total_saved * 100) if total_saved > 0 else 0
            breakdown_parts.append(
                f"reasoning: {reasoning_tokens_saved:,} ({pct:.0f}%)"
            )
        if tool_result_tokens_saved > 0:
            pct = (
                (tool_result_tokens_saved / total_saved * 100) if total_saved > 0 else 0
            )
            breakdown_parts.append(
                f"tool results: {tool_result_tokens_saved:,} ({pct:.0f}%)"
            )
        if compression_tokens_saved > 0:
            pct = (
                (compression_tokens_saved / total_saved * 100) if total_saved > 0 else 0
            )
            breakdown_parts.append(
                f"compression: {compression_tokens_saved:,} ({pct:.0f}%)"
            )

        breakdown_str = ", ".join(breakdown_parts) if breakdown_parts else "no savings"
        logger.info(
            f"Auto-compacting successful: {initial_tokens:,} -> {final_tokens:,} tokens "
            f"({reduction_pct:.1f}% reduction, saved {total_saved:,} tokens) "
            f"[{breakdown_str}]"
        )
        # Pin protected head messages so a downstream limit_log call (e.g. in
        # prepare_messages) cannot drop them — limit_log only preserves pinned
        # or initial-system messages, not arbitrary index positions.
        yield from (
            m.replace(pinned=True) if keep_head > 0 and idx < keep_head else m
            for idx, m in enumerate(compacted_log)
        )
        return

    # If still over limit, fall back to regular reduction.
    # Protected head messages are yielded with pinned=True so any downstream
    # reduce_log call (e.g. in prepare_messages) also skips them — it only
    # respects the pinned flag, not the keep_head index.
    logger.info("Auto-compacting not sufficient, falling back to regular reduction")

    if keep_head > 0 and keep_head < len(compacted_log):
        head_msgs = [m.replace(pinned=True) for m in compacted_log[:keep_head]]
        tail_msgs = compacted_log[keep_head:]
        head_tokens = len_tokens(head_msgs, model.model)
        tail_limit = max(0, limit - head_tokens)
        yield from head_msgs
        yield from reduce_log(tail_msgs, tail_limit)
    elif keep_head >= len(compacted_log):
        # All messages are protected — accept the budget overshoot verbatim.
        # Pin every message so that if the result is still over the model's
        # context limit, a downstream reduce_log in prepare_messages cannot
        # summarize or drop task context.
        logger.info(
            "keep_head covers all %d messages; yielding pinned verbatim (accepting budget overshoot)",
            len(compacted_log),
        )
        yield from (m.replace(pinned=True) for m in compacted_log)
    else:
        yield from reduce_log(compacted_log, limit)
