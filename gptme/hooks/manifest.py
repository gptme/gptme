"""Tool-call manifest writer.

When activated via --manifest-dir, writes a JSON record before and after each
tool call. Records are hash-linked into a tamper-evident chain so that
``verify_manifest_chain()`` can detect any retroactive modification.

Each record carries a ``hash`` of its own content and a ``prev_hash`` that
links to the preceding record, forming a linear chain:

    seq=1 pre  →  seq=1 post  →  seq=2 pre  →  seq=2 post  →  …

Together they complement session-level attribution in
``scripts/analysis/sessions-blame.py`` with tool-call granularity and
integrity guarantees.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from . import HookType, register_hook

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from ..hooks.types import StopPropagation, ToolExecutePostData, ToolExecutePreData
    from ..message import Message

logger = logging.getLogger(__name__)


def _sha256_hex(data: str) -> str:
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


def _record_content_hash(record: dict[str, Any]) -> str:
    """Compute a deterministic hash of *record* excluding any ``hash`` field.

    Sorts keys so the hash is independent of insertion order.
    """
    payload = {k: v for k, v in record.items() if k != "hash"}
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return _sha256_hex(canonical)


def _write_record(manifest_dir: Path, fname: str, record: dict[str, Any]) -> bool:
    """Compute the record hash, inject it, and write the JSON file.

    Returns True on success, False if the write failed.  Callers must only
    advance the chain-tail (``prev_hash``) when True is returned so that a
    transient write failure does not leave subsequent records linking to a
    file that was never persisted.
    """
    record["hash"] = _record_content_hash(record)
    try:
        (manifest_dir / fname).write_text(json.dumps(record, indent=2))
        return True
    except OSError as e:
        logger.warning("manifest write failed for %s: %s", fname, e)
        return False


def register_manifest_hooks(manifest_dir: Path) -> None:
    """Register pre/post tool-call hooks that write JSON manifests to *manifest_dir*.

    Each tool call produces two files::

        <session_id>-<seq:04d>-<tool>-pre.json
        <session_id>-<seq:04d>-<tool>-post.json

    Records are hash-linked: every record includes a ``prev_hash`` pointing
    to the preceding record and a ``hash`` of its own content.  The chain:

    - seq=1 pre:  ``prev_hash`` is ``null`` (genesis record).
    - seq=N pre:  ``prev_hash`` = hash of the previous post record.
    - post record: ``prev_hash`` = hash of the corresponding pre record.
    """
    manifest_dir.mkdir(parents=True, exist_ok=True)
    # Resolve session_id once at registration time so all records in this session share
    # the same identifier. Fallback generates a unique id per registration to avoid
    # overwriting files from other anonymous sessions in the same manifest-dir.
    session_id = os.environ.get("GPTME_SESSION_ID") or f"anon-{uuid.uuid4().hex[:8]}"
    # Mutable state captured by both closures: seq counter and chain tail hash.
    seq: list[int] = [0]
    prev_hash: list[str | None] = [None]  # chain tail: hash of last-written record

    def _pre(
        data: ToolExecutePreData,
    ) -> Generator[Message | StopPropagation, None, None]:
        if data.tool_use is None:
            yield from ()
            return
        seq[0] += 1
        model = os.environ.get("GPTME_MODEL", os.environ.get("CC_MODEL", "unknown"))
        args_str = json.dumps(data.tool_use.args, default=str)
        record: dict[str, Any] = {
            "session_id": session_id,
            "model": model,
            "sequence": seq[0],
            "tool": data.tool_use.tool,
            "args_hash": _sha256_hex(args_str),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "phase": "pre",
            "prev_hash": prev_hash[0],  # links to prior post-record (or null)
        }
        if data.tool_use.content:
            record["content_hash"] = _sha256_hex(data.tool_use.content)
        fname = f"{session_id}-{seq[0]:04d}-{data.tool_use.tool}-pre.json"
        if _write_record(manifest_dir, fname, record):
            # Only advance the chain tail when the file was actually persisted.
            prev_hash[0] = record["hash"]
        yield from ()

    def _post(
        data: ToolExecutePostData,
    ) -> Generator[Message | StopPropagation, None, None]:
        if data.tool_use is None:
            yield from ()
            return
        model = os.environ.get("GPTME_MODEL", os.environ.get("CC_MODEL", "unknown"))
        result_text = "\n".join(
            m.content
            for m in (data.result_msgs or ())
            if hasattr(m, "content") and m.content
        )
        record: dict[str, Any] = {
            "session_id": session_id,
            "model": model,
            "sequence": seq[0],
            "tool": data.tool_use.tool,
            "result_hash": _sha256_hex(result_text),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "phase": "post",
            "prev_hash": prev_hash[0],  # links to the pre-record just written
        }
        fname = f"{session_id}-{seq[0]:04d}-{data.tool_use.tool}-post.json"
        if _write_record(manifest_dir, fname, record):
            prev_hash[0] = record["hash"]
        yield from ()

    register_hook("manifest.pre", HookType.TOOL_EXECUTE_PRE, _pre, priority=0)
    register_hook("manifest.post", HookType.TOOL_EXECUTE_POST, _post, priority=0)
    logger.debug("Manifest hooks registered, writing to %s", manifest_dir)


def _verify_session_chain(
    session_id: str,
    entries: list[tuple[Path, dict[str, Any]]],
) -> list[str]:
    """Verify one session's ordered pre/post record chain."""
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    pre_by_seq: dict[int, tuple[Path, dict[str, Any]]] = {}
    post_by_seq: dict[int, tuple[Path, dict[str, Any]]] = {}

    for fpath, record in entries:
        sequence = record["sequence"]
        phase = record["phase"]
        by_seq = pre_by_seq if phase == "pre" else post_by_seq
        if sequence in by_seq:
            errors.append(
                f"{fpath.name}: duplicate {phase} sequence {sequence} "
                f"for session {session_id}"
            )
        else:
            by_seq[sequence] = (fpath, record)

    all_sequences = sorted(set(pre_by_seq) | set(post_by_seq))
    previous_sequence = 0
    for sequence in all_sequences:
        if sequence > previous_sequence + 1:
            gap_start, gap_end = previous_sequence + 1, sequence - 1
            if gap_start == gap_end:
                errors.append(
                    f"Session {session_id} sequence {gap_start}: "
                    "missing pre and post records"
                )
            else:
                errors.append(
                    f"Session {session_id} sequences {gap_start}–{gap_end}: "
                    f"missing ({gap_end - gap_start + 1} tool calls)"
                )
        previous_sequence = sequence

        pre_entry = pre_by_seq.get(sequence)
        post_entry = post_by_seq.get(sequence)
        if pre_entry is None:
            errors.append(
                f"Session {session_id} sequence {sequence}: missing pre record"
            )
        else:
            records.append(pre_entry[1])
        if post_entry is None:
            errors.append(
                f"Session {session_id} sequence {sequence}: missing post record"
            )
        else:
            records.append(post_entry[1])

    for record in records:
        stored_hash = record.get("hash")
        if not isinstance(stored_hash, str) or not stored_hash:
            errors.append(
                f"{record['phase']} seq={record['sequence']}: missing hash field"
            )
            continue
        computed_hash = _record_content_hash(record)
        if stored_hash != computed_hash:
            errors.append(
                f"{record['phase']} seq={record['sequence']}: hash mismatch "
                f"(stored={stored_hash[:20]}…, computed={computed_hash[:20]}…)"
            )

    for index, record in enumerate(records):
        expected_previous = None if index == 0 else records[index - 1].get("hash")
        actual_previous = record.get("prev_hash")
        if actual_previous != expected_previous:
            errors.append(
                f"{record['phase']} seq={record['sequence']}: prev_hash mismatch "
                f"(got={str(actual_previous)[:20]}…, "
                f"expected={str(expected_previous)[:20]}…)"
            )

    return errors


