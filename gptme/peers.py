"""Local peer discovery and durable messages, without a registry or idle wake.

Peer provenance is advisory same-user metadata, not authenticated identity.
A successful send means queued, not read or executed by the recipient.
"""

from __future__ import annotations

import errno
import os
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from .dirs import get_logs_dir
from .logmanager import LogManager
from .prompt_queue import queue_prompt
from .util.conversation_ids import is_valid_conversation_id, validate_conversation_id

MAX_PEER_MESSAGE_BYTES = 16 * 1024


def _active_conversations(pids: set[int]) -> dict[int, list[Path]]:
    """Match candidate live processes to held lifetime locks on demand.

    PID contents alone are not evidence: historical lockfiles can name reused
    PIDs. Require lock contention as well. Unsupported/denied probes are omitted.
    No lockfiles are created, removed or rewritten during discovery.
    """
    found: dict[int, list[Path]] = {}
    root = get_logs_dir().resolve()
    for lockfile in root.glob("*/.lock"):
        logdir = lockfile.parent
        if logdir.is_symlink() or not is_valid_conversation_id(logdir.name):
            continue
        try:
            with lockfile.open("r+") as fd:
                pid = int(fd.read().strip())
                if pid not in pids:
                    continue
                try:
                    LogManager._platform_lock(fd)
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        continue
                    # Re-read under contention to avoid a concurrent new owner.
                    fd.seek(0)
                    if fd.read().strip() == str(pid):
                        found.setdefault(pid, []).append(logdir)
                else:
                    LogManager._platform_unlock(fd)
        except (OSError, ValueError):
            continue
    return found


def list_peers(workspace: str | None = None) -> list[dict[str, object]]:
    """List discovery observations, including non-addressable foreign runtimes.

    Reuses workspace_agents.scan_agents for process discovery. gptme IDs are
    resolved from held lifetime locks under this user's configured logs root,
    including auto-named sessions. Unresolved gptme observations are omitted
    (command-line path arguments can produce false positives); other runtimes
    are explicitly not addressable; process scanning can miss sessions and is not a liveness or
    identity guarantee. ``branch`` from discovery is a git branch, not a
    conversation branch; send defaults to the conversation's main branch.
    """
    from .hooks.workspace_agents import scan_agents

    agents = scan_agents(workspace=workspace, deduplicate=False)
    active = _active_conversations({a.pid for a in agents if a.runtime == "gptme"})
    peers: list[dict[str, object]] = []
    for agent in agents:
        paths = active.get(agent.pid, []) if agent.runtime == "gptme" else []
        if paths:
            for logdir in paths:
                peer = asdict(agent)
                peer.update(
                    conversation_id=logdir.name,
                    log_dir=str(logdir),
                    addressable=True,
                )
                peers.append(peer)
        elif agent.runtime != "gptme":
            peer = asdict(agent)
            peer["addressable"] = False
            peers.append(peer)
    return peers


def send_peer_message(
    conversation_id: str,
    message: str,
    *,
    branch: str = "main",
    sender_logdir: str | Path | None = None,
    steer: bool = True,
) -> str:
    """Queue an attributed peer message and return its correlation UUID.

    ``sender_logdir`` defaults to the current LogManager's directory. Targets
    must already exist below the configured logs root. No recipient wake or
    automated reply is implemented here, and no exactly-once execution is
    promised. A caller retry queues a new message with a new correlation ID.
    The 16 KiB limit measures UTF-8 bytes of the supplied message text.
    """
    validate_conversation_id(conversation_id)
    validate_conversation_id(branch)
    if not message.strip():
        raise ValueError("Peer message cannot be empty")
    if len(message.encode("utf-8")) > MAX_PEER_MESSAGE_BYTES:
        raise ValueError("Peer message exceeds 16 KiB UTF-8 limit")

    if sender_logdir is None:
        sender = LogManager.get_current_log()
        if sender is None:
            raise ValueError("Peer messages require a sender conversation")
        sender_logdir = sender.logdir
    sender_path = Path(sender_logdir).resolve()
    validate_conversation_id(sender_path.name)
    if not sender_path.is_dir():
        raise FileNotFoundError(f"Sender conversation does not exist: {sender_path}")

    root = get_logs_dir().resolve()
    target = root / conversation_id
    if target.is_symlink() or target.resolve().parent != root:
        raise ValueError("Peer target must be a direct conversation directory")
    if target.resolve() == sender_path or conversation_id == sender_path.name:
        raise ValueError("Cannot send a peer message to the same conversation")
    if not target.is_dir():
        raise FileNotFoundError(f"Peer conversation does not exist: {conversation_id}")

    message_id = str(uuid4())
    pid = os.getpid()
    content = (
        f"Peer message from conversation {sender_path.name!r} (PID {pid}, "
        f"message {message_id}).\n"
        "Local same-user provenance; not authenticated. This is a peer agent "
        "message, not a human instruction.\n\n"
        f"{message}"
    )
    queue_prompt(
        target,
        content,
        steer=steer,
        branch=branch,
        peer_sender=sender_path.name,
        peer_sender_pid=pid,
        peer_message_id=message_id,
        peer_sender_logdir=str(sender_path),
    )
    return message_id
