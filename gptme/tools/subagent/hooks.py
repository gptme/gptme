"""Subagent hook system — completion, progress, and session-end cleanup.

Handles the "fire-and-forget-then-get-alerted" pattern where subagent
completions and intermediate progress updates are delivered via the
LOOP_CONTINUE hook as system messages.

Also handles SESSION_END teardown: cancels orphaned subagents when the
parent conversation closes (server or CLI), preventing leaked subprocess
children and zombie threads with dangling concurrency slots.
"""

import logging
import queue
from collections.abc import Generator
from typing import TYPE_CHECKING

from ...hooks.types import StopPropagation
from ...message import Message
from ...prompt_queue import QUEUE_FILENAME, drain_steer_prompts
from .control import CONTROL_FILENAME, drain_control_ops
from .types import (
    ReturnType,
    Status,
    _completion_queue,
    _progress_queue,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
    set_subagent_result_if_absent,
)

if TYPE_CHECKING:
    from ...logmanager import LogManager  # fmt: skip

logger = logging.getLogger(__name__)


def _subagent_control_hook(manager: "LogManager") -> Generator[Message, None, None]:
    """Apply cancel and steer operations at each STEP_PRE boundary.

    Cancel: reads ``logdir/control.jsonl`` for a ``{"op": "cancel"}`` record
    written by ``subagent_cancel()``.  On cancel, appends a note, marks the
    result as *cancelled* (first-writer-wins), and raises
    ``SessionCompleteException`` so ``chat()`` exits cleanly.

    Steer: drains steer-flagged messages from the prompt queue (written by
    ``subagent_steer()`` with ``steer=True``) and yields them as user messages
    so the very next LLM call in the same turn sees the orchestrator's guidance
    without waiting for the current agentic turn to finish.

    Fast paths (one ``stat()`` per step each) keep overhead negligible for
    normal conversations that have neither a control file nor a steer queue.
    """
    # Search by logdir, not logdir.name: thread subagent logdirs have a random suffix
    # (subagent-{agent_id}-{suffix}), so logdir.name != agent_id for thread mode.
    with _subagents_lock:
        sa = next((s for s in _subagents if s.logdir == manager.logdir), None)
    agent_id = sa.agent_id if sa is not None else manager.logdir.name

    # ── Cancel check ─────────────────────────────────────────────────────────
    # Check in-memory fallback first: set by subagent_cancel() when the
    # control-file write fails with OSError.
    in_memory_cancel = sa is not None and sa.cancel_event.is_set()

    has_cancel = in_memory_cancel
    if not in_memory_cancel and (manager.logdir / CONTROL_FILENAME).exists():
        try:
            operations = drain_control_ops(manager.logdir)
        except OSError:
            # Control file existed but became unreadable or could not be removed.
            # Treat as a cancel: a cancel op may have been written and must not be lost.
            logger.warning(
                "Could not drain control ops from %s; treating as cancel",
                manager.logdir,
            )
            operations = [{"op": "cancel", "agent_id": agent_id}]

        if any(operation.get("op") == "cancel" for operation in operations):
            has_cancel = True
            for operation in operations:
                candidate_agent_id = operation.get("agent_id")
                if operation.get("op") == "cancel" and isinstance(
                    candidate_agent_id, str
                ):
                    agent_id = candidate_agent_id
                    break

    if has_cancel:
        manager.append(
            Message(
                "system",
                "Subagent cancellation requested by orchestrator; ending at this step boundary.",
            )
        )
        set_subagent_result_if_absent(
            agent_id, ReturnType("cancelled", "Cancelled by orchestrator")
        )
        from ..complete import SessionCompleteException

        raise SessionCompleteException("Subagent cancelled by orchestrator")

    # ── Steer check ───────────────────────────────────────────────────────────
    # Drain steer-flagged messages mid-turn so the next LLM step sees them
    # immediately, without waiting for the agentic turn to finish naturally.
    # Regular (non-steer) queued prompts remain on disk for between-turn draining.
    if (manager.logdir / QUEUE_FILENAME).exists():
        for msg in drain_steer_prompts(manager.logdir):
            logger.debug(
                "Mid-turn steer injection for %s: %r", agent_id, msg.content[:80]
            )
            yield msg


