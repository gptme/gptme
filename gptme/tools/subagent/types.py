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
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:
    from typing_extensions import NotRequired

from ..base import ToolUse

if TYPE_CHECKING:
    from ...logmanager import LogManager  # fmt: skip

logger = logging.getLogger(__name__)

Status = Literal["running", "success", "failure", "clarification_needed", "timeout"]
Role = Literal["general", "explore", "implement", "verify"]

# Role → profile name mapping
_ROLE_PROFILES: dict[Role, str] = {
    "general": "default",
    "explore": "explorer",
    "implement": "developer",
    "verify": "verifier",
}


def resolve_role_defaults(
    role: Role | None,
    explicit_use_subprocess: bool | None = None,
    explicit_isolated: bool | None = None,
) -> tuple[bool, bool, str | None]:
    """Resolve profile and defaults from a role.

    Args:
        role: The role to resolve, or None.
        explicit_use_subprocess: Caller's use_subprocess setting.
            None means "not set — use role default or False".
            True/False means "explicitly set — override role default".
        explicit_isolated: Caller's isolated setting.
            None means "not set — use role default or False".
            True/False means "explicitly set — override role default".

    Returns:
        Tuple of (effective_use_subprocess, effective_isolated, effective_profile).

    Precedence: explicit args > role defaults > base defaults.
    """
    if role is None:
        return bool(explicit_use_subprocess), bool(explicit_isolated), None

    profile = _ROLE_PROFILES.get(role)

    # Role defaults
    subprocess_default = False
    isolated_default = False
    if role == "verify":
        subprocess_default = True
        isolated_default = True

    # Explicit True/False overrides role defaults; None falls through to role default.
    use_sub = (
        explicit_use_subprocess
        if explicit_use_subprocess is not None
        else subprocess_default
    )
    use_iso = explicit_isolated if explicit_isolated is not None else isolated_default

    return use_sub, use_iso, profile


class SubtaskDef(TypedDict):
    """Definition of a subtask for planner mode."""

    id: str
    description: str
    # role sets both the executor profile AND execution mode defaults:
    # - "verify" → verifier profile + subprocess + isolated (sandboxed validation)
    # - "explore" → explorer profile + thread mode (read-only analysis)
    # - "implement" → developer profile + thread mode (full capability)
    role: NotRequired[Role]


# ---------------------------------------------------------------------------
# Module-level registries (shared mutable state)
# ---------------------------------------------------------------------------

_subagents: list["Subagent"] = []
_subagents_lock = threading.Lock()

# Cache for subprocess results (keyed by agent_id)
# This allows Subagent to remain frozen while storing mutable result state
_subagent_results: dict[str, "ReturnType"] = {}
_subagent_results_lock = threading.Lock()

# Thread-safe queue for completed subagent notifications
# Each entry is (agent_id, status, summary)
_completion_queue: queue.Queue[tuple[str, Status, str]] = queue.Queue()

# Thread-safe queue for intermediate progress notifications from subagents
# Each entry is (agent_id, message) — delivered as ⏳ system messages via LOOP_CONTINUE
# Only populated in thread-mode subagents (subprocess mode cannot share in-process queues)
_progress_queue: queue.Queue[tuple[str, str]] = queue.Queue()


def set_subagent_result_if_absent(agent_id: str, result: "ReturnType") -> bool:
    """Cache a subagent result unless another terminal result already exists."""
    with _subagent_results_lock:
        if agent_id in _subagent_results:
            return False
        _subagent_results[agent_id] = result
        return True


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReturnType:
    status: Status
    result: str | None = None


def clarification_result_from_content(content: str) -> ReturnType | None:
    """Return a clarification result if content contains a clarify block."""
    if "```clarify" not in content:
        return None

    match = re.search(r"```clarify\s*\n(.*?)\n```", content, re.DOTALL)
    if not match:
        return None

    question = match.group(1).strip()
    return ReturnType(
        "clarification_needed",
        question or "Subagent needs clarification (no question provided)",
    )


@dataclass(frozen=True)
class Subagent:
    """Represents a running or completed subagent.

    Supports both thread-based (default) and subprocess-based execution modes.
    Subprocess mode provides better output isolation.

    Communication Model:
        - Parent sends prompt, child executes independently
        - Results retrieved after completion via status()/subagent_wait()
        - Subagents can use the ``clarify`` code block to signal ambiguity;
          the parent receives a hook notification and can call subagent_reply()
          to re-spawn with the question answered.
    """

    agent_id: str
    prompt: str
    thread: threading.Thread | None
    logdir: Path
    model: str | None
    context_mode: Literal["full", "selective"] = "full"
    context_include: list[str] | None = None
    profile: str | None = None
    output_schema: type | None = None
    use_acp: bool = False
    # Subprocess mode fields
    process: subprocess.Popen | None = None
    execution_mode: Literal["thread", "subprocess", "acp"] = "thread"
    # ACP mode fields
    acp_command: str | None = None
    # Worktree isolation fields
    isolated: bool = False
    worktree_path: Path | None = None
    repo_path: Path | None = None
    # Maximum time (seconds) the subprocess monitor will wait before killing
    timeout: int = 1800  # 30 minutes
    role: Role | None = None
    # Secret redaction: when True, common secret patterns (API keys, tokens, passwords)
    # are redacted from workspace context messages before they are seen by the subagent.
    redact_secrets: bool = True
    # Context window: None = no limit; 0 = minimal (no workspace files); N = at most N msgs
    context_window: int | None = None
    # Timestamp (seconds since epoch) when this subagent was created
    started_at: float = field(default_factory=time.time)
    # Wall-clock limit in seconds; when set, a watchdog auto-cancels after this time
    max_time: float | None = None

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
        if self.is_running():
            return ReturnType("running")
        return self._read_log()

    def _read_log(self) -> ReturnType:
        """Read result from cache or conversation log, bypassing thread-liveness check.

        Safe to call from within a completion handler (e.g. the run_subagent thread),
        where status() would incorrectly return "running" because the thread is alive.
        """
        # Return cached result if available (set by cancel or subprocess completion)
        with _subagent_results_lock:
            if self.agent_id in _subagent_results:
                return _subagent_results[self.agent_id]

        # Check if executor used the complete tool
        try:
            log = self.get_log().log
        except FileNotFoundError:
            return ReturnType(
                "failure",
                f"Subagent exited before creating a conversation log: {self.logdir}",
            )
        if not log:
            return ReturnType("failure", "No messages in log")

        last_msg = log[-1]

        # Check for clarify code block — subagent is asking the parent for more info.
        # Must be checked before the complete block so a "clarify" isn't misread as failure.
        clarification_result = clarification_result_from_content(last_msg.content)
        if clarification_result:
            return clarification_result

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
