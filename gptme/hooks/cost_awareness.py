"""Cost awareness hook for tracking and reporting LLM costs.

Integrates with the CostTracker to:
- Initialize cost tracking at session start
- Emit cost warnings at configurable thresholds
- Provide cost data for eval framework integration

See Issue #935 for design context.
"""

import logging
from collections.abc import Generator
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..hooks import HookType, StopPropagation, register_hook
from ..message import Message
from ..util.cost_tracker import CostTracker

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)

# Thread-safe storage for pending warning to inject on next user message
_pending_warning_var: ContextVar[str | None] = ContextVar(
    "pending_warning", default=None
)

# Default cost warning thresholds (in USD)
# Warns when total session cost crosses these values
# Includes $10 increments after the first $10 for extended sessions
COST_WARNING_THRESHOLDS = [
    0.10,
    0.50,
    1.00,
    5.00,  # Early warnings
    10.00,
    20.00,
    30.00,
    40.00,
    50.00,
    60.00,
    70.00,
    80.00,
    90.00,
    100.00,  # $10 increments up to $100
    200.00,
    500.00,
    1000.00,  # Large session warnings
]


def session_start_cost_tracking(
    logdir: Path, workspace: Path | None, initial_msgs: list[Message]
) -> Generator[Message | StopPropagation, None, None]:
    """Initialize cost tracking at session start.

    Args:
        logdir: Log directory path (used as session ID)
        workspace: Workspace directory path
        initial_msgs: Initial messages in the conversation

    Yields:
        Nothing - just initializes tracking
    """
    session_id = str(logdir)
    CostTracker.start_session(session_id)
    logger.debug(f"Cost tracking started for session: {session_id}")
    yield from ()


def cost_warning_hook(
    manager: "LogManager",
) -> Generator[Message | StopPropagation, None, None]:
    """Emit cost warnings when session cost crosses thresholds.

    Called after each message is processed. Checks if total session cost
    has crossed any warning thresholds and prints it to the user. The warning
    will be injected as a system message on the next user input.

    Args:
        manager: The LogManager for the current session

    Yields:
        Nothing - warning is stored for later injection
    """

    costs = CostTracker.get_session_costs()
    if not costs or not costs.entries:
        return

    total = costs.total_cost

    # Find if we crossed any threshold with the last request
    last_entry = costs.entries[-1]
    prev_cost = total - last_entry.cost

    for threshold in COST_WARNING_THRESHOLDS:
        if prev_cost < threshold <= total:
            # Crossed this threshold
            cache_hit_pct = costs.cache_hit_rate * 100
            warning_text = (
                f"<system_warning>Session cost reached ${total:.2f} "
                f"(tokens: {costs.total_input_tokens:,}/{costs.total_output_tokens:,} in/out, "
                f"cache hit: {cache_hit_pct:.1f}%)</system_warning>"
            )

            # Store warning to inject on next user message
            _pending_warning_var.set(warning_text)

            # Log the warning with full details
            logger.info(
                f"Session cost reached ${total:.2f} "
                f"(tokens: {costs.total_input_tokens:,} in / {costs.total_output_tokens:,} out, "
                f"cache: {costs.total_cache_read_tokens:,} read / {costs.total_cache_creation_tokens:,} created, "
                f"hit rate: {cache_hit_pct:.1f}%) "
                f"[threshold: ${threshold:.2f}]"
            )
            # Only emit one warning per request
            break

    yield from ()


def inject_pending_warning(
    messages: list[Message],
    **kwargs: Any,
) -> Generator[Message | StopPropagation, None, None]:
    """Inject pending cost warning before generating response.

    If a cost warning was triggered on the previous assistant response,
    inject it as a system message before generating the next response.
    This way the assistant sees the warning in context with the user's message.

    Args:
        messages: List of conversation messages
        **kwargs: Additional keyword arguments (workspace, manager, etc.)

    Yields:
        System message with pending warning if one exists
    """
    pending_warning = _pending_warning_var.get()

    # Only inject if there's a pending warning and the last message is from user
    if not pending_warning:
        return

    if messages and messages[-1].role == "user":
        yield Message(
            "system",
            pending_warning,
            hide=True,
        )
        _pending_warning_var.set(None)  # Clear after injecting

    yield from ()


def session_end_cost_summary(
    manager: "LogManager", **kwargs
) -> Generator[Message | StopPropagation, None, None]:
    """Display brief cost summary at session end.

    Args:
        manager: The LogManager for the session
        **kwargs: Additional arguments (e.g., logdir)

    Yields:
        Nothing - just prints to console
    """
    from ..util import console

    costs = CostTracker.get_session_costs()
    if not costs or not costs.entries:
        return

    total = costs.total_cost
    if total == 0:
        return

    # Count turns (assistant responses)
    turns = len(costs.entries)

    # Final context size from last request (input + cache tokens)
    last = costs.entries[-1]
    final_context = (
        last.input_tokens + last.cache_read_tokens + last.cache_creation_tokens
    )

    # Format context size (use k suffix for readability)
    if final_context >= 1000:
        context_str = f"{final_context / 1000:.0f}k"
    else:
        context_str = str(final_context)

    # Brief summary on exit
    cache_pct = costs.cache_hit_rate * 100
    console.log(
        f"[dim]Session: ${total:.2f} | {turns} turns | "
        f"{context_str} context | {cache_pct:.0f}% cached[/dim]"
    )

    yield from ()


def register() -> None:
    """Register the cost awareness hooks with the hook system."""
    register_hook(
        "cost_awareness.session_start",
        HookType.SESSION_START,
        session_start_cost_tracking,
        priority=10,  # High priority to initialize early
    )
    register_hook(
        "cost_awareness.cost_warning",
        HookType.MESSAGE_POST_PROCESS,
        cost_warning_hook,
        priority=0,  # Normal priority
    )
    register_hook(
        "cost_awareness.inject_warning",
        HookType.GENERATION_PRE,
        inject_pending_warning,
        priority=5,  # Run early but after critical pre-processing
    )
    register_hook(
        "cost_awareness.session_end",
        HookType.SESSION_END,
        session_end_cost_summary,
        priority=-10,  # Low priority to run last
    )
    logger.debug("Registered cost awareness hooks")
