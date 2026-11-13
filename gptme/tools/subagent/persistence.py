"""Persist and rehydrate subagent registry entries across process restarts.

Each subagent writes a ``subagent-meta.json`` file into its logdir when it is
registered. After a parent gptme process restart, lookup APIs reload that file
so ``subagent_status`` / ``subagent_wait`` / ``subagent_continue`` still work.
Completed agents keep their metadata — removing it on completion would make
resume-after-restart impossible.

The terminal outcome of each attempt is persisted separately in
``subagent-outcome.json``, keyed by the attempt's ``started_at``. Without it a
restarted parent can only infer the outcome from the conversation log, which
reports a child killed at its deadline (or cancelled) as a generic failure.

Rehydrated entries have no live ``thread``/``process`` handles. Thread-mode
children die with the parent, so treating them as terminal is correct.
Reattaching a subprocess that outlived the parent is out of scope: status()
reads the conversation log rather than polling a PID.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from dataclasses import fields
from pathlib import Path
from typing import Any

from .types import (
    TERMINAL_STATUSES,
    ReturnType,
    Subagent,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)

logger = logging.getLogger(__name__)

META_FILENAME = "subagent-meta.json"
# Terminal attempt outcome, keyed by the attempt's ``started_at`` generation.
OUTCOME_FILENAME = "subagent-outcome.json"

# Runtime objects that cannot round-trip through JSON.
_RUNTIME_FIELDS = frozenset(
    {
        "thread",
        "process",
        "cancel_event",
        "prompt_queue_closed",
        "latest_progress",
        "frozen_duration_s",
        "snapshot_outcome",
    }
)
# Required Subagent.__init__ args that must round-trip even when null.
_REQUIRED_FIELDS = frozenset({"agent_id", "prompt", "logdir", "model"})
_PATH_FIELDS = frozenset(
    {
        "logdir",
        "workdir",
        "base_workdir",
        "worktree_path",
        "repo_path",
        "parent_logdir",
    }
)


def _subagent_to_dict(sa: Subagent) -> dict[str, Any]:
    """Serialize a Subagent to a JSON-safe dict (runtime fields omitted)."""
    result: dict[str, Any] = {}
    for field in fields(sa):
        if field.name in _RUNTIME_FIELDS:
            continue
        value = getattr(sa, field.name)
        if value is None:
            if field.name in _REQUIRED_FIELDS:
                result[field.name] = None
            continue
        if field.name == "output_schema" and not isinstance(value, dict):
            # Pydantic/type schemas are not JSON-serializable.
            continue
        if isinstance(value, Path):
            result[field.name] = str(value)
        else:
            result[field.name] = value
    return result


def _dict_to_subagent(data: dict[str, Any]) -> Subagent:
    """Deserialize a dict back into a Subagent (runtime fields defaulted)."""
    payload = dict(data)
    init_names = {f.name for f in fields(Subagent) if f.init}
    for key in list(payload):
        if key not in init_names or key in _RUNTIME_FIELDS:
            payload.pop(key)
            continue
        if key in _PATH_FIELDS and payload[key] is not None:
            payload[key] = Path(payload[key])
    if "started_at" in payload:
        try:
            payload["started_at"] = float(payload["started_at"])
        except (TypeError, ValueError):
            # Invalid timestamps sort oldest so they cannot win newest-id selection.
            payload["started_at"] = 0.0
    payload["thread"] = None
    payload["process"] = None
    return Subagent(**payload)


def _atomic_write_json(logdir: Path, filename: str, payload: str) -> None:
    """Write *payload* to ``logdir/filename`` via a same-directory temp file.

    ``os.replace`` onto the destination means readers never see a truncated
    rewrite. Raises ``OSError`` on failure; callers decide how to report it.
    """
    logdir.mkdir(parents=True, exist_ok=True)
    dest = logdir / filename
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=logdir,
            prefix=f".{Path(filename).stem}-",
            suffix=".tmp",
            delete=False,
        ) as tf:
            tmp_path = tf.name
            tf.write(payload)
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(tmp_path, dest)
        tmp_path = None
    finally:
        if tmp_path is not None:
            Path(tmp_path).unlink(missing_ok=True)


def persist_subagent_meta(sa: Subagent) -> None:
    """Atomically write ``subagent-meta.json`` into the subagent's logdir.

    An interrupted ``Path.write_text`` can leave invalid JSON and make a
    completed child undiscoverable after restart, hence the atomic write.
    """
    try:
        payload = json.dumps(_subagent_to_dict(sa), indent=2, default=str)
        _atomic_write_json(sa.logdir, META_FILENAME, payload)
    except OSError as e:
        logger.warning("Failed to persist subagent meta for %s: %s", sa.agent_id, e)


def persist_subagent_outcome(sa: Subagent, result: ReturnType) -> bool:
    """Durably record *result* as the terminal outcome of *sa*'s current attempt.

    The in-memory result cache does not survive a parent restart, and
    ``_read_log()`` can only guess an outcome from the conversation: a child
    killed at its deadline has no ``complete`` call, so after a restart the
    original ``timeout``/``cancelled`` cause would be lost. The outcome is keyed
    by ``started_at`` (a continuation gets a fresh one), so an outcome from an
    earlier attempt never masks a later attempt in the same logdir.

    Returns False when the write failed; the in-memory cache is unaffected.
    """
    payload = {
        "agent_id": sa.agent_id,
        "generation": sa.started_at,
        "status": result.status,
        "result": result.result,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "tool_uses": result.tool_uses,
        "duration_s": result.duration_s,
    }
    try:
        _atomic_write_json(
            sa.logdir, OUTCOME_FILENAME, json.dumps(payload, indent=2, default=str)
        )
    except OSError as e:
        logger.warning("Failed to persist subagent outcome for %s: %s", sa.agent_id, e)
        return False
    return True


def load_subagent_outcome(sa: Subagent) -> ReturnType | None:
    """Load the persisted terminal outcome for *sa*'s current attempt, if any.

    Returns None when no outcome exists, it is unreadable, or it belongs to a
    different attempt generation (a continuation has started since).
    """
    path = sa.logdir / OUTCOME_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("subagent outcome must be a JSON object")
        if data.get("agent_id") != sa.agent_id:
            return None
        if float(data["generation"]) != sa.started_at:
            return None
        if data.get("status") not in TERMINAL_STATUSES:
            raise ValueError("subagent outcome status must be terminal")
        for name in ("input_tokens", "output_tokens", "tool_uses"):
            value = data.get(name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(
                    f"subagent outcome {name} must be a nonnegative integer"
                )
        duration = data.get("duration_s")
        if duration is not None and (
            type(duration) not in (int, float)
            or not math.isfinite(duration)
            or duration < 0
        ):
            raise ValueError(
                "subagent outcome duration_s must be finite and nonnegative"
            )
        result = data.get("result")
        if result is not None and not isinstance(result, str | dict):
            raise TypeError("subagent outcome result must be str, dict or null")
        return ReturnType(
            data["status"],
            result,
            input_tokens=data.get("input_tokens"),
            output_tokens=data.get("output_tokens"),
            tool_uses=data.get("tool_uses"),
            duration_s=data.get("duration_s"),
        )
    except (
        json.JSONDecodeError,
        OSError,
        TypeError,
        KeyError,
        ValueError,
        OverflowError,
    ) as e:
        logger.warning("Failed to load subagent outcome from %s: %s", sa.logdir, e)
        return None


def restore_subagent_outcome(sa: Subagent) -> bool:
    """Seed the result cache from *sa*'s persisted outcome after a restart.

    Never overwrites a result already cached in this process. Returns True when
    a persisted outcome was restored.
    """
    outcome = load_subagent_outcome(sa)
    if outcome is None:
        return False
    # Match continuation's registry -> result lock order. Loading may have
    # overlapped a continuation, so the file generation check alone is not enough.
    with _subagents_lock:
        current = next(
            (s for s in reversed(_subagents) if s.agent_id == sa.agent_id), None
        )
        if current is not sa:
            return False
        with _subagent_results_lock:
            if sa.agent_id in _subagent_results:
                return False
            _subagent_results[sa.agent_id] = outcome
    return True


def remove_subagent_meta(logdir: Path) -> None:
    """Remove ``subagent-meta.json`` from *logdir* (idempotent)."""
    try:
        (logdir / META_FILENAME).unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Failed to remove subagent meta from %s: %s", logdir, e)


def load_subagent_meta(logdir: Path) -> Subagent | None:
    """Load a Subagent from ``subagent-meta.json`` in *logdir*, if it exists."""
    meta_path = logdir / META_FILENAME
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("subagent meta must be a JSON object")
        return _dict_to_subagent(data)
    except (json.JSONDecodeError, OSError, TypeError, KeyError, ValueError) as e:
        logger.warning("Failed to load subagent meta from %s: %s", logdir, e)
        return None


def load_subagent_by_id(agent_id: str, logs_dir: Path | None = None) -> Subagent | None:
    """Load one persisted subagent by id from the logs directory.

    Planner logdirs are ``subagent-{id}``. Executor/thread/subprocess/ACP logdirs
    are ``subagent-{id}-{4 random chars}``. When several matches exist, return
    the newest by ``started_at``.
    """
    from ...dirs import get_logs_dir

    if logs_dir is None:
        logs_dir = get_logs_dir()
    if not logs_dir.is_dir():
        return None

    prefix = f"subagent-{agent_id}-"
    exact = logs_dir / f"subagent-{agent_id}"
    candidates: list[Subagent] = []
    try:
        entries = list(logs_dir.iterdir())
    except OSError as e:
        logger.warning("Failed to scan logs dir %s: %s", logs_dir, e)
        return None
    for entry in entries:
        # Filter by name before touching the filesystem: the logs dir holds every
        # conversation, so a stat per entry made each cold lookup of an unknown
        # id cost ~1s at 20k logs while the name check costs ~2ms.
        if entry != exact and not entry.name.startswith(prefix):
            continue
        if not entry.is_dir():
            continue
        sa = load_subagent_meta(entry)
        if (
            sa is not None
            and sa.agent_id == agent_id
            and (
                (entry / "conversation.jsonl").exists()
                or load_subagent_outcome(sa) is not None
            )
        ):
            candidates.append(sa)
    if not candidates:
        return None
    return max(candidates, key=lambda sa: sa.started_at)


def scan_rehydrate_subagents(logs_dir: Path | None = None) -> list[Subagent]:
    """Scan the logs directory for subagent meta files and return Subagents.

    Requires a conversation or a valid terminal outcome, plus readable metadata.
    Queued cancellation can persist an outcome before creating a conversation.
    Explicit cleanup removes metadata, so it cannot resurrect a removed child.
    A failed directory listing raises ``OSError`` so the
    one-shot registry rehydration can retry instead of treating the failure as
    an empty (complete) scan.
    """
    from ...dirs import get_logs_dir

    if logs_dir is None:
        logs_dir = get_logs_dir()
    try:
        if not logs_dir.is_dir():
            return []
        entries = list(logs_dir.iterdir())
    except OSError as e:
        logger.warning("Failed to scan logs dir %s: %s", logs_dir, e)
        raise

    rehydrated: list[Subagent] = []
    for entry in entries:
        if not entry.is_dir() or not entry.name.startswith("subagent-"):
            continue
        sa = load_subagent_meta(entry)
        if sa is not None and (
            (entry / "conversation.jsonl").exists()
            or load_subagent_outcome(sa) is not None
        ):
            rehydrated.append(sa)
    return rehydrated