def verify_manifest_chain(manifest_dir: Path) -> list[str]:
    """Verify every session's hash-linked manifest chain in *manifest_dir*.

    Returns a list of integrity errors (empty means every discovered chain is
    intact). A manifest directory may be reused by multiple sessions; each
    ``session_id`` starts an independent chain at sequence 1.
    """
    errors: list[str] = []
    sessions: dict[str, list[tuple[Path, dict[str, Any]]]] = {}

    for fpath in sorted(manifest_dir.glob("*.json")):
        try:
            record = json.loads(fpath.read_text())
        except (json.JSONDecodeError, OSError) as error:
            errors.append(f"Failed to read {fpath.name}: {error}")
            continue
        if not isinstance(record, dict):
            errors.append(
                f"{fpath.name}: expected JSON object, got {type(record).__name__}"
            )
            continue

        session_id = record.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            errors.append(f"{fpath.name}: missing or invalid session_id")
            continue
        sequence = record.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
            errors.append(f"{fpath.name}: missing or invalid sequence number")
            continue
        phase = record.get("phase")
        if phase not in ("pre", "post"):
            errors.append(f"{fpath.name}: missing or invalid phase")
            continue

        sessions.setdefault(session_id, []).append((fpath, record))

    for session_id, entries in sorted(sessions.items()):
        errors.extend(_verify_session_chain(session_id, entries))

    return errors
