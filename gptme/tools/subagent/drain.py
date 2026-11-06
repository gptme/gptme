"""Non-interactive drain policy for owned subagents.

When a non-interactive parent reaches ordinary final text (prompt queue
exhausted) while children it spawned are still running, the session would
otherwise end and SESSION_END cleanup would cancel them, silently discarding
delegated work.

Policy (opt-in, bounded):

- ``drain_timeout`` (``GPTME_SUBAGENT_DRAIN_TIMEOUT`` env var, else
  ``[subagent] drain_timeout`` in ``gptme.toml``, else ``0``) sets ONE
  monotonic closing window per session, in seconds. ``0`` keeps the previous
  behavior: end immediately and cancel, but warn about unfinished children.
- The wait is event-driven via :mod:`gptme.util.notification_ready`; there is
  no LLM polling. Only terminal completion records end the wait early;
  progress updates neither trigger generation nor extend the window.
- The deadline is fixed when the window first opens. A continuation that
  delegates new work cannot reset it.
- Explicit ``complete``, interrupts and errors never enter the window; they
  keep the existing bounded teardown.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .control import CONTROL_FILENAME, drain_control_ops
from .hooks import _unpack_completion
from .types import (
    ReturnType,
    _completion_queue,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
    set_subagent_result_if_absent,
)

logger = logging.getLogger(__name__)

DRAIN_TIMEOUT_ENV = "GPTME_SUBAGENT_DRAIN_TIMEOUT"
# Per-iteration cap so children that die without a completion record (crashed
# subprocess, foreign cancel) are noticed. This is a liveness check, not
# generation.
_LIVENESS_RECHECK_SECONDS = 1.0
_NOTIFY_GRACE_SECONDS = 0.25


def _parse_timeout(value: object, source: str) -> float | None:
    if isinstance(value, bool):
        logger.warning("Ignoring invalid %s=%r: not a number", source, value)
        return None
    try:
        timeout = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid %s=%r: not a number", source, value)
        return None
    if timeout < 0 or timeout != timeout or timeout == float("inf"):
        logger.warning("Ignoring invalid %s=%r: must be finite and >= 0", source, value)
        return None
    return timeout


def drain_timeout() -> float:
    """Resolve the closing-window length: env > project config > 0 (disabled)."""
    env_val = os.environ.get(DRAIN_TIMEOUT_ENV)
    if env_val:
        parsed = _parse_timeout(env_val, DRAIN_TIMEOUT_ENV)
        if parsed is not None:
            return parsed
    try:
        from ...config import get_config

        project = get_config().project
        if project is not None and project.subagent.drain_timeout is not None:
            parsed = _parse_timeout(
                project.subagent.drain_timeout, "[subagent] drain_timeout"
            )
            if parsed is not None:
                return parsed
    except Exception:
        logger.debug("Could not read [subagent] drain_timeout", exc_info=True)
    return 0.0


def _same(left: Path | None, right: Path) -> bool:
    return left is not None and Path(left).resolve() == right


def unfinished_owned_subagents(logdir: Path) -> list[str]:
    """IDs of still-running subagents owned by this parent without a result."""
    parent = Path(logdir).resolve()
    with _subagents_lock:
        snapshot = list(_subagents)
    unfinished: list[str] = []
    for sa in snapshot:
        if not _same(sa.parent_logdir, parent):
            continue
        with _subagent_results_lock:
            if sa.agent_id in _subagent_results:
                continue
        if sa.is_running():
            unfinished.append(sa.agent_id)
    return unfinished


def has_owned_completion(logdir: Path, branch: str | None = None) -> bool:
    """Peek (without consuming) for a queued terminal record for this parent.

    Matches :func:`~.hooks.take_queued_completions` so a positive peek is
    always consumable by the delivery that follows it.
    """
    parent = Path(logdir).resolve()
    with _completion_queue.mutex:
        for item in _completion_queue.queue:
            item_parent, _, _, _, item_branch = _unpack_completion(item)
            if _same(item_parent, parent) and (
                branch is None or (item_branch or "main") == branch
            ):
                return True
    return False


def has_pending_owned_work(logdir: Path, branch: str | None = None) -> bool:
    """True while an owned result is queued or an owned child may still send one.

    A child caches its result just before enqueueing it, so a still-alive
    owned thread/process counts as pending even when its result is cached.
    """
    if has_owned_completion(logdir, branch) or unfinished_owned_subagents(logdir):
        return True
    parent = Path(logdir).resolve()
    with _subagents_lock:
        snapshot = list(_subagents)
    return any(_same(sa.parent_logdir, parent) and sa.is_running() for sa in snapshot)


@dataclass
class DrainWindow:
    """One session's closing window. The deadline never moves once opened."""

    timeout: float
    deadline: float | None = None
    closed: bool = False

    def remaining(self) -> float:
        if self.closed or self.timeout <= 0:
            return 0.0
        if self.deadline is None:
            self.deadline = time.monotonic() + self.timeout
        return max(0.0, self.deadline - time.monotonic())


def _check_drain_cancellation(logdir: Path) -> None:
    """Keep the cooperative cancel checkpoint active without another LLM step."""
    parent = Path(logdir).resolve()
    with _subagents_lock:
        agent = next((sa for sa in _subagents if _same(sa.logdir, parent)), None)
    agent_id = agent.agent_id if agent is not None else parent.name
    cancelled = agent is not None and agent.cancel_event.is_set()
    if not cancelled and (parent / CONTROL_FILENAME).exists():
        try:
            operations = drain_control_ops(parent)
        except OSError:
            # Match the STEP_PRE control hook: an unreadable cancel must not
            # leave a thread blocked until the entire closing window expires.
            operations = [{"op": "cancel", "agent_id": agent_id}]
        for operation in operations:
            if operation.get("op") == "cancel":
                cancelled = True
                candidate = operation.get("agent_id")
                if isinstance(candidate, str):
                    agent_id = candidate
                break
    if cancelled:
        from ..complete import SessionCompleteException

        # Preserve the orchestrator's result (or an already completed result),
        # just as the control hook and background-shell wait do.
        set_subagent_result_if_absent(
            agent_id, ReturnType("cancelled", "Cancelled during subagent drain")
        )
        raise SessionCompleteException("Subagent cancelled during subagent drain")


def wait_for_owned_completion(
    logdir: Path, window: DrainWindow, *, branch: str | None = None
) -> bool:
    """Block until an owned terminal record is queued or the window closes.

    Returns True when a completion is queued for delivery, False when the
    window expired, was disabled, or no owned child is left running. The
    readiness subscription is registered before the first check so a
    completion racing the check is not lost. Cooperative cancellation raises
    SessionCompleteException, as at a normal step boundary.
    """
    from ...util.notification_ready import subscribe

    ready = threading.Event()
    unsubscribe = subscribe(Path(logdir), ready.set)
    try:
        while True:
            # Clear before checking: a signal after the check sets it again.
            ready.clear()
            _check_drain_cancellation(logdir)
            # Open/check the fixed window before admitting even a queued answer.
            remaining = window.remaining()
            if remaining <= 0:
                return False
            if has_owned_completion(logdir, branch):
                return True
            if not has_pending_owned_work(logdir, branch):
                # Children cache their result just before notifying; give a
                # just-finished child a moment to enqueue its record.
                ready.wait(min(remaining, _NOTIFY_GRACE_SECONDS))
                _check_drain_cancellation(logdir)
                return window.remaining() > 0 and has_owned_completion(logdir, branch)
            ready.wait(min(remaining, _LIVENESS_RECHECK_SECONDS))
    finally:
        unsubscribe()
