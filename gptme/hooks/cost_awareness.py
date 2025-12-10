"""Cost awareness hook for tracking and reporting LLM costs.

Integrates with the CostTracker to:
- Initialize cost tracking at session start
- Emit cost warnings at configurable thresholds
- Provide cost data for eval framework integration

See Issue #935 for design context.
"""

import logging
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from ..hooks import HookType, StopPropagation, register_hook
from ..message import Message
from ..util.cost_tracker import CostTracker

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)

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
    has crossed any warning thresholds and emits a system warning if so.

    Args:
        manager: The LogManager for the current session

    Yields:
        System message with cost warning if threshold crossed
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
            yield Message(
                "system",
                f"<system_warning>Session cost reached ${total:.2f} "
                f"(tokens: {costs.total_input_tokens:,}/{costs.total_output_tokens:,} in/out, "
                f"cache hit: {cache_hit_pct:.1f}%)</system_warning>",
                hide=True,
            )
            logger.info(
                f"Cost warning: ${total:.2f} (threshold ${threshold:.2f}), "
                f"cache hit rate: {cache_hit_pct:.1f}%"
            )
            # Only emit one warning per request
            break


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
    logger.debug("Registered cost awareness hooks")
