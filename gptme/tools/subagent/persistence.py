"""Persist and rehydrate subagent registry entries across process restarts.

Each subagent writes a ``subagent-meta.json`` file into its logdir when it is
registered. After a parent gptme process restart, lookup APIs reload that file
so ``subagent_status`` / ``subagent_wait`` / ``subagent_continue`` still work.
Completed agents keep their metadata — removing it on completion would make
resume-after-restart impossible.

Rehydrated entries have no live ``thread``/``process`` handles. Thread-mode
children die with the parent, so treating them as terminal is correct.
Reattaching a subprocess that outlived the parent is out of scope: status()
reads the conversation log rather than polling a PID.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import fields
from pathlib import Path
from typing import Any

from .types import Subagent

logger = logging.getLogger(__name__)

META_FILENAME = "subagent-meta.json"

# Runtime objects that cannot round-trip through JSON.
_RUNTIME_FIELDS = frozenset(
    {"thread", "process", "cancel_event", "prompt_queue_closed"}
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


def persist_subagent_meta(sa: Subagent) -> None:
    """Atomically write ``subagent-meta.json`` into the subagent's logdir.

    Write to a same-directory temp file and ``os.replace`` onto the destination
    so readers never see a truncated rewrite. An interrupted
    ``Path.write_text`` can leave invalid JSON and make a completed child
    undiscoverable after restart.
    """
    try:
        sa.logdir.mkdir(parents=True, exist_ok=True)
        dest = sa.logdir / META_FILENAME
        payload = json.dumps(_subagent_to_dict(sa), indent=2, default=str)
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=sa.logdir,
                prefix=".subagent-meta-",
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
    except OSError as e:
        logger.warning("Failed to persist subagent meta for %s: %s", sa.agent_id, e)


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
        if not entry.is_dir():
            continue
        if entry != exact and not entry.name.startswith(prefix):
            continue
        if not (entry / "conversation.jsonl").exists():
            continue
        sa = load_subagent_meta(entry)
        if sa is not None and sa.agent_id == agent_id:
            candidates.append(sa)
    if not candidates:
        return None
    return max(candidates, key=lambda sa: sa.started_at)


def scan_rehydrate_subagents(logs_dir: Path | None = None) -> list[Subagent]:
    """Scan the logs directory for subagent meta files and return Subagents.

    Skips directories without ``conversation.jsonl`` (explicit cleanup) and
    unreadable metadata. A failed directory listing raises ``OSError`` so the
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
        if not (entry / "conversation.jsonl").exists():
            continue
        sa = load_subagent_meta(entry)
        if sa is not None:
            rehydrated.append(sa)
    return rehydrated
