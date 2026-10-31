"""Conservative admission for abandoned-conversation cleanup (not CRUD delete)."""

import json
import logging
import shutil
from pathlib import Path

from ..prompt_queue import get_prompt_queue_path
from .conversations import ConversationMeta
from .locking import conversation_lifecycle_lock, unowned_conversation

logger = logging.getLogger(__name__)


def _eligible(logdir: Path, max_messages: int) -> bool:
    """Re-read authoritative history, not scan metadata, under deletion ownership."""
    queue = get_prompt_queue_path(logdir)
    if queue.exists() and queue.stat().st_size:
        return False
    if any(logdir.glob("branches/*.jsonl")):
        return False
    # A compacted view may be short while its lossless history is substantive.
    paths = [logdir / "conversation.jsonl", logdir / "lossless.jsonl"]
    paths.extend(logdir.glob("views/*.jsonl"))
    for path in paths:
        if not path.exists():
            continue
        count = 0
        with path.open(encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                # Tolerant history readers may skip damage. Cleanup cannot:
                # a damaged record may be the only copy of user work.
                record = json.loads(line)
                if not isinstance(record, dict) or not isinstance(
                    record.get("content"), str
                ):
                    return False
                count += 1
                if count > max_messages:
                    return False
    return (logdir / "conversation.jsonl").is_file()


def cleanup_conversation(
    conv: ConversationMeta, *, max_messages: int, delete: bool = False
) -> bool:
    """Return whether a candidate is still abandoned; optionally remove it.

    Busy, unreadable or changed candidates are protected. The stable lifecycle
    gate serializes startup/publication with final validation and removal. The
    lifetime lock is probed strictly, including same-process owners, and closed
    before rmtree so Windows can remove the lock file too.
    """
    logdir = Path(conv.path).parent
    try:
        with conversation_lifecycle_lock(logdir, blocking=False):
            if not logdir.is_dir():
                return False
            with unowned_conversation(logdir):
                if not _eligible(logdir, max_messages):
                    return False
            if delete:
                from ..util.cost_tracker import CostTracker, session_id_for_logdir

                shutil.rmtree(logdir)
                CostTracker.end_session(session_id_for_logdir(logdir))
            return True
    except (OSError, ValueError) as exc:
        logger.debug("Cleanup protected %s: %s", conv.id, exc)
        return False


def delete_empty_conversation(conv_id: str, *, max_messages: int = 1) -> bool:
    """Delete only if the discovered conversation is still safe for cleanup."""
    from ..util.conversation_ids import validate_conversation_id
    from .conversations import get_logs_dir

    validate_conversation_id(conv_id)
    # Do not load metadata here: legacy ChatConfig loading creates a workspace
    # and can recreate a directory deleted since the discovery scan.
    conv = ConversationMeta(
        id=conv_id,
        name=conv_id,
        path=str(get_logs_dir() / conv_id / "conversation.jsonl"),
        created=0,
        modified=0,
        messages=0,
        branches=1,
        workspace="",
    )
    return cleanup_conversation(conv, max_messages=max_messages, delete=True)
