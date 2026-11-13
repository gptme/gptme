"""Durable queued prompts for active conversations."""

from __future__ import annotations

import importlib
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from .message import Message, MessageMetadata

if TYPE_CHECKING:
    from collections.abc import Iterator

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
def _prompt_queue_lock(logdir: Path) -> Iterator[None]:
    from .logmanager.locking import conversation_lifecycle_lock

    # All queue operations share cleanup's outer gate, including drains whose
    # initial exists() check may precede deletion. Never recreate a deleted chat.
    with conversation_lifecycle_lock(logdir):
        if not logdir.is_dir():
            raise FileNotFoundError(f"Conversation no longer exists: {logdir}")
        lock_path = _get_prompt_queue_lock_path(logdir)
        with lock_path.open("a+") as fd:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)


_QUEUE_ROLES = {"user", "system", "assistant"}


def _record_from_message(message: Message) -> dict | None:
    """Serialize a queued message for durable storage."""
    content = str(message.content).strip()
    if not content:
        return None
    record: dict = {
        "content": content,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    if message.role in _QUEUE_ROLES and message.role != "user":
        record["role"] = message.role
    metadata = message.metadata or {}
    skill_invocation_id = metadata.get("skill_invocation_id")
    if isinstance(skill_invocation_id, str) and skill_invocation_id.strip():
        record["skill_invocation_id"] = skill_invocation_id
    if metadata.get("steer") is True:
        record["steer"] = True
    for key in (
        "branch",
        "peer_sender",
        "peer_sender_pid",
        "peer_message_id",
        "peer_sender_logdir",
    ):
        value = metadata.get(key)
        if value is not None:
            record[key] = value
    return record


def _message_from_record(record: dict) -> Message | None:
    """Rebuild a queued message, defaulting unknown roles to user."""
    content = str(record.get("content", "")).strip()
    if not content:
        return None
    role = record.get("role", "user")
    if not isinstance(role, str) or role not in _QUEUE_ROLES:
        role = "user"
    metadata: dict[str, Any] = {}
    skill_invocation_id = record.get("skill_invocation_id")
    if isinstance(skill_invocation_id, str) and skill_invocation_id.strip():
        metadata = {"skill_invocation_id": skill_invocation_id}
    for key in ("branch", "peer_sender", "peer_message_id", "peer_sender_logdir"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            metadata[key] = value
    pid = record.get("peer_sender_pid")
    if type(pid) is int and pid > 0:
        metadata["peer_sender_pid"] = pid
    if record.get("steer") is True:
        metadata["steer"] = True
    return Message(
        cast(Literal["system", "user", "assistant"], role),
        content,
        quiet=True,
        metadata=cast(MessageMetadata, metadata) if metadata else None,
    )


def requeue_prompts(
    logdir: Path, messages: list[Message], *, prepend: bool = True
) -> int:
    """Write in-memory queued prompts back to disk without executing them.

    Interactive exhaustion parks remaining intent here so the chat loop can
    return control without discarding work or immediately retrying under a
    fresh budget. Prepend keeps already-drained FIFO order ahead of prompts
    that were still on disk.
    """
    records = [
        record
        for message in messages
        if (record := _record_from_message(message)) is not None
    ]
    if not records:
        return 0

    queue_path = get_prompt_queue_path(logdir)
    new_lines = [json.dumps(record) for record in records]
    with _prompt_queue_lock(logdir):
        existing: list[str] = []
        if queue_path.exists():
            existing = [
                line
                for line in queue_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        lines = new_lines + existing if prepend else existing + new_lines
        # Publish only a complete replacement: ENOSPC during parking must not
        # truncate prompts that were already durable. Keep the stable lock
        # held across staging and rename so concurrent appenders stay ordered.
        fd, name = tempfile.mkstemp(prefix=".prompt-queue-", dir=logdir)
        staged = Path(name)
        try:
            os.close(fd)
            staged.write_text("\n".join(lines) + "\n", encoding="utf-8")
            staged.replace(queue_path)
        finally:
            staged.unlink(missing_ok=True)
    return len(records)


def queue_prompt(
    logdir: Path,
    content: str,
    *,
    steer: bool = False,
    skill_invocation_id: str | None = None,
    branch: str = "main",
    peer_sender: str | None = None,
    peer_sender_pid: int | None = None,
    peer_message_id: str | None = None,
    peer_sender_logdir: str | None = None,
) -> None:
    """Append a prompt to a conversation queue.

    Args:
        steer: Mark as a steering message for mid-turn injection via the STEP_PRE
            hook.  Regular (non-steer) prompts are drained between turns; steer
            prompts are drained at each STEP_PRE checkpoint so the next LLM call
            in the same turn sees them immediately.
        skill_invocation_id: Correlate a queued skill prompt with its invocation.
        branch: Target conversation branch. Legacy records target main.
        peer_sender: Sender conversation ID; local provenance, not authentication.
        peer_sender_pid: Sender process ID.
        peer_message_id: Correlation nonce for this peer message.
        peer_sender_logdir: Optional sender conversation directory.
    """
    queue_path = get_prompt_queue_path(logdir)
    record: dict = {
        "content": content,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    if steer:
        record["steer"] = True
    if skill_invocation_id is not None:
        record["skill_invocation_id"] = skill_invocation_id

    if branch != "main":
        record["branch"] = branch
    if peer_sender is not None:
        record["peer_sender"] = peer_sender
    if peer_sender_pid is not None:
        record["peer_sender_pid"] = peer_sender_pid
    if peer_message_id is not None:
        record["peer_message_id"] = peer_message_id
    if peer_sender_logdir is not None:
        record["peer_sender_logdir"] = peer_sender_logdir

    with _prompt_queue_lock(logdir), queue_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def drain_prompt_queue(
    logdir: Path, max_items: int | None = None, *, branch: str = "main"
) -> list[Message]:
    """Drain regular prompts for branch, leaving steer and other branches queued.

    Legacy records without a branch target main. Excess prompts remain in FIFO
    order when ``max_items`` is set.
    """
    return _drain_prompts(logdir, max_items, branch=branch, kind="regular")


def drain_steer_prompts(
    logdir: Path, max_items: int | None = None, *, branch: str = "main"
) -> list[Message]:
    """Drain steer prompts for branch at a mid-turn checkpoint.

    Regular prompts, other branches, and excess prompts remain queued.
    """
    return _drain_prompts(logdir, max_items, branch=branch, kind="steer")


def drain_peer_prompts(logdir: Path, *, branch: str = "main") -> list[Message]:
    """Drain only peer messages for branch, whether regular or steering.

    Automatic peer wakes must not execute unrelated queued user/skill commands.
    Peer provenance is same-user local metadata, not authenticated identity.
    """
    return _drain_prompts(logdir, None, branch=branch, kind="peer")


def _drain_prompts(
    logdir: Path,
    max_items: int | None,
    *,
    branch: str,
    kind: Literal["regular", "steer", "peer"],
) -> list[Message]:
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
                remaining.append(line)
                continue
            if not isinstance(record, dict):
                remaining.append(line)
                continue
            # Never consume another branch's intent, including on a peer wake.
            if record.get("branch", "main") != branch:
                remaining.append(line)
                continue
            steer = record.get("steer") is True
            if kind == "peer":
                sender = record.get("peer_sender")
                matches = isinstance(sender, str) and bool(sender.strip())
            else:
                matches = (
                    steer
                    if kind == "steer"
                    else not steer and not record.get("peer_sender")
                )
            if not matches:
                remaining.append(line)
                continue
            message = _message_from_record(record)
            if message is None:
                logger.warning("Skipping empty queued prompt in %s", queue_path)
                continue
            drained.append(message)

        if remaining:
            queue_path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
        else:
            queue_path.unlink(missing_ok=True)
        return drained
