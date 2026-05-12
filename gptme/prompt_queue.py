"""Durable queued prompts for active conversations."""

from __future__ import annotations

import importlib
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .message import Message

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

try:
    fcntl: Any = importlib.import_module("fcntl")
except ImportError:  # pragma: no cover
    fcntl = None


QUEUE_FILENAME = "prompt-queue.jsonl"
LOCK_FILENAME = ".prompt-queue.lock"


def get_prompt_queue_path(logdir: Path) -> Path:
    return logdir / QUEUE_FILENAME


def _get_prompt_queue_lock_path(logdir: Path) -> Path:
    return logdir / LOCK_FILENAME


@contextmanager
def _prompt_queue_lock(logdir: Path):
    lock_path = _get_prompt_queue_lock_path(logdir)
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


def queue_prompt(logdir: Path, content: str) -> None:
    """Append a prompt to a conversation queue."""
    queue_path = get_prompt_queue_path(logdir)
    record = {
        "content": content,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }

    with _prompt_queue_lock(logdir), queue_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def drain_prompt_queue(logdir: Path, max_items: int | None = None) -> list[Message]:
    """Drain queued prompts into in-memory Message objects.

    If ``max_items`` is set, any extra prompts remain on disk in FIFO order.
    """
    queue_path = get_prompt_queue_path(logdir)
    if not queue_path.exists():
        return []

    with _prompt_queue_lock(logdir):
        if not queue_path.exists():
            return []

        lines = queue_path.read_text(encoding="utf-8").splitlines()
        drained: list[Message] = []
        remaining: list[str] = []

        for line in lines:
            if not line.strip():
                continue

            if max_items is not None and len(drained) >= max_items:
                remaining.append(line)
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed queued prompt in %s", queue_path)
                continue

            content = str(record.get("content", "")).strip()
            if not content:
                logger.warning("Skipping empty queued prompt in %s", queue_path)
                continue

            drained.append(Message("user", content, quiet=True))

        if remaining:
            queue_path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
        else:
            queue_path.unlink(missing_ok=True)

        return drained
