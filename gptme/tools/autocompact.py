"""
Auto-compacting tool for handling conversations with massive tool results.

Automatically triggers when conversation has massive tool results that would
prevent resumption, compacting them to allow the conversation to continue.
"""

import logging
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from ..hooks import StopPropagation
from ..message import Message, len_tokens
from ..util.output_storage import create_tool_result_summary

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)

# Reentrancy guard to prevent infinite loops
_last_autocompact_time = 0.0
_autocompact_min_interval = 60  # Minimum 60 seconds between autocompact attempts


def auto_compact_log(
    log: list[Message],
    limit: int | None = None,
    max_tool_result_tokens: int = 2000,
    logdir: Path | None = None,
) -> Generator[Message, None, None]:
    """
    Auto-compact log for conversations with massive tool results.

    More aggressive than reduce_log - completely removes massive tool results
    instead of just truncating them, to allow conversation resumption.

    Args:
        log: List of messages to compact
        limit: Token limit (defaults to 80% of model context)
        max_tool_result_tokens: Maximum tokens allowed in a tool result before removal
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

    # Only return early if we're not close to limit and don't have massive tool results
    if tokens <= limit and not close_to_limit:
        yield from log
        return

    logger.info(f"Auto-compacting log: {tokens} tokens exceeds limit of {limit}")

    # Process messages and remove massive tool results
    compacted_log = []
    tokens_saved = 0

    for msg in log:
        # Skip processing pinned messages
        if msg.pinned:
            compacted_log.append(msg)
            continue

        msg_tokens = len_tokens(msg.content, model.model)

        # Check if this is a massive tool result (system message with huge content)
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
            compacted_log.append(msg)

    # Check if we're now within limits
    final_tokens = len_tokens(compacted_log, model.model)
    if final_tokens <= limit:
        logger.info(
            f"Auto-compacting successful: {tokens} -> {final_tokens} tokens (saved {tokens_saved})"
        )
        yield from compacted_log
        return

    # If still over limit, fall back to regular reduction
    logger.info("Auto-compacting not sufficient, falling back to regular reduction")
    from ..util.reduce import reduce_log

    yield from reduce_log(compacted_log, limit)


def should_auto_compact(log: list[Message], limit: int | None = None) -> bool:
    """
    Check if a log should be auto-compacted.

    Returns True if the log contains massive tool results that would benefit
    from auto-compacting rather than regular reduction.

    Auto-compacting is triggered when:
    1. The conversation exceeds the limit, OR
    2. The conversation is close to the limit (80%+) AND contains massive tool results
    """
    from ..llm.models import get_default_model, get_model

    model = get_default_model() or get_model("gpt-4")
    if limit is None:
        limit = int(0.9 * model.context)

    total_tokens = len_tokens(log, model.model)
    close_to_limit = total_tokens >= int(0.8 * model.context)  # 80% threshold

    # Check if there are any massive system messages (tool results)
    has_massive_tool_result = False
    for msg in log:
        if not msg.pinned and msg.role == "system":
            msg_tokens = len_tokens(msg.content, model.model)
            if msg_tokens > 2000:  # Threshold for "massive"
                has_massive_tool_result = True
                break

    # Trigger auto-compacting if over limit OR close to limit with massive tool results
    return total_tokens > limit or (close_to_limit and has_massive_tool_result)


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
        resume_response = llm.reply(llm_msgs, model=m.model, tools=[])
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

    import re
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
