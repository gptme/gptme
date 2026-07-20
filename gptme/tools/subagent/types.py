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

Status = Literal[
    "running",
    "success",
    "failure",
    "clarification_needed",
    "timeout",
    "budget_exceeded",
    "cancelled",
]
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


def update_subagent_result_with_branch(
    agent_id: str, branch: str, has_output_schema: bool = False
) -> None:
    """Amend a cached result to include a preserved branch name.

    Called when the normal-completion thread loses the set_subagent_result_if_absent
    race to a timeout/cancel watchdog. Cleanup already ran and found a preserved
    branch; without this patch the caller's stored result would have no branch name.

    When ``has_output_schema`` is True, the cached result string is JSON that
    callers parse — appending human-readable text would break that parse, so
    the branch is logged only (via ``_cleanup_isolation``) and not patched in.
    """
    if has_output_schema:
        return
    suffix = (
        f"\n\nChanges preserved on branch {branch!r} — merge with: git merge {branch}"
    )
    with _subagent_results_lock:
        existing = _subagent_results.get(agent_id)
        if existing is None:
            return
        if isinstance(existing.result, str):
            amended: str | None = existing.result + suffix
        elif existing.result is None:
            amended = suffix.lstrip("\n")
        else:
            return  # structured (dict) result — cannot amend inline
        _subagent_results[agent_id] = ReturnType(
            existing.status,
            amended,
            input_tokens=existing.input_tokens,
            output_tokens=existing.output_tokens,
        )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReturnType:
    status: Status
    result: str | dict[str, object] | None = None
    # Token budget tracking: LLM token counts for the subagent's run.
    # None means unavailable (e.g. cached terminal result, pre-budget-tracking log).
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class SubagentBudget:
    """Fleet-wide output-token budget tracker. Thread-safe.

    Pass a shared instance to ``subagent_parallel()`` or ``subagent_pipeline()``
    to gate new agent spawns when the budget is exhausted. Agents that are already
    running when the budget hits zero are allowed to complete normally — only new
    spawns are blocked.

    Tracks output tokens only (the expensive marginal cost), matching the
    Claude Code Workflow ``budget.spent()`` semantics.

    Example::

        from gptme.tools.subagent import subagent_parallel, SubagentBudget

        budget = SubagentBudget(total=200_000)   # 200k output tokens
        results = subagent_parallel(tasks, budget=budget)
        # Items spawned after the budget was exhausted have status="budget_exceeded"

    Dynamic loop pattern (accumulate until budget runs out)::

        budget = SubagentBudget(total=500_000)
        findings = []
        while not budget.exhausted():
            batch_results = subagent_parallel(next_batch, budget=budget)
            findings.extend(r["result"] for r in batch_results if r["status"] == "success")
    """

    total: int | None = None  # None = unlimited
    _spent: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def record(self, output_tokens: int) -> None:
        """Add output_tokens to the spent counter."""
        with self._lock:
            self._spent += output_tokens

    def spent(self) -> int:
        """Return total output tokens spent so far."""
        with self._lock:
            return self._spent

    def remaining(self) -> float:
        """Return remaining token budget, or ``float('inf')`` when total is None."""
        if self.total is None:
            return float("inf")
        return max(0, self.total - self.spent())

    def exhausted(self) -> bool:
        """Return True when a finite budget has been fully consumed."""
        return self.total is not None and self.remaining() <= 0


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
    output_schema: "type | dict | None" = None
    use_acp: bool = False
    # Subprocess mode fields
    process: subprocess.Popen | None = None
    execution_mode: Literal["thread", "subprocess", "acp"] = "thread"
    # ACP mode fields
    acp_command: str | None = None
    # Working directory: the resolved path passed via workdir=; None means cwd at spawn time.
    # Stored so subagent_reply() can re-spawn in the same directory.
    workdir: Path | None = None
    # Worktree isolation fields
    isolated: bool = False
    worktree_path: Path | None = None
    repo_path: Path | None = None
    # String isolation mode ("worktree" or None). When set, cleanup uses smart
    # behaviour: auto-remove if unchanged, preserve branch if changes exist.
    isolation_mode: str | None = None
    # Maximum time (seconds) the subprocess monitor will wait before killing
    timeout: int = 1800  # 30 minutes
    role: Role | None = None
    # Secret redaction: when True, common secret patterns (API keys, tokens, passwords)
    # are redacted from workspace context messages before they are seen by the subagent.
    redact_secrets: bool = True
    # Context window: None = no limit; 0 = minimal (no workspace files); N = at most N msgs
    context_window: int | None = None
    # Number of parent conversation turns to forward as context (P1: persist for re-spawn)
    context_turns: int | None = None
    # Timestamp (seconds since epoch) when this subagent was created
    started_at: float = field(default_factory=time.time)
    # Wall-clock limit in seconds; when set, a watchdog auto-cancels after this time
    max_time: float | None = None
    # Logdir of the parent session that spawned this subagent.
    # Used by SESSION_END cleanup to scope cancellation to the correct session and
    # prevent cross-conversation interference in multi-session server deployments.
    parent_logdir: Path | None = field(default=None)
    # In-memory fallback cancellation signal for thread mode.
    # Set by subagent_cancel() when the control-file write fails (OSError), so the
    # STEP_PRE checkpoint hook can still stop the thread without the file.
    cancel_event: threading.Event = field(
        default_factory=threading.Event, init=False, repr=False
    )
    # Set when chat() has returned and the prompt queue will no longer be drained.
    # Provides an earlier, more reliable signal than _subagent_results caching
    # (which requires a disk read via _read_log() before the result is stored).
    # subagent_steer() checks this flag to catch the cleanup window where
    # thread.is_alive() is True but the chat loop has already stopped.
    prompt_queue_closed: threading.Event = field(default_factory=threading.Event)

    def _normalize_json_result(self, result: str) -> str:
        """Normalize a complete-block result as canonical JSON.

        When output_schema is set, the complete block content must be valid JSON.
        If it parses as valid JSON, the result is returned as a canonical JSON string
        (re-serialized for consistency). If it fails to parse, the original string
        is returned unchanged so the caller still gets a result (parsing failure is
        logged as a warning, not treated as a hard failure).

        Note: this validates JSON *syntax*, not Pydantic schema conformance —
        callers that need full schema validation should call
        ``output_schema.model_validate(parsed)`` separately.

        Args:
            result: The raw text from the complete block.

        Returns:
            Canonical JSON string when output_schema is set and the result is valid
            JSON; otherwise the original result string unchanged.
        """
        if self.output_schema is None:
            return result
        import json

        try:
            parsed = json.loads(result)
            return json.dumps(parsed)
        except json.JSONDecodeError as e:
            logger.warning(
                "Subagent '%s' output_schema set but complete block is not valid JSON: %s. "
                "Returning raw result. Snippet: %.200r",
                self.agent_id,
                e,
                result,
            )
            return result

    def get_log(self) -> "LogManager":
        # noreorder
        from ...logmanager import LogManager  # fmt: skip

        return LogManager.load(self.logdir)

    def _read_token_stats(self) -> tuple[int | None, int | None]:
        """Return (input_tokens, output_tokens) from the subagent's conversation log.

        Reads the JSONL log file directly to sum up usage metadata across all
        assistant turns — same approach as logmanager/conversations.py _full_scan().
        Returns (None, None) on any error so missing stats never block the caller.
        """
        import json

        try:
            conv_fn = self.logdir / "conversation.jsonl"
            if not conv_fn.exists():
                # Some log layouts use the directory name as the file
                candidates = list(self.logdir.glob("*.jsonl"))
                if not candidates:
                    return None, None
                conv_fn = candidates[0]

            input_tokens = 0
            output_tokens = 0
            saw_usage = False
            with open(conv_fn, "rb") as f:
                for line in f:
                    line = line.strip()
                    if not line or b'"metadata"' not in line:
                        continue
                    try:
                        msg = json.loads(line)
                        meta = msg.get("metadata")
                        if not meta:
                            continue
                        usage = meta.get("usage")
                        src = usage if isinstance(usage, dict) else meta
                        token_keys = (
                            "input_tokens",
                            "output_tokens",
                            "cache_read_tokens",
                            "cache_creation_tokens",
                        )
                        if not any(k in src for k in token_keys):
                            continue
                        saw_usage = True
                        cache_read = src.get("cache_read_tokens", 0) or 0
                        input_tokens += (
                            (src.get("input_tokens", 0) or 0)
                            + cache_read
                            + (src.get("cache_creation_tokens", 0) or 0)
                        )
                        output_tokens += src.get("output_tokens", 0) or 0
                    except (json.JSONDecodeError, TypeError):
                        continue
            if not saw_usage:
                return None, None
            return input_tokens, output_tokens
        except Exception as e:
            logger.debug("Could not read token stats for %s: %s", self.agent_id, e)
            return None, None

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
        with _subagent_results_lock:
            cached_result = _subagent_results.get(self.agent_id)
        if cached_result is not None:
            return cached_result
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

        # Read token stats once; attach to every terminal ReturnType we return.
        in_tok, out_tok = self._read_token_stats()

        # Check if executor used the complete tool
        try:
            log = self.get_log().log
        except FileNotFoundError:
            return ReturnType(
                "failure",
                f"Subagent exited before creating a conversation log: {self.logdir}",
                input_tokens=in_tok,
                output_tokens=out_tok,
            )
        if not log:
            return ReturnType(
                "failure",
                "No messages in log",
                input_tokens=in_tok,
                output_tokens=out_tok,
            )

        last_msg = log[-1]

        # Check for clarify code block — subagent is asking the parent for more info.
        # Must be checked before the complete block so a "clarify" isn't misread as failure.
        clarification_result = clarification_result_from_content(last_msg.content)
        if clarification_result:
            # Attach token stats to clarification result too
            return ReturnType(
                clarification_result.status,
                clarification_result.result,
                input_tokens=in_tok,
                output_tokens=out_tok,
            )

        # Check for complete tool call in last message
        # Try parsing as ToolUse first
        tool_uses = list(ToolUse.iter_from_content(last_msg.content))
        complete_tool = next((tu for tu in tool_uses if tu.tool == "complete"), None)

        if complete_tool:
            # Extract content from complete tool
            # Don't silently fall back - make it clear when no summary was provided
            if complete_tool.content and complete_tool.content.strip():
                result = complete_tool.content.strip()
                result = self._normalize_json_result(result)
            else:
                result = "Task completed (no summary provided)"
            return ReturnType(
                "success",
                result + f"\n\nFull log: {self.logdir}",
                input_tokens=in_tok,
                output_tokens=out_tok,
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
                    result = self._normalize_json_result(result)
                else:
                    result = "Task completed (no summary provided)"
                return ReturnType(
                    "success",
                    result + f"\n\nFull log: {self.logdir}",
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                )

        # Check if session ended with system completion message
        if last_msg.role == "system" and "Task complete" in last_msg.content:
            return ReturnType(
                "success",
                f"Task completed successfully. Full log: {self.logdir}",
                input_tokens=in_tok,
                output_tokens=out_tok,
            )

        # Task didn't complete properly
        return ReturnType(
            "failure",
            f"Task did not complete properly. Check log: {self.logdir}",
            input_tokens=in_tok,
            output_tokens=out_tok,
        )