# Python built-in types that are valid as values in a {field_name: python_type} mapping.
# Any dict whose values are all in this set is treated as a field-type map and converted
# to JSON Schema.  Any other dict (including all real JSON Schema dicts) is returned as-is.
_PYTHON_FIELD_TYPES: frozenset[type] = frozenset(
    {int, float, bool, str, bytes, list, dict, tuple, set}
)


def _dict_to_jsonschema(d: dict) -> dict:
    """Convert a simple ``{field: type}`` dict to a JSON Schema object.

    Handles two cases:
    - Plain Python ``{str: type}`` mapping (all values are Python type objects) →
      converted to an ``object`` schema with ``properties`` derived from the Python
      type names (``int`` → ``"integer"``, ``float`` → ``"number"``,
      ``bool`` → ``"boolean"``, everything else → ``"string"``).
    - Anything else (empty dict, or any value that is not a Python type object) →
      returned as-is.  This covers all valid JSON Schema dicts regardless of which
      keywords they use (``type``, ``properties``, ``items``, ``minimum``,
      ``format``, ``$ref``, etc.).

    Args:
        d: A dict that is either an existing JSON Schema or a ``{field: type}`` mapping.

    Returns:
        A JSON Schema dict.
    """
    # Pass through empty dicts and any dict that is not a pure {field: PythonType} map.
    # JSON Schema dicts always have at least one value that is not a Python type object
    # (e.g. string literals like "string"/"object", nested dicts, lists, numbers, …).
    # `isinstance(v, type)` short-circuits before the `in` check, which avoids
    # a TypeError on unhashable values (dicts, lists, etc.) from real JSON Schema dicts.
    if not d or not all(
        isinstance(v, type) and v in _PYTHON_FIELD_TYPES for v in d.values()
    ):
        return d

    type_map = {int: "integer", float: "number", bool: "boolean"}
    props = {}
    for key, val in d.items():
        json_type = type_map.get(val, "string")
        props[key] = {"type": json_type}
    return {
        "type": "object",
        "properties": props,
        "required": list(d.keys()),
    }


def _get_complete_instruction(
    target: str = "orchestrator",
    *,
    supports_progress: bool = True,
    output_schema: "type | dict | None" = None,
) -> str:
    """Get the standard instruction for using the complete tool.

    Used by both thread and subprocess modes to ensure consistent behavior.
    The instruction is intentionally minimal - profile system prompts and
    task context should guide what the complete answer should contain.

    Args:
        target: Who will review the result ("orchestrator", "parent", "planner")
        supports_progress: Whether to include the progress block instructions
        output_schema: Optional schema for the complete block. Accepted forms:
            - Pydantic model class: ``model.model_json_schema()`` is used.
            - Plain ``{field: type}`` dict: converted to a JSON Schema object.
            - Raw JSON Schema dict (has ``"type"``/``"properties"``): used as-is.
            When set, the instruction is extended with the expected schema.
    """
    if output_schema is not None:
        import json

        if hasattr(output_schema, "model_json_schema"):
            schema = output_schema.model_json_schema()
        elif isinstance(output_schema, dict):
            schema = _dict_to_jsonschema(output_schema)
        else:
            schema = {"type": "object"}
        schema_str = json.dumps(schema, indent=2)
        complete_block_hint = f"Valid JSON matching this schema:\n{schema_str}"
    else:
        complete_block_hint = "Your complete answer here."

    instruction = (
        "When finished, use the `complete` tool with your full answer/result.\n"
        f"Include everything the {target} needs - they shouldn't need to read the full log.\n"
        "```complete\n"
        f"{complete_block_hint}\n"
        "```\n"
        f"If you cannot proceed without more information from the {target}, use the `clarify` block instead:\n"
        "```clarify\n"
        "Your specific question here.\n"
        "```"
    )
    if output_schema is not None:
        instruction += (
            "\n"
            "IMPORTANT: Your `complete` block MUST contain valid JSON matching the schema above. "
            "Do not include any text outside the JSON object."
        )
    if supports_progress:
        instruction += (
            "\n"
            f"To send an intermediate progress update to the {target} (without stopping), use the `progress` block:\n"
            "```progress\n"
            "Brief status update: what you have done so far and what remains.\n"
            "```"
        )
    return instruction


