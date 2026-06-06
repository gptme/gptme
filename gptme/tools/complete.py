"""Complete tool - signals that the autonomous session is finished."""

import logging
import os
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from ..hooks import HookType, StopPropagation
from ..message import Message
from .base import ToolSpec, ToolUse
from .todo import get_incomplete_todos_summary, has_incomplete_todos

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)


class SessionCompleteException(Exception):
    """Exception raised to signal that the session should end."""


def execute_complete(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Message:
    """Signal that the autonomous session is complete and ready to exit."""
    return Message(
        "system",
        "Task complete. Autonomous session finished.",
        quiet=False,
    )


def complete_hook(
    messages: list[Message],
    **kwargs,
) -> Generator[Message | StopPropagation, None, None]:
    """
    Hook that detects complete tool call and prevents next generation.

    Runs at GENERATION_PRE (before generating response) to stop the session
    immediately after complete tool is called.

    Args:
        messages: List of conversation messages
        **kwargs: Additional arguments (workspace, manager - currently unused)

    Note: GENERATION_PRE hooks are called with messages as first positional arg,
    not manager as the Protocol suggests. This is a known type safety issue.
    """
    # Make function a generator for type checking
    if False:
        yield

    logger.debug(f"complete_hook: checking {len(messages) if messages else 0} messages")

    if not messages:
        logger.debug("complete_hook: no messages")
        return

    # Only look at assistant messages in the CURRENT turn (after the last user message).
    # This prevents re-triggering when subsequent chained prompts are processed:
    # after the second prompt is appended, the last user message is that prompt,
    # and there are no assistant messages after it yet, so we correctly do nothing.
    last_user_idx = next(
        (
            len(messages) - 1 - i
            for i, m in enumerate(reversed(messages))
            if m.role == "user"
        ),
        None,
    )
    current_turn = (
        messages[last_user_idx + 1 :] if last_user_idx is not None else messages
    )

    last_assistant_msg = next(
        (m for m in reversed(current_turn) if m.role == "assistant"), None
    )
    if not last_assistant_msg:
        logger.debug("complete_hook: no assistant messages in current turn")
        return

    logger.debug(
        "complete_hook: checking last assistant message in current turn for complete tool call"
    )

    # Check if the assistant called the complete tool
    tool_uses = list(ToolUse.iter_from_content(last_assistant_msg.content))
    for tool_use in tool_uses:
        if tool_use.tool == "complete":
            logger.info("Complete tool call detected, stopping session immediately")
            raise SessionCompleteException("Session completed via complete tool")

    logger.debug("complete_hook: complete tool not detected")


def _auto_reply_nudge_interactive(
    manager: "LogManager",
) -> Generator[Message | StopPropagation, None, None]:
    """Gentle nudge for think-only responses in interactive+no_confirm mode.

    Injects a single quiet nudge message when the assistant produces a
    think-only response in -y mode, then returns without exit logic.
    Only nudges once per conversation — after a nudge, subsequent
    think-only responses are silently ignored (the user can type "continue"
    themselves without being spammed).
    """
    last_assistant_msg = next(
        (m for m in reversed(manager.log.messages) if m.role == "assistant"), None
    )
    if not last_assistant_msg:
        return

    tool_uses = list(ToolUse.iter_from_content(last_assistant_msg.content))
    if tool_uses:
        return  # Has tools, no need to nudge

    # Count existing nudges — only nudge once per think-only sequence
    nudge_count = 0
    for msg in reversed(manager.log.messages):
        if msg.role == "user" and "No tool call detected" in msg.content:
            nudge_count += 1
        elif msg.role == "assistant":
            # Stop counting when we hit an assistant message with tools
            if list(ToolUse.iter_from_content(msg.content)):
                break
        else:
            break

    # Only nudge once — if a nudge was already injected, don't pile on
    if nudge_count >= 1:
        return

    logger.info("Auto-nudge: think-only in -y mode, injecting continuation hint")
    yield Message(
        "user",
        "<system>No tool call detected. Please continue with a tool call, or use `complete` if done.</system>",
        quiet=True,
    )


def auto_reply_hook(
    manager: "LogManager",
    interactive: bool,
    prompt_queue: Any,
    no_confirm: bool = False,
) -> Generator[Message | StopPropagation, None, None]:
    """
    Hook that implements auto-reply mechanism for autonomous operation.

    If in non-interactive mode and last assistant message had no tools,
    inject an auto-reply to ensure the assistant does work.

    In interactive + no_confirm mode (gptme -y), inject a quiet nudge once
    to avoid piling on, then let the loop continue naturally.

    This is called via LOOP_CONTINUE hook, which receives interactive, prompt_queue,
    and no_confirm.

    Args:
        manager: Conversation manager with log and workspace
        interactive: Whether in interactive mode
        prompt_queue: Queue of pending prompts
        no_confirm: Whether tool confirmations are skipped (--no-confirm / -y mode)
    """
    # In interactive mode without -y, skip (real human conversation)
    if interactive and not no_confirm:
        return

    # In interactive + no_confirm mode: gentle nudge, no exit path
    if interactive and no_confirm:
        yield from _auto_reply_nudge_interactive(manager)
        return

    # Non-interactive mode: existing auto-reply logic with 2x exit

    # Skip if there are queued prompts
    if prompt_queue:
        return

    last_assistant_msg = next(
        (m for m in reversed(manager.log.messages) if m.role == "assistant"), None
    )
    if not last_assistant_msg:
        return

    tool_uses = list(ToolUse.iter_from_content(last_assistant_msg.content))
    if tool_uses:
        return  # Has tools, no need to prompt

    # Count consecutive auto-replies
    auto_reply_count = 0
    for msg in reversed(manager.log.messages):
        if msg.role == "user" and "use the `complete` tool" in msg.content:
            auto_reply_count += 1
        elif msg.role == "assistant":
            # Stop counting when we hit an assistant message with tools
            if list(ToolUse.iter_from_content(msg.content)):
                break
        else:
            break

    # Exit after 2 consecutive auto-replies without tools
    if auto_reply_count >= 2:
        logger.warning("Autonomous mode: No tools used after 2 confirmations. Exiting.")
        raise SessionCompleteException("No tools used after 2 auto-reply confirmations")

    # First time - inject auto-reply
    # Check for incomplete todos - if present, remind about them instead of asking about completion
    if has_incomplete_todos():
        incomplete_summary = get_incomplete_todos_summary()
        logger.warning(
            "Auto-reply: Assistant had no tools but has incomplete todos. Reminding to continue..."
        )
        yield Message(
            "user",
            f"<system>No tool call detected in last message. You have incomplete todos:\n{incomplete_summary}\n\nPlease continue working on these tasks, or mark them complete/remove them before finishing.</system>",
            quiet=False,
        )
    else:
        logger.warning(
            "Auto-reply: Assistant message had no tools. Asking for confirmation..."
        )
        yield Message(
            "user",
            "<system>No tool call detected in last message. Did you mean to finish? If so, make sure you are completely done and then use the `complete` tool to end the session.</system>",
            quiet=False,
        )


def _env_flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid int for %s=%r, using default %d", name, raw, default)
        return default


STUCK_MARKER = "appear stuck"


def _turn_fingerprint(msg: "Message") -> tuple | None:
    """Fingerprint an assistant turn's tool uses for stuck detection.

    Returns a sorted, order-independent multiset of (tool, args, body) for all
    tool uses in the message, or None if the message has no tool uses. Two turns
    with identical fingerprints reissue exactly the same action(s); a different
    file, arg, or body produces a different fingerprint and resets the count.
    """
    uses = list(ToolUse.iter_from_content(msg.content))
    if not uses:
        return None
    return tuple(
        sorted(
            (u.tool or "", tuple(u.args or ()), (u.content or "").strip()) for u in uses
        )
    )


def stuck_detect_hook(
    manager: "LogManager",
    interactive: bool,
    prompt_queue: Any,
    no_confirm: bool = False,
) -> Generator[Message | StopPropagation, None, None]:
    """Detect a stuck agent that keeps issuing the same tool call(s).

    Unlike ``auto_reply_hook`` (which only acts when the last assistant message
    has *no* tool uses), this hook fires when the agent *does* emit tool uses but
    keeps repeating an identical action without progress — a silent failing loop
    that would otherwise run until the budget or session timeout is hit.

    Registered as a separate LOOP_CONTINUE hook at higher priority than
    ``auto_reply_hook`` so it can observe the yes-tool-but-repeating case the
    latter early-returns on. Mutates nothing; only yields a system nudge and,
    after repeated escalations, raises SessionCompleteException.

    See gptme/gptme#2725 and the design note in Bob's workspace.
    """
    # Only run in non-interactive mode — a human can break their own loop.
    if interactive:
        return

    if not _env_flag("GPTME_STUCK_DETECT", "1"):
        return

    # Skip if there are queued prompts (mirrors auto_reply_hook).
    if prompt_queue:
        return

    repeat_threshold = _env_int("GPTME_STUCK_REPEAT_THRESHOLD", 3)
    escalate_max = _env_int("GPTME_STUCK_ESCALATE_MAX", 2)
    if repeat_threshold < 2:
        return  # detection disabled by config

    # Fingerprint the most recent consecutive run of assistant turns.
    assistant_msgs = [
        m for m in reversed(manager.log.messages) if m.role == "assistant"
    ]
    if len(assistant_msgs) < repeat_threshold:
        return

    latest_fp = _turn_fingerprint(assistant_msgs[0])
    if latest_fp is None:
        return  # no tool uses → auto_reply_hook's concern, not ours

    repeats = 1
    for msg in assistant_msgs[1:]:
        if _turn_fingerprint(msg) == latest_fp:
            repeats += 1
        else:
            break
    if repeats < repeat_threshold:
        return

    # Count how many times we've already escalated this stuck run (walk back over
    # injected stuck markers, stopping at the first non-matching assistant turn).
    escalation_count = 0
    for msg in reversed(manager.log.messages):
        if msg.role == "user" and STUCK_MARKER in msg.content:
            escalation_count += 1
        elif msg.role == "assistant":
            if _turn_fingerprint(msg) != latest_fp:
                break
        elif msg.role == "system":
            continue  # skip tool results — always present between turns in real sessions
        else:
            break

    # Collect all unique tool names from the repeated fingerprint (multi-tool turns
    # would show only the first alphabetically if we used latest_fp[0][0]).
    repeated_tools = sorted({fp[0] for fp in latest_fp}) if latest_fp else ["?"]
    repeated_tool_str = "/".join(repeated_tools)

    if escalation_count >= escalate_max:
        logger.warning(
            "Stuck loop not broken after %d escalations (repeated `%s`). Exiting.",
            escalate_max,
            repeated_tool_str,
        )
        raise SessionCompleteException(
            f"Stuck loop not broken after {escalate_max} escalations"
        )

    logger.warning(
        "Stuck detected: `%s` repeated %d times without progress. Nudging.",
        repeated_tool_str,
        repeats,
    )
    yield Message(
        "user",
        (
            f"<system>You appear stuck: the same tool call (`{repeated_tool_str}`) was "
            f"repeated {repeats} times without progress. Try a different approach, "
            f"fix the underlying error, or use the `complete` tool if you are "
            f"genuinely blocked.</system>"
        ),
        quiet=False,
    )


tool = ToolSpec(
    name="complete",
    desc="Signal that the autonomous session is finished",
    disabled_by_default=True,  # Only enable in autonomous/non-interactive sessions
    instructions="""
Use this tool to signal that you have completed your work and the autonomous session should end.

Make sure you have actually completely finished before calling this tool.

### When to use complete

Use only after all requested work is done and committed. Do not call it mid-task or while blocked on something fixable — only call when work is genuinely finished or you have hit a hard blocker requiring human intervention.
""",
    examples="""
> User: Everything done, just complete
> Assistant: I'll use the complete tool to end the session.
```complete
```
> System: Task complete. Autonomous session finished.
""",
    execute=execute_complete,
    block_types=["complete"],
    available=True,
    hooks={
        "complete": (
            HookType.GENERATION_PRE.value,
            complete_hook,
            1000,
        ),  # High priority - prevent generation after complete
        "auto_reply": (
            HookType.LOOP_CONTINUE,
            auto_reply_hook,
            999,
        ),  # Run after complete check (lower priority)
        "stuck_detect": (
            HookType.LOOP_CONTINUE,
            stuck_detect_hook,
            1000,
        ),  # Run before auto_reply: catches repeating-tool loops it can't see
    },
)
