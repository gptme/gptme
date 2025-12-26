"""Parallel tool execution support.

This module provides parallel execution of multiple tool calls using threads,
as an alternative to sequential execution.
"""

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import get_config, set_config
from ..message import Message
from ..util.terminal import terminal_state_title

if TYPE_CHECKING:
    from ..logmanager import Log
    from . import ConfirmFunc, ToolUse

logger = logging.getLogger(__name__)


def is_parallel_enabled() -> bool:
    """Check if parallel tool execution is enabled.

    .. deprecated::
        Parallel execution is deprecated due to thread-safety issues with
        prompt_toolkit and global state. Use GPTME_BREAK_ON_TOOLUSE=0 for
        multi-tool mode which allows multiple tool calls per response but
        executes them sequentially.
    """
    import warnings

    config = get_config()
    enabled = bool(config.get_env_bool("GPTME_TOOLUSE_PARALLEL", False))
    if enabled:
        warnings.warn(
            "Parallel tool execution (GPTME_TOOLUSE_PARALLEL) is deprecated due to "
            "thread-safety issues and will be removed in a future version. "
            "Use GPTME_BREAK_ON_TOOLUSE=0 for multi-tool mode instead.",
            DeprecationWarning,
            stacklevel=2,
        )
    return enabled


def execute_tooluse_in_thread(
    tooluse: "ToolUse",
    confirm: "ConfirmFunc",
    log: "Log | None",
    workspace: Path | None,
) -> list[Message]:
    """Execute a single tool use in a thread-safe manner.

    Copies the current context to preserve ContextVars like config, loaded_tools, etc.
    """
    from . import init_tools

    # Initialize tools in this thread (they are stored in thread-local ContextVars)
    # This ensures each thread has its own tool instances
    init_tools(None)

    results: list[Message] = []
    try:
        with terminal_state_title(f"🛠️ running {tooluse.tool}"):
            for tool_response in tooluse.execute(confirm, log, workspace):
                results.append(tool_response.replace(call_id=tooluse.call_id))
    except Exception as e:
        logger.exception(f"Error executing tool {tooluse.tool}: {e}")
        results.append(
            Message(
                "system",
                f"Error executing {tooluse.tool}: {e}",
                call_id=tooluse.call_id,
            )
        )
    return results


def execute_tools_parallel(
    tooluses: "Sequence[ToolUse]",
    confirm: "ConfirmFunc",
    log: "Log | None",
    workspace: Path | None,
    max_workers: int | None = None,
) -> list[Message]:
    """Execute multiple tool uses in parallel.

    Args:
        tooluses: List of ToolUse objects to execute
        confirm: Confirmation function for tool execution
        log: Log manager instance
        workspace: Workspace path
        max_workers: Maximum number of parallel workers (default: min(4, len(tooluses)))

    Returns:
        List of Message results, maintaining original order
    """
    if not tooluses:
        return []

    # Default to 4 workers or the number of tools, whichever is smaller
    if max_workers is None:
        max_workers = min(4, len(tooluses))

    # Store the current config to pass to threads
    config = get_config()

    # Results will be collected in order
    results: list[list[Message]] = [[] for _ in tooluses]

    def run_tool(index: int, tooluse: "ToolUse") -> tuple[int, list[Message]]:
        """Run a single tool and return its index and results."""
        # Copy the current context to this thread
        ctx = copy_context()

        def execute_with_context() -> list[Message]:
            # Set config in this thread's context
            set_config(config)
            return execute_tooluse_in_thread(tooluse, confirm, log, workspace)

        # Run within the copied context
        return index, ctx.run(execute_with_context)

    logger.info(
        f"Executing {len(tooluses)} tools in parallel (max_workers={max_workers})"
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tool executions
        futures = {executor.submit(run_tool, i, tu): i for i, tu in enumerate(tooluses)}

        # Collect results as they complete
        for future in as_completed(futures):
            try:
                index, tool_results = future.result()
                results[index] = tool_results
            except Exception as e:
                index = futures[future]
                logger.exception(f"Future {index} raised exception: {e}")
                results[index] = [
                    Message(
                        "system",
                        f"Error in parallel execution: {e}",
                        call_id=tooluses[index].call_id,
                    )
                ]

    # Flatten results while maintaining order
    return [msg for result_list in results for msg in result_list]