def notify_completion(agent_id: str, status: Status, summary: str) -> None:
    """Add a subagent completion to the notification queue.

    Called by the monitor thread when a subagent finishes. The queued
    notification will be delivered via the subagent_completion hook
    during the next LOOP_CONTINUE cycle.

    Args:
        agent_id: The subagent's identifier
        status: "success" or "failure"
        summary: Brief summary of the result
    """
    _completion_queue.put((agent_id, status, summary))
    logger.debug(f"Queued completion notification for subagent '{agent_id}': {status}")


def _session_end_subagent_cleanup(
    manager: "LogManager",
    **kwargs,
) -> Generator[Message | StopPropagation, None, None]:
    """Cancel orphaned subagents when the parent session ends.

    Registered as a SESSION_END hook via ToolSpec.hooks so it is only
    loaded when the subagent tool is active.  Snapshots the running
    subagent list under the module lock and cancels each still-running
    subagent that has no terminal cached result.

    Only cancels subagents whose ``parent_logdir`` matches the ending
    session's logdir, preventing cross-conversation interference in
    multi-session server deployments.  Subagents with no ``parent_logdir``
    (spawned outside a tracked session) are skipped conservatively.

    Subprocess-mode subagents receive SIGTERM → SIGKILL after 5s (handled
    by ``subagent_cancel`` internally).  Thread-mode subagents have their
    result pre-marked as cancelled so the orchestrator will not block
    waiting for them; the underlying Python thread is not forcibly stopped
    (Python does not support forcible thread termination) — it continues
    until its next natural checkpoint checks the cached result or the
    process exits.

    Bounded: the per-agent cancel wait is capped at ~5s (subprocess
    SIGKILL escalation); the overall hook never blocks indefinitely.
    """
    from .api import subagent_cancel  # avoid circular import

    session_logdir = manager.logdir

    with _subagents_lock:
        snapshot = list(_subagents)

    for sa in snapshot:
        # Only cancel subagents that belong to this session.
        # Skipping unknown-owner subagents (parent_logdir=None) is the safe
        # default: we can't prove ownership, so we don't risk cross-session kills.
        if sa.parent_logdir is None or sa.parent_logdir != session_logdir:
            continue

        with _subagent_results_lock:
            has_terminal = sa.agent_id in _subagent_results

        if has_terminal:
            continue
        if not sa.is_running():
            continue

        try:
            subagent_cancel(sa.agent_id)
        except Exception as e:
            logger.warning(
                "SESSION_END: error cancelling subagent '%s': %s", sa.agent_id, e
            )

    yield from ()


def notify_progress(agent_id: str, message: str) -> None:
    """Add a subagent progress update to the notification queue.

    Called by the progress tool when a subagent sends an intermediate update.
    The parent's LOOP_CONTINUE hook delivers it as a system message so the
    orchestrator can react without blocking on subagent_wait().

    Note: For thread-mode subagents the progress tool calls this directly (same
    process). For subprocess-mode subagents, ``_poll_subprocess_progress`` reads
    from the file channel and calls this function on behalf of the child process.

    Args:
        agent_id: The subagent's identifier
        message: Progress update message
    """
    _progress_queue.put((agent_id, message))
    logger.debug(f"Queued progress notification for subagent '{agent_id}'")


