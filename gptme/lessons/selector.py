"""Default-off transport to the shared advisory lesson selector JSON CLI.

Eligibility, hard guards, session limits and lesson rendering stay in gptme.
The configured service owns selection, cache policy and provider budgets.
"""

import hashlib
import json
import logging
import math
import os
import signal
import subprocess
import tempfile
from collections.abc import Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from .matcher import MatchResult
from .parser import Lesson

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)
_MAX_BYTES = 1_048_576
_last_intent: ContextVar[tuple[str, str] | None] = ContextVar(
    "lesson_selector_intent", default=None
)


def _transport(command: list[str], request: dict[str, Any], timeout: float) -> Any:
    """Bound the owned CLI process tree; only read a bounded response into memory."""
    payload = json.dumps(request).encode()
    if len(payload) > _MAX_BYTES:
        raise ValueError("request too large")
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output:
        source.write(payload)
        source.seek(0)
        with subprocess.Popen(
            command,
            stdin=source,
            stdout=output,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as process:
            try:
                process.wait(timeout=timeout)
            finally:
                # On interruption the unreaped CLI still owns this PID/group.
                # A successful service has already joined its provider worker.
                if process.returncode is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
            if process.returncode != 0:
                raise ValueError("selector command failed")
        output.seek(0)
        raw = output.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("response too large")
    return json.loads(raw)


def refine_matches(
    matches: list[MatchResult],
    manager: "LogManager",
    is_guard: Callable[[Lesson, set[str]], bool],
) -> list[MatchResult]:
    """Narrow eligible matches, preserving their order and all hard guards.

    Call after holdouts, before emitted-lesson dedup, so emitting a selected
    lesson does not change the eligible catalog and invalidate its cached choice.
    Any configuration, transport or protocol failure returns the exact baseline.
    """
    raw_command = os.environ.get("GPTME_LESSONS_SELECTOR_COMMAND")
    if not raw_command or not matches:
        return matches
    try:
        raw_guards = os.environ.get("GPTME_LESSONS_SELECTOR_GUARDS") or ""
        guards = {
            token.strip().lower().replace("\\", "/")
            for token in raw_guards.split(",")
            if token.strip()
        }
        if not guards:
            raise ValueError("explicit guard configuration required")
        if os.name != "posix":
            raise ValueError("bounded process-tree transport requires POSIX")
        command = json.loads(raw_command)
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(arg, str) or not arg for arg in command)
        ):
            raise ValueError("command must be a nonempty JSON argv array")
        timeout = float(os.environ.get("GPTME_LESSONS_SELECTOR_TIMEOUT") or "2")
        calls = int(os.environ.get("GPTME_LESSONS_SELECTOR_MAX_CALLS") or "0")
        if not math.isfinite(timeout) or not 0 < timeout <= 30 or not 0 <= calls <= 10:
            raise ValueError("invalid selector deadline or call budget")
        model = os.environ.get("GPTME_LESSONS_SELECTOR_MODEL")
        if not model or not calls:
            raise ValueError("explicit model and positive call budget required")
        protected = {
            str(match.lesson.path)
            for match in matches
            if is_guard(match.lesson, guards)
            or match.lesson.metadata.always_apply
            or (match.lesson.metadata.priority or "").lower() == "critical"
        }
        advisory = [
            match for match in matches if str(match.lesson.path) not in protected
        ]
        if not advisory:
            return matches
        messages = manager.log.messages
        user_index = next(
            (i for i in range(len(messages) - 1, -1, -1) if messages[i].role == "user"),
            None,
        )
        if user_index is None:
            return matches
        query = messages[user_index].content
        session_id = str(manager.logdir.resolve())
        intent = (session_id, query)
        # A resumed conversation already in a tool turn must not restart paid
        # selection merely because this process has no in-memory cadence state.
        in_tool_turn = any(
            message.role == "assistant" for message in messages[user_index + 1 :]
        )
        event = (
            "tool" if _last_intent.get() == intent or in_tool_turn else "user_prompt"
        )
        _last_intent.set(intent)
        request: dict[str, Any] = {
            "version": 1,
            "runtime": "gptme",
            "session_id": session_id,
            "event_kind": event,
            "query": query,
            "context_fingerprint": hashlib.sha256(query.encode()).hexdigest(),
            "policy_version": "gptme-advisory-v1:" + json.dumps(sorted(guards)),
            "candidates": [
                {
                    "id": str(match.lesson.path),
                    "description": match.lesson.description,
                    "metadata": {
                        "keywords": match.lesson.metadata.keywords,
                        "tools": match.lesson.metadata.tools,
                        "priority": match.lesson.metadata.priority,
                    },
                }
                for match in advisory
            ],
            "fallback_ids": [str(match.lesson.path) for match in advisory],
            "max_results": 3,
            "model": model,
            "timeout_seconds": timeout,
            "max_calls_per_session": calls,
        }
        # Leave transport startup/serialization headroom around the provider's
        # own deadline while keeping the complete subprocess tree bounded.
        result = _transport(command, request, timeout + 1)
        if (
            not isinstance(result, dict)
            or type(result.get("version")) is not int
            or result["version"] != 1
        ):
            raise ValueError("invalid result version")
        status = result.get("status")
        if status == "fallback":
            logger.debug("Lesson selector fallback: %s", result.get("reason"))
            return matches
        ids = result.get("ids")
        allowed = {str(match.lesson.path) for match in advisory}
        if (
            status not in ("selected", "empty")
            or not isinstance(ids, list)
            or any(
                not isinstance(identifier, str) or identifier not in allowed
                for identifier in ids
            )
            or len(ids) != len(set(ids))
            or len(ids) > request["max_results"]
            or (status == "empty") != (len(ids) == 0)
        ):
            raise ValueError("invalid selected IDs")
        keep = protected | set(ids)
        logger.debug("Lesson selector %s: %d advisory matches", status, len(ids))
        return [match for match in matches if str(match.lesson.path) in keep]
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        logger.debug("Lesson selector fallback: %s", exc)
        return matches
