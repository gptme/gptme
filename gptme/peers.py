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
    lockfiles = set(root.glob("*/.lock"))
    # Linux peers may use a different GPTME_LOGS_HOME (e.g. per-run roots).
    # Inspect only discovered processes, not their environments or the filesystem
    # tree. Other platforms/denied procfs access retain local-root discovery.
    for pid in pids:
        try:
            for proc_fd in Path(f"/proc/{pid}/fd").iterdir():
                try:
                    path = proc_fd.readlink()
                    if path.is_absolute() and path.name == ".lock":
                        lockfiles.add(path)
                except OSError:
                    continue
        except OSError:
            continue
    for lockfile in sorted(lockfiles):
        logdir = lockfile.parent
        if (
            logdir.is_symlink()
            or lockfile.is_symlink()
            or not is_valid_conversation_id(logdir.name)
        ):
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
    """Discover peers; only addressable gptme IDs accept messages.

    Reuses workspace_agents.scan_agents for process discovery. gptme IDs are
    resolved from held lifetime locks, including auto-named sessions. On Linux,
    process file descriptors also locate conversations in other logs roots;
    otherwise discovery is limited to the configured logs root. Use ``log_dir``
    as an explicit send target when names collide. Unresolved gptme observations
    are omitted (command-line path arguments can produce false positives); other
    runtimes are explicitly not addressable. Process scanning can miss sessions
    and is not a liveness or
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


def _resolve_peer_target(conversation_id: str) -> Path:
    """Resolve local names first, or an explicit path/unique live peer name."""
    path = Path(conversation_id)
    root = get_logs_dir().resolve()
    if path.is_absolute():
        validate_conversation_id(path.name)
        if ".." in path.parts or path != path.resolve():
            raise ValueError("Peer target must be a direct conversation directory")
        # An explicit path must identify a conversation, not an arbitrary dir.
        if (
            not (path / "conversation.jsonl").is_file()
            or (path / "conversation.jsonl").is_symlink()
        ):
            raise ValueError("Peer target must contain a conversation.jsonl file")
        target = path
    else:
        validate_conversation_id(conversation_id)
        target = root / conversation_id
        if not target.exists() and not target.is_symlink():
            matches = {
                Path(str(peer["log_dir"]))
                for peer in list_peers()
                if peer.get("addressable")
                and peer.get("conversation_id") == conversation_id
            }
            if len(matches) > 1:
                raise ValueError(
                    "Ambiguous peer conversation name; use its absolute log_dir"
                )
            if matches:
                target = matches.pop()
    if target.is_symlink():
        raise ValueError("Peer target must be a direct conversation directory")
    if not target.is_dir():
        raise FileNotFoundError(f"Peer conversation does not exist: {conversation_id}")
    return target.resolve()


def send_peer_message(
    conversation_id: str,
    message: str,
    *,
    branch: str = "main",
    sender_logdir: str | Path | None = None,
    steer: bool = True,
) -> str:
    """Queue peer input; return correlation UUID.

    ``sender_logdir`` defaults to the current LogManager's directory. Targets
    may be a name below the configured logs root, a unique live peer name from
    ``list_peers``, or an absolute conversation directory (containing
    ``conversation.jsonl``). Local names take precedence; ambiguous external
    names require an explicit path. No recipient wake or
    automated reply is implemented here, and no exactly-once execution is
    promised. A caller retry queues a new message with a new correlation ID.
    The 16 KiB limit measures UTF-8 bytes of the supplied message text.
    """
    from .tools import is_read_only_session  # fmt: skip

    if is_read_only_session():
        # A peer may hold every tool; a read-only agent must not steer it.
        raise PermissionError("Peer messages are not allowed under a read-only profile")
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

    target = _resolve_peer_target(conversation_id)
    if target == sender_path:
        raise ValueError("Cannot send a peer message to the same conversation")

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
