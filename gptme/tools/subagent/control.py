"""Durable control operations for a running subagent conversation."""

from __future__ import annotations

import importlib
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

try:
    fcntl: Any = importlib.import_module("fcntl")
except ImportError:  # pragma: no cover
    fcntl = None


CONTROL_FILENAME = "control.jsonl"
LOCK_FILENAME = ".control.lock"


def _control_path(logdir: Path) -> Path:
    return logdir / CONTROL_FILENAME


@contextmanager
def _control_lock(logdir: Path):
    lock_path = logdir / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)

    with lock_path.open("r+") as fd:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)


def append_control_op(logdir: Path, op: str, **payload: object) -> None:
    """Append a control operation for the conversation in *logdir*."""
    record = {
        "op": op,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    control_path = _control_path(logdir)
    with _control_lock(logdir), control_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def drain_control_ops(logdir: Path) -> list[dict[str, object]]:
    """Drain valid control operations from *logdir* in FIFO order."""
    control_path = _control_path(logdir)
    if not control_path.exists():
        return []

    with _control_lock(logdir):
        if not control_path.exists():
            return []

        operations: list[dict[str, object]] = []
        try:
            text = control_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Could not read control file %s: %s", control_path, e)
            return []  # Leave file in place; hook will retry at next checkpoint
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(
                    "Skipping malformed subagent control op in %s", control_path
                )
                continue
            if not isinstance(record, dict):
                logger.warning(
                    "Skipping non-object subagent control op in %s", control_path
                )
                continue
            operations.append(record)

        try:
            control_path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Could not remove %s: %s", control_path, e)
        return operations