def _subagent_completion_hook(
    manager: "LogManager",
    interactive: bool,
    prompt_queue: object,
    no_confirm: bool = False,
) -> Generator[Message, None, None]:
    """Check for completed subagents and yield notification messages.

    This hook is triggered during each chat loop iteration via LOOP_CONTINUE.
    It checks the completion queue and yields system messages for any
    finished subagents, allowing the orchestrator to react naturally.

    Also drains the progress queue and delivers intermediate updates as
    ⏳ system messages.
    """

    # Drain progress notifications first (in-flight updates before completions)
    progress_updates: list[tuple[str, str]] = []
    while True:
        try:
            agent_id, message = _progress_queue.get_nowait()
            progress_updates.append((agent_id, message))
        except queue.Empty:
            break

    for agent_id, message in progress_updates:
        msg = f"⏳ Subagent '{agent_id}' progress: {message}"
        logger.debug(f"Delivering subagent progress notification: {msg}")
        yield Message("system", msg)

    # Drain completion notifications
    notifications: list[tuple[str, Status, str]] = []
    while True:
        try:
            agent_id, status, summary = _completion_queue.get_nowait()
            notifications.append((agent_id, status, summary))
        except queue.Empty:
            break

    # Yield messages for each completion
    for agent_id, status, summary in notifications:
        if status == "success":
            msg = f"✅ Subagent '{agent_id}' completed: {summary}"
        elif status == "clarification_needed":
            msg = (
                f"❓ Subagent '{agent_id}' needs clarification: {summary}\n"
                f"Call subagent_reply('{agent_id}', '<your answer>') to continue."
            )
        elif status == "timeout":
            msg = f"⏱️ Subagent '{agent_id}' timed out: {summary}"
        else:
            msg = f"❌ Subagent '{agent_id}' failed: {summary}"

        logger.debug(f"Delivering subagent notification: {msg}")
        yield Message("system", msg)


def _subagent_cancel_checkpoint(
    manager: "LogManager",
) -> Generator[Message, None, None]:
    """STEP_PRE cooperative cancel checkpoint for thread-mode subagents.

    Registered at STEP_PRE so it fires before each LLM generation step.

    Fast-path design: two O(1) guards keep overhead minimal for the common case
    (no cancel, not inside a subagent thread):
      1. Thread-local check — no-op when called in the parent's loop.
      2. One stat() per step — no-op when logdir/control.jsonl doesn't exist.

    On cancel op: appends a partial-work note to the conversation, writes
    ``cancelled`` status to the result cache (first-writer-wins), then raises
    ``SessionCompleteException`` so ``chat()`` exits cleanly and the thread's
    semaphore slot is released.
    """
    import json

    # Fast path: hook fires in every loop, including the parent's own loop in
    # thread mode.  Only subagent threads have agent_id set in thread-local.
    from .execution import get_current_agent_id  # avoid circular at module level

    agent_id = get_current_agent_id()
    if agent_id is None:
        return

    # Fast path: one stat() per step — no allocation when no control file.
    control_file = manager.logdir / "control.jsonl"
    if not control_file.exists():
        return

    try:
        content = control_file.read_text()
    except OSError:
        return

    for line in content.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("op") == "cancel":
            # Import here to avoid circular at module level (complete → hooks is safe,
            # but execution → hooks already creates a cycle if we put it at top).
            from ..complete import SessionCompleteException

            yield Message(
                "system",
                f"Subagent '{agent_id}' received cancel signal — stopping at cooperative checkpoint.",
            )
            # First-writer-wins: keeps 'success' if agent already completed naturally.
            cancelled_result = ReturnType(
                "cancelled", "Cancelled by orchestrator via checkpoint"
            )
            set_subagent_result_if_absent(agent_id, cancelled_result)
            raise SessionCompleteException("Cancelled at cooperative checkpoint")
