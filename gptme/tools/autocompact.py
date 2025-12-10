"""
Auto-compacting tool for handling conversations with massive tool results.

Automatically triggers when conversation has massive tool results that would
prevent resumption, compacting them to allow the conversation to continue.
"""

import logging
import re
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from ..context import strip_reasoning
from ..hooks import StopPropagation
from ..message import Message, len_tokens
from ..util.output_storage import create_tool_result_summary

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)

# Reentrancy guard to prevent infinite loops
_last_autocompact_time = 0.0
_autocompact_min_interval = 60  # Minimum 60 seconds between autocompact attempts

# Minimum savings threshold - don't compact unless we can achieve meaningful savings
# This prevents triggering compaction that only saves a few percent (not worth cache invalidation)
MIN_SAVINGS_RATIO = 0.10  # Require at least 10% savings to justify compaction


def extract_code_blocks(content: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Extract code blocks from content, returning cleaned content and blocks.

    Args:
        content: Message content with potential code blocks

    Returns:
        Tuple of (content without code blocks, list of (marker, code block) tuples)
    """
    code_blocks: list[tuple[str, str]] = []
    code_block_pattern = r"```[\s\S]*?```"

    def replacer(match):
        marker = f"__CODE_BLOCK_{len(code_blocks)}__"
        code_blocks.append((marker, match.group(0)))
        return marker

    cleaned = re.sub(code_block_pattern, replacer, content)
    return cleaned, code_blocks


def score_sentence(sentence: str, position: int, total: int) -> float:
    """
    Score sentence importance using simple heuristics.

    Higher scores for:
    - Sentences at beginning/end (positional bias)
    - Sentences with key terms
    - Shorter sentences (more information-dense)

    Args:
        sentence: The sentence to score
        position: Position in message (0-indexed)
        total: Total number of sentences

    Returns:
        Importance score (higher = more important)
    """
    score = 0.0

    # Positional bias: first and last sentences more important
    if position == 0:
        score += 2.0
    elif position == total - 1:
        score += 1.5
    elif position < 3:
        score += 1.0

    # Key term presence
    key_terms = [
        "error",
        "fail",
        "success",
        "complete",
        "implement",
        "fix",
        "bug",
        "issue",
        "result",
        "output",
        "TODO",
        "FIXME",
        "NOTE",
        "WARNING",
    ]
    lower_sentence = sentence.lower()
    for term in key_terms:
        if term.lower() in lower_sentence:
            score += 0.5

    # Length penalty: prefer shorter, denser sentences
    # But not too short (less than 10 chars is probably not useful)
    length = len(sentence)
    if length < 10:
        score -= 1.0
    elif length < 50:
        score += 0.3
    elif length > 200:
        score -= 0.2

    return score


def compress_content(content: str, target_ratio: float = 0.7) -> str:
    """
    Compress content using extractive summarization.

    Preserves:
    - Code blocks (always kept)
    - Important sentences based on scoring
    - Overall structure

    Args:
        content: Content to compress
        target_ratio: Target length as ratio of original (0.7 = 30% reduction)

    Returns:
        Compressed content
    """
    # Extract and preserve code blocks
    cleaned, code_blocks = extract_code_blocks(content)

    # Split into sentences (simple split on . ! ?)
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    if len(sentences) <= 3:
        # Too few sentences to compress meaningfully
        return content

    # Keep sentences that contain code block markers (don't score them)
    marker_sentences = []
    scoreable_sentences = []
    for i, sent in enumerate(sentences):
        if "__CODE_BLOCK_" in sent:
            marker_sentences.append((i, sent))
        else:
            scoreable_sentences.append((i, sent))

    # Score scoreable sentences
    scored = [
        (score_sentence(sent, i, len(sentences)), i, sent)
        for i, sent in scoreable_sentences
    ]

    # Sort by score (keep highest scoring)
    scored.sort(key=lambda x: x[0], reverse=True)

    # Select top sentences to meet target ratio (excluding marker sentences)
    target_count = max(2, int(len(scoreable_sentences) * target_ratio))
    selected = scored[:target_count]

    # Combine selected sentences with marker sentences and sort by original position
    all_selected = [(i, sent) for _, i, sent in selected] + marker_sentences
    all_selected.sort(key=lambda x: x[0])

    # Reconstruct compressed content
    compressed = " ".join(sent for _, sent in all_selected)

    # Restore code blocks
    for marker, code_block in code_blocks:
        compressed = compressed.replace(marker, code_block)

    return compressed


def auto_compact_log(
    log: list[Message],
    limit: int | None = None,
    max_tool_result_tokens: int = 2000,
    reasoning_strip_age_threshold: int = 5,
    logdir: Path | None = None,
) -> Generator[Message, None, None]:
    """
    Auto-compact log for conversations with massive tool results.

    More aggressive than reduce_log - implements strategic removal:
    1. Strips reasoning tags from older messages (age-based)
    2. Removes massive tool results (existing behavior)
    3. Extractive compression for long assistant messages (Phase 3)

    Args:
        log: List of messages to compact
        limit: Token limit (defaults to 80% of model context)
        max_tool_result_tokens: Maximum tokens allowed in a tool result before removal
        reasoning_strip_age_threshold: Strip reasoning from messages >N positions back
        logdir: Path to conversation directory for saving removed outputs
    """
    from ..llm.models import get_default_model, get_model

    # get the token limit
    model = get_default_model() or get_model("gpt-4")
    if limit is None:
        limit = int(0.8 * model.context)

    # if we are below the limit AND don't need compacting, return the log as-is
    tokens = len_tokens(log, model=model.model)
    close_to_limit = tokens >= int(0.7 * model.context)

    # Calculate message positions from end (for age-based reasoning stripping)
    log_length = len(log)

    # Check if any reasoning stripping is needed
    needs_reasoning_strip = any(
        (log_length - idx - 1) >= reasoning_strip_age_threshold
        and ("<think>" in msg.content or "<thinking>" in msg.content)
        for idx, msg in enumerate(log)
    )
    needs_compacting = tokens > limit or close_to_limit

    # Check if any messages need Phase 3 compression
    needs_phase3_compression = any(
        (log_length - idx - 1) >= 3  # Don't compress very recent messages
        and msg.role == "assistant"  # Only compress assistant responses
        and len_tokens(msg.content, model.model) > 1000  # Only compress long messages
        for idx, msg in enumerate(log)
    )

    # Only return early if nothing needs processing
    if (
        not needs_reasoning_strip
        and not needs_compacting
        and not needs_phase3_compression
    ):
        yield from log
        return

    if needs_compacting:
        logger.info(f"Auto-compacting log: {tokens} tokens exceeds limit of {limit}")
    if needs_reasoning_strip:
        logger.info(
            f"Stripping reasoning from messages beyond threshold {reasoning_strip_age_threshold}"
        )

    # Process messages and remove massive tool results
    compacted_log = []
    tokens_saved = 0
    reasoning_tokens_saved = 0

    for idx, msg in enumerate(log):
        # Skip processing pinned messages
        if msg.pinned:
            compacted_log.append(msg)
            continue

        # Calculate distance from end (for age-based processing)
        distance_from_end = log_length - idx - 1

        # Phase 1: Strategic reasoning stripping for older messages
        # Strip reasoning from messages beyond the threshold
        if distance_from_end >= reasoning_strip_age_threshold:
            stripped_content, reasoning_saved = strip_reasoning(
                msg.content, model.model
            )
            if reasoning_saved > 0:
                msg = msg.replace(content=stripped_content)
                reasoning_tokens_saved += reasoning_saved
                logger.info(
                    f"Stripped reasoning from message {idx}: "
                    f"saved {reasoning_saved} tokens (distance from end: {distance_from_end})"
                )

        msg_tokens = len_tokens(msg.content, model.model)

        # Phase 2: Check if this is a massive tool result (system message with huge content)
        # Use same logic as should_auto_compact: over limit OR close to limit with massive tool result
        close_to_limit = tokens >= int(0.8 * model.context)
        if (
            msg.role == "system"
            and msg_tokens > max_tool_result_tokens
            and (tokens > limit or close_to_limit)
        ):
            # Replace with a brief summary message
            summary_content = create_tool_result_summary(
                content=msg.content,
                original_tokens=msg_tokens,
                logdir=logdir,
                tool_name="autocompact",
            )
            summary_msg = msg.replace(content=summary_content)
            compacted_log.append(summary_msg)

            tokens_saved += msg_tokens - len_tokens(summary_content, model.model)
            logger.info(
                f"Removed massive tool result: {msg_tokens} tokens -> {len_tokens(summary_content, model.model)} tokens"
            )
        else:
            # Phase 3: Extractive compression for long assistant messages
            # Compress messages >1000 tokens that aren't very recent
            if (
                distance_from_end >= 3  # Don't compress very recent messages
                and msg.role == "assistant"  # Only compress assistant responses
                and msg_tokens > 1000  # Only compress long messages
            ):
                compressed_content = compress_content(msg.content, target_ratio=0.7)
                compressed_tokens = len_tokens(compressed_content, model.model)

                if compressed_tokens < msg_tokens:
                    msg = msg.replace(content=compressed_content)
                    compression_saved = msg_tokens - compressed_tokens
                    tokens_saved += compression_saved
                    logger.info(
                        f"Compressed message {idx}: {msg_tokens} -> {compressed_tokens} tokens "
                        f"({compression_saved} saved, {(compression_saved/msg_tokens)*100:.1f}% reduction)"
                    )

            compacted_log.append(msg)

    # Check if we're now within limits
    final_tokens = len_tokens(compacted_log, model.model)
    total_saved = tokens_saved + reasoning_tokens_saved
    if final_tokens <= limit:
        logger.info(
            f"Auto-compacting successful: {tokens} -> {final_tokens} tokens "
            f"(saved {total_saved}: {tokens_saved} from tool results, "
            f"{reasoning_tokens_saved} from reasoning)"
        )
        yield from compacted_log
        return

    # If still over limit, fall back to regular reduction
    logger.info("Auto-compacting not sufficient, falling back to regular reduction")
    from ..util.reduce import reduce_log

    yield from reduce_log(compacted_log, limit)


def estimate_compaction_savings(
    log: list[Message],
    limit: int | None = None,
    max_tool_result_tokens: int = 2000,
    reasoning_strip_age_threshold: int = 5,
    assistant_compression_age_threshold: int = 3,
    assistant_compression_min_tokens: int = 1000,
) -> tuple[int, int, int]:
    """
    Estimate potential savings from auto-compaction without actually compacting.

    Args:
        log: The conversation log to estimate savings for
        limit: Token limit (defaults to 90% of model context)
        max_tool_result_tokens: Threshold for "massive" tool results
        reasoning_strip_age_threshold: Distance from end before stripping reasoning
        assistant_compression_age_threshold: Distance from end before compressing assistant messages
        assistant_compression_min_tokens: Minimum tokens for assistant message compression

    Returns:
        Tuple of (total_tokens, estimated_savings, savings_from_reasoning)

    This allows us to decide if compaction is worth the cost (cache invalidation)
    before actually triggering it.

    Note: Estimation matches actual compaction logic:
    - Phase 1: Reasoning stripping (always applied to old messages)
    - Phase 2: Tool result removal (only when over/close to limit)
    - Phase 3: Assistant message compression (only when over/close to limit)
    """
    from ..llm.models import get_default_model, get_model

    model = get_default_model() or get_model("gpt-4")
    total_tokens = len_tokens(log, model.model)
    log_length = len(log)

    if limit is None:
        limit = int(0.9 * model.context)

    # Match actual compaction logic: only remove tool results when over/close to limit
    close_to_limit = total_tokens >= int(0.8 * model.context)
    would_remove_tool_results = total_tokens > limit or close_to_limit

    estimated_tool_result_savings = 0
    estimated_reasoning_savings = 0
    estimated_compression_savings = 0

    for idx, msg in enumerate(log):
        if msg.pinned:
            continue

        msg_tokens = len_tokens(msg.content, model.model)
        distance_from_end = log_length - idx - 1

        # Phase 1: Estimate reasoning stripping savings (always applied to old messages)
        if distance_from_end >= reasoning_strip_age_threshold:
            if "<think>" in msg.content or "<thinking>" in msg.content:
                # Rough estimate: reasoning usually ~30-50% of content
                estimated_reasoning_savings += int(msg_tokens * 0.3)

        # Phase 2: Estimate massive tool result removal savings
        # CRITICAL: Only count if we would actually remove tool results
        if (
            would_remove_tool_results
            and msg.role == "system"
            and msg_tokens > max_tool_result_tokens
        ):
            # Tool results get reduced to summary (~200 tokens)
            estimated_tool_result_savings += msg_tokens - 200

        # Phase 3: Estimate assistant message compression savings
        # Only for older messages (distance >= threshold) with enough tokens
        if (
            would_remove_tool_results
            and distance_from_end >= assistant_compression_age_threshold
            and msg.role == "assistant"
            and msg_tokens > assistant_compression_min_tokens
        ):
            # Compression targets 70% of original, so saves ~30%
            estimated_compression_savings += int(msg_tokens * 0.3)

    total_estimated_savings = (
        estimated_tool_result_savings
        + estimated_reasoning_savings
        + estimated_compression_savings
    )
    return total_tokens, total_estimated_savings, estimated_reasoning_savings


def should_auto_compact(log: list[Message], limit: int | None = None) -> bool:
    """
    Check if a log should be auto-compacted.

    Returns True if the log contains massive tool results that would benefit
    from auto-compacting rather than regular reduction.

    Auto-compacting is triggered when:
    1. The conversation exceeds the limit, OR
    2. The conversation is close to the limit (80%+) AND contains massive tool results
    3. AND estimated savings exceed MIN_SAVINGS_RATIO (to justify cache invalidation)
    """
    from ..llm.models import get_default_model, get_model

    model = get_default_model() or get_model("gpt-4")
    if limit is None:
        limit = int(0.9 * model.context)

    total_tokens = len_tokens(log, model.model)
    close_to_limit = total_tokens >= int(
        0.5 * model.context
    )  # 50% threshold (more proactive)

    # Check if there are any massive system messages (tool results)
    has_massive_tool_result = False
    for msg in log:
        if not msg.pinned and msg.role == "system":
            msg_tokens = len_tokens(msg.content, model.model)
            if msg_tokens > 2000:  # Threshold for "massive"
                has_massive_tool_result = True
                break

    # First check: would we trigger based on token count?
    would_trigger = total_tokens > limit or (close_to_limit and has_massive_tool_result)

    if not would_trigger:
        return False

    # Second check: estimate if savings would be worth it
    total, estimated_savings, reasoning_savings = estimate_compaction_savings(
        log, limit
    )
    savings_ratio = estimated_savings / total if total > 0 else 0

    if savings_ratio < MIN_SAVINGS_RATIO:
        logger.info(
            f"Skipping auto-compact: estimated savings {savings_ratio:.1%} "
            f"({estimated_savings} tokens) below threshold {MIN_SAVINGS_RATIO:.0%}. "
            f"Consider using '/compact resume' for LLM-powered summarization."
        )
        return False

    logger.debug(
        f"Auto-compact viable: estimated {savings_ratio:.1%} savings "
        f"({estimated_savings} tokens, {reasoning_savings} from reasoning)"
    )
    return True


def cmd_compact_handler(ctx) -> Generator[Message, None, None]:
    """Command handler for /compact - compact the conversation using auto-compacting or LLM-powered resume generation."""

    ctx.manager.undo(1, quiet=True)

    # Parse arguments
    method = ctx.args[0] if ctx.args else "auto"

    if method not in ["auto", "resume"]:
        yield Message(
            "system",
            "Invalid compact method. Use 'auto' for auto-compacting or 'resume' for LLM-powered resume generation.\n"
            "Usage: /compact [auto|resume]",
        )
        return

    msgs = ctx.manager.log.messages[:-1]  # Exclude the /compact command itself

    if method == "auto":
        yield from _compact_auto(ctx, msgs)
    elif method == "resume":
        yield from _compact_resume(ctx, msgs)


def _compact_auto(ctx, msgs: list[Message]) -> Generator[Message, None, None]:
    """Auto-compact using the aggressive compacting algorithm."""
    from ..llm.models import get_default_model
    from ..logmanager import Log

    if not should_auto_compact(msgs):
        yield Message(
            "system",
            "Auto-compacting not needed. Conversation doesn't contain massive tool results or isn't close to context limits.",
        )
        return

    # Apply auto-compacting
    compacted_msgs = list(auto_compact_log(msgs, logdir=ctx.manager.logdir))

    # Calculate reduction stats
    original_count = len(msgs)
    compacted_count = len(compacted_msgs)
    m = get_default_model()
    original_tokens = len_tokens(msgs, m.model) if m else 0
    compacted_tokens = len_tokens(compacted_msgs, m.model) if m else 0

    # Replace the conversation history
    ctx.manager.log = Log(compacted_msgs)
    ctx.manager.write()

    yield Message(
        "system",
        f"✅ Auto-compacting completed:\n"
        f"• Messages: {original_count} → {compacted_count}\n"
        f"• Tokens: {original_tokens:,} → {compacted_tokens:,} "
        f"({((original_tokens - compacted_tokens) / original_tokens * 100):.1f}% reduction)",
    )


def _compact_resume(ctx, msgs: list[Message]) -> Generator[Message, None, None]:
    """LLM-powered compact that creates RESUME.md, suggests files to include, and starts a new conversation with the context."""
    from .. import llm
    from ..llm.models import get_default_model
    from ..logmanager import Log, prepare_messages

    # Prepare messages for summarization
    prepared_msgs = prepare_messages(msgs)
    visible_msgs = [m for m in prepared_msgs if not m.hide]

    if len(visible_msgs) < 3:
        yield Message(
            "system", "Not enough conversation history to create a meaningful resume."
        )
        return

    # Generate conversation summary using LLM
    yield Message("system", "🔄 Generating conversation resume with LLM...")

    resume_prompt = """Please create a comprehensive resume of this conversation that includes:

1. **Conversation Summary**: Key topics, decisions made, and progress achieved
2. **Technical Context**: Important code changes, configurations, or technical details
3. **Current State**: What was accomplished and what remains to be done
4. **Context Files**: Suggest which files should be included in future context (with brief rationale)

Format the response as a structured document that could serve as a RESUME.md file."""

    # Create a temporary message for the LLM prompt
    resume_request = Message("user", resume_prompt)
    llm_msgs = visible_msgs + [resume_request]

    try:
        # Generate the resume using LLM
        m = get_default_model()
        assert m
        resume_response = llm.reply(llm_msgs, model=m.model, tools=[], workspace=None)
        resume_content = resume_response.content

        # Save RESUME.md file
        resume_path = Path("RESUME.md")
        with open(resume_path, "w") as f:
            f.write(resume_content)

        # Create a compact conversation with just the resume
        system_msg = Message(
            "system", f"Previous conversation resumed from {resume_path}:"
        )
        resume_msg = Message("assistant", resume_content)

        # Replace conversation history with resume
        # TODO: fork into a new conversation?
        ctx.manager.log = Log([system_msg, resume_msg])
        ctx.manager.write()

        yield Message(
            "system",
            f"✅ LLM-powered resume completed:\n"
            f"• Original conversation ({len(visible_msgs)} messages) compressed to resume\n"
            f"• Resume saved to: {resume_path.absolute()}\n"
            f"• Conversation history replaced with resume\n"
            f"• Review the RESUME.md file for suggested context files",
        )

    except Exception as e:
        yield Message("system", f"❌ Failed to generate resume: {e}")


def _get_compacted_name(conversation_name: str) -> str:
    """
    Get a unique name for the compacted conversation fork.

    The original conversation stays untouched as the backup.
    The fork gets a new name with timestamp to identify when compaction occurred.

    Strips any existing -compacted-YYYYMMDD-HHMMSS suffixes to prevent accumulation
    on repeated compactions.

    Examples:
    - "my-conversation" -> "my-conversation-compacted-20251029-073045"
    - "my-conversation-compacted-20251029-073045" -> "my-conversation-compacted-20251029-080000"

    Args:
        conversation_name: The current conversation directory name

    Returns:
        The compacted conversation name with timestamp

    Raises:
        ValueError: If conversation_name is empty
    """
    if not conversation_name:
        raise ValueError("conversation name cannot be empty")

    from datetime import datetime

    # Strip any existing compacted suffixes: -compacted-YYYYMMDDHHMM
    # This handles repeated compactions by removing previous timestamps
    base_name = conversation_name
    while True:
        # Match -compacted-{8 digits}-{6 digits} pattern
        new_name = re.sub(r"-compacted-\d{8}-\d{6}$", "", base_name)
        if new_name == base_name:  # No more changes
            break
        base_name = new_name

    if not base_name:  # Safety: if entire name was the suffix (shouldn't happen)
        base_name = conversation_name

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{base_name}-compacted-{timestamp}"


def autocompact_hook(
    manager: "LogManager",
) -> Generator[Message | StopPropagation, None, None]:
    """
    Hook that checks if auto-compacting is needed and applies it.

    Runs after each message is processed to check if the conversation
    has grown too large with massive tool results.

    If compacting is needed:
    1. Creates compacted fork (original stays as backup)
    2. Manager switches to fork automatically
    3. Applies auto-compacting to fork conversation
    4. User continues in compacted conversation

    Args:
        manager: Conversation manager with log and workspace
    """

    import time

    from ..llm.models import get_default_model
    from ..logmanager import Log
    from ..message import len_tokens

    global _last_autocompact_time

    # Check if enough time has passed since last autocompact attempt
    current_time = time.time()
    if current_time - _last_autocompact_time < _autocompact_min_interval:
        logger.debug(
            f"Skipping autocompact: {current_time - _last_autocompact_time:.1f}s "
            f"since last attempt (min interval: {_autocompact_min_interval}s)"
        )
        return

    messages = manager.log.messages

    if not should_auto_compact(messages):
        return

    logger.info("Auto-compacting triggered: conversation has massive tool results")
    _last_autocompact_time = current_time

    # Create compacted fork (original stays as backup)
    fork_name = _get_compacted_name(manager.logfile.parent.name)
    try:
        # Fork creates compacted conversation (manager switches to fork automatically)
        manager.fork(fork_name)

        logger.info(f"Created compacted conversation: '{fork_name}'")
    except Exception as e:
        logger.error(f"Failed to fork conversation: {e}")
        yield Message(
            "system",
            f"⚠️ Auto-compact: Failed to fork conversation: {e}\n"
            "Skipping auto-compact to preserve safety.",
            hide=False,
        )
        return

    # Apply auto-compacting with comprehensive error handling
    try:
        compacted_msgs = list(auto_compact_log(messages, logdir=manager.logdir))

        # Calculate reduction stats
        m = get_default_model()
        original_count = len(messages)
        compacted_count = len(compacted_msgs)
        original_tokens = len_tokens(messages, m.model) if m else 0
        compacted_tokens = len_tokens(compacted_msgs, m.model) if m else 0

        # Replace the log with compacted version
        manager.log = Log(compacted_msgs)
        manager.write()

        # Yield a message indicating what happened
        yield Message(
            "system",
            f"🔄 Auto-compacted conversation due to massive tool results:\n"
            f"• Messages: {original_count} → {compacted_count}\n"
            f"• Tokens: {original_tokens:,} → {compacted_tokens:,} "
            f"({((original_tokens - compacted_tokens) / original_tokens * 100):.1f}% reduction)\n"
            f"Original state preserved in '{fork_name}'.",
            hide=True,  # Hide to prevent triggering responses
        )
    except Exception as e:
        logger.error(f"Auto-compact failed during compaction: {e}")
        # Don't yield error message to avoid triggering more hooks
        return


from ..hooks import HookType

# Tool specification
from .base import ToolSpec

tool = ToolSpec(
    name="autocompact",
    desc="Automatically compact conversations with massive tool results",
    instructions="",  # No user-facing instructions, runs automatically
    hooks={
        "autocompact": (
            HookType.MESSAGE_POST_PROCESS,
            autocompact_hook,
            100,
        ),  # Low priority, runs after other hooks
    },
    commands={
        "compact": cmd_compact_handler,
    },
)
__doc__ = tool.desc
