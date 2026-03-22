"""Subagent types, data classes, and module-level state.

Contains the core type definitions used across the subagent package:
- SubtaskDef: Planner mode subtask definition
- ReturnType: Result container for subagent execution
- Subagent: Running/completed subagent representation
- Module-level registries and locks
"""

import logging
import queue
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict

from ..base import ToolUse

if TYPE_CHECKING:
    from ...logmanager import LogManager  # fmt: skip

logger = logging.getLogger(__name__)

Status = Literal["running", "success", "failure"]


class SubtaskDef(TypedDict):
    """Definition of a subtask for planner mode."""

    id: str
    description: str


# ---------------------------------------------------------------------------
# Module-level registries (shared mutable state)
# ---------------------------------------------------------------------------

_subagents: list["Subagent"] = []

# Cache for subprocess results (keyed by agent_id)
# This allows Subagent to remain frozen while storing mutable result state
_subagent_results: dict[str, "ReturnType"] = {}
_subagent_results_lock = threading.Lock()

# Thread-safe queue for completed subagent notifications
# Each entry is (agent_id, status, summary)
_completion_queue: queue.Queue[tuple[str, Status, str]] = queue.Queue()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReturnType:
    status: Status
    result: str | None = None


@dataclass(frozen=True)
class Subagent:
    """Represents a running or completed subagent.

    Supports both thread-based (default) and subprocess-based execution modes.
    Subprocess mode provides better output isolation.

    Communication Model (Phase 1):
        - One-way: Parent sends prompt, child executes independently
        - No runtime updates from child to parent
        - Results retrieved after completion via status()/subagent_wait()

    Future (Phase 2/3):
        - Support for progress notifications from child → parent
        - Clarification requests when child encounters ambiguity
        - See module docstring for full design intent
    """

    agent_id: str
    prompt: str
    thread: threading.Thread | None
    logdir: Path
    model: str | None
    output_schema: type | None = None
    # Subprocess mode fields
    process: subprocess.Popen | None = None
    execution_mode: Literal["thread", "subprocess", "acp"] = "thread"
    # ACP mode fields
    acp_command: str | None = None
    # Worktree isolation fields
    isolated: bool = False
    worktree_path: Path | None = None
    repo_path: Path | None = None

    def get_log(self) -> "LogManager":
        # noreorder
        from ...logmanager import LogManager  # fmt: skip

        return LogManager.load(self.logdir)

    def is_running(self) -> bool:
        """Check if the subagent is still running."""
        if self.execution_mode == "subprocess" and self.process:
            return self.process.poll() is None
        if self.execution_mode == "acp" and self.thread:
            # ACP mode uses a thread wrapping the async client
            return self.thread.is_alive()
        if self.thread:
            return self.thread.is_alive()
        return False

    def status(self) -> ReturnType:
        # Return cached result if available (subprocess mode)
        with _subagent_results_lock:
            if self.agent_id in _subagent_results:
                return _subagent_results[self.agent_id]

        if self.is_running():
            return ReturnType("running")

        # Check if executor used the complete tool
        log = self.get_log().log
        if not log:
            return ReturnType("failure", "No messages in log")

        last_msg = log[-1]

        # Check for complete tool call in last message
        # Try parsing as ToolUse first
        tool_uses = list(ToolUse.iter_from_content(last_msg.content))
        complete_tool = next((tu for tu in tool_uses if tu.tool == "complete"), None)

        if complete_tool:
            # Extract content from complete tool
            # Don't silently fall back - make it clear when no summary was provided
            if complete_tool.content and complete_tool.content.strip():
                result = complete_tool.content.strip()
            else:
                result = "Task completed (no summary provided)"
            return ReturnType(
                "success",
                result + f"\n\nFull log: {self.logdir}",
            )

        # Fallback: Check for complete code block directly
        if "```complete" in last_msg.content:
            # Extract content between ```complete and ```
            match = re.search(
                r"```complete\s*\n(.*?)\n```", last_msg.content, re.DOTALL
            )
            if match:
                content = match.group(1).strip()
                if content:
                    result = content
                else:
                    result = "Task completed (no summary provided)"
                return ReturnType(
                    "success",
                    result + f"\n\nFull log: {self.logdir}",
                )

        # Check if session ended with system completion message
        if last_msg.role == "system" and "Task complete" in last_msg.content:
            return ReturnType(
                "success",
                f"Task completed successfully. Full log: {self.logdir}",
            )

        # Task didn't complete properly
        return ReturnType(
            "failure",
            f"Task did not complete properly. Check log: {self.logdir}",
        )
