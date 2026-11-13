"""Progress tool — subagents use this to send intermediate updates to the parent.

Registers the ``progress`` block type so subagents can report status mid-task
without stopping their session. The parent orchestrator receives these updates
via the LOOP_CONTINUE hook as ⏳ system messages, enabling the "fire-and-forget-
then-get-alerted-when-update/done" pattern alongside ``complete`` and ``clarify``.

Unlike ``complete`` (ends session) and ``clarify`` (pauses session), ``progress``
continues execution after delivering the update.

Delivery modes:

- Thread-mode subagents: uses the in-process ``_progress_queue``; the parent's
  LOOP_CONTINUE hook picks it up on the next iteration.
- Subprocess-mode subagents: writes JSON lines to ``GPTME_PROGRESS_FILE`` (set
  by the parent before spawning); the parent's monitor thread polls that file and
  delivers updates via the same hook path.

Only enabled in autonomous/subagent sessions (disabled_by_default=True).
"""

import json
import logging
import os
from collections.abc import Generator
from typing import TYPE_CHECKING, Literal

from ..message import Message
from .base import Parameter, ToolSpec

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Env var the parent sets to tell a subprocess-mode subagent where to write
# progress updates. Value is a path to a JSONL file; each line is a JSON object
# with "agent_id" and "message" keys.
_PROGRESS_FILE_ENVVAR = "GPTME_PROGRESS_FILE"
# Env var conveying the subagent's own identity to subprocess-mode children.
_AGENT_ID_ENVVAR = "GPTME_SUBAGENT_AGENT_ID"


def _write_progress_to_file(agent_id: str, message: str, progress_file: str) -> None:
    """Write a progress update to the file-based channel for subprocess mode."""
    entry = json.dumps({"agent_id": agent_id, "message": message})
    with open(progress_file, "a") as f:
        f.write(entry + "\n")


def send_progress_to_parent(message: str) -> Literal["queue", "file"] | None:
    """Deliver a progress update to the parent orchestrator.

    Returns the channel used, or ``None`` when the update was not delivered.

    Routing is decided by who actually owns the parent, not merely by whether
    this process has a subagent identity:

    - A thread-mode subagent (thread-local agent_id) shares a process with its
      parent, so it uses the in-process ``_progress_queue``.
    - Otherwise, a managed subprocess child (``GPTME_SUBAGENT_AGENT_ID`` *and*
      ``GPTME_PROGRESS_FILE`` set) appends to the file channel the parent polls.
      Its in-memory queue is private to the child and would never reach the
      parent (#4910).
    """
    from .subagent.execution import get_thread_agent_id
    from .subagent.hooks import notify_progress

    agent_id = get_thread_agent_id()
    if agent_id is not None:
        notify_progress(agent_id, message)
        logger.debug(
            f"Progress update queued for subagent '{agent_id}': {message[:80]}"
        )
        return "queue"

    agent_id = os.environ.get(_AGENT_ID_ENVVAR)
    progress_file = os.environ.get(_PROGRESS_FILE_ENVVAR)
    if not (agent_id and progress_file):
        logger.warning(
            "progress: no thread-mode subagent and no subprocess file channel "
            f"({_AGENT_ID_ENVVAR}/{_PROGRESS_FILE_ENVVAR}) — update not delivered."
        )
        return None
    try:
        _write_progress_to_file(agent_id, message, progress_file)
    except OSError as e:
        logger.warning(f"progress: failed to write {progress_file}: {e}")
        return None
    logger.debug(
        f"Progress update written to file for subagent '{agent_id}': {message[:80]}"
    )
    return "file"


def execute_progress(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Send an intermediate progress update to the parent orchestrator.

    See ``send_progress_to_parent()`` for channel selection. The receipt
    reports delivery only when a parent-reachable channel accepted the update.
    """
    if code is None and kwargs:
        code = kwargs.get("message")
    message = (code or "").strip()
    if not message:
        yield Message(
            "system", "Progress block was empty — no update sent.", quiet=True
        )
        return

    channel = send_progress_to_parent(message)
    if channel == "queue":
        receipt = "Progress update sent to parent orchestrator."
    elif channel == "file":
        receipt = "Progress update sent to parent orchestrator (via file channel)."
    else:
        receipt = (
            "Progress update NOT delivered to parent "
            "(not running as a managed subagent, or the progress channel failed)."
        )
    yield Message("system", receipt, quiet=True)


tool = ToolSpec(
    name="progress",
    desc="Send an intermediate progress update to the parent orchestrator without stopping the session",
    disabled_by_default=True,
    instructions="""
Use this block to send an intermediate status update to the parent orchestrator
while your task is still in progress. The session continues after the update.

```progress
Brief status: what you have completed so far and what still remains.
```

Guidelines:
- Use for meaningful milestones, not every step (too many updates are noise)
- Keep it brief — one or two sentences summarizing state and next steps
- Use ``complete`` when fully done, ``clarify`` when blocked on a question

Example milestones worth reporting:
- Finished analysis phase, starting implementation
- Encountered an obstacle, switching approach
- Completed subtask A, now working on B
""".strip(),
    examples="""
> User: Run a long analysis task using a subagent
> Assistant: I'll start the analysis subagent.
```ipython
subagent("analysis", "Analyze the entire codebase for security issues")
```
> System: Started subagent "analysis"
> System: ⏳ Subagent 'analysis' progress: Finished scanning auth module (47 files). Starting API layer.
> System: ⏳ Subagent 'analysis' progress: API layer done, 3 issues found. Starting data layer.
> System: ✅ Subagent 'analysis' completed: Found 5 security issues across 3 modules. See analysis.md.
""",
    execute=execute_progress,
    block_types=["progress"],
    parameters=[
        Parameter(
            name="message",
            type="string",
            description="Brief status: what is done so far and what remains",
            required=True,
        ),
    ],
    available=True,
)
