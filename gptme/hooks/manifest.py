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


def verify_manifest_chain(manifest_dir: Path) -> list[str]:
    """Verify the hash-linked chain of manifest records in *manifest_dir*.

    Returns a list of error strings (empty list = chain is intact).
    Each error describes a specific integrity violation.

    Checks performed:
    1. Every record's ``hash`` matches its own content.
    2. Every record's ``prev_hash`` matches the ``hash`` of the preceding
       record (in (pre → post → pre → post) order).
    3. The chain is contiguous (no gaps in sequence numbers).
    """
    errors: list[str] = []

    # Collect and sort records by (sequence, phase).
    pre_files = sorted(manifest_dir.glob("*-pre.json"))
    post_files = sorted(manifest_dir.glob("*-post.json"))

    if not pre_files and not post_files:
        return []  # empty directory, nothing to verify

    # Interleave pre and post records in chain order.
    records: list[dict[str, Any]] = []
    pre_by_seq: dict[int, dict[str, Any]] = {}
    post_by_seq: dict[int, dict[str, Any]] = {}

    def _load_records(
        files: list,
        by_seq: dict[int, dict[str, Any]],
        kind: str,
    ) -> None:
        for fpath in files:
            try:
                rec = json.loads(fpath.read_text())
            except (json.JSONDecodeError, OSError) as e:
                errors.append(f"Failed to read {fpath.name}: {e}")
                continue
            if not isinstance(rec, dict):
                errors.append(
                    f"{fpath.name}: expected JSON object, got {type(rec).__name__}"
                )
                continue
            seq = rec.get("sequence")
            if not isinstance(seq, int) or seq <= 0:
                errors.append(f"{fpath.name}: missing or invalid sequence number")
                continue
            if seq in by_seq:
                errors.append(
                    f"{fpath.name}: duplicate {kind} sequence {seq} "
                    f"(collides with another session or forged record)"
                )
            # Last writer wins so the chain attempt is at least deterministic.
            by_seq[seq] = rec

    _load_records(pre_files, pre_by_seq, "pre")
    _load_records(post_files, post_by_seq, "post")

    # Build ordered chain: pre(1) → post(1) → pre(2) → post(2) → …
    # Iterate all_seqs directly rather than range(1, max+1) to avoid DoS from
    # a crafted record with a very large sequence number.
    all_seqs = sorted(set(pre_by_seq.keys()) | set(post_by_seq.keys()))
    if not all_seqs:
        return errors

    prev_seq_num = 0
    for seq_num in all_seqs:
        if seq_num > prev_seq_num + 1:
            gap_start, gap_end = prev_seq_num + 1, seq_num - 1
            if gap_start == gap_end:
                errors.append(f"Sequence {gap_start}: missing pre and post records")
            else:
                errors.append(
                    f"Sequences {gap_start}–{gap_end}: missing "
                    f"({gap_end - gap_start + 1} records)"
                )
        prev_seq_num = seq_num

        pre = pre_by_seq.get(seq_num)
        post = post_by_seq.get(seq_num)
        if pre is None:
            errors.append(f"Sequence {seq_num}: missing pre record")
            continue
        if post is None:
            errors.append(f"Sequence {seq_num}: missing post record")
        records.append(pre)
        if post is not None:
            records.append(post)

    # Check 1: each record's hash matches its content.
    for rec in records:
        stored_hash = rec.get("hash")
        if not stored_hash:
            errors.append(
                f"{rec.get('phase', '?')} seq={rec.get('sequence', '?')}: missing hash field"
            )
            continue
        computed = _record_content_hash(rec)
        if stored_hash != computed:
            errors.append(
                f"{rec['phase']} seq={rec['sequence']}: hash mismatch "
                f"(stored={stored_hash[:20]}…, computed={computed[:20]}…)"
            )

    # Check 2: prev_hash links.
    for i, rec in enumerate(records):
        expected_prev: str | None = None
        if i == 0:
            # Genesis pre-record.
            expected_prev = None
        else:
            expected_prev = records[i - 1].get("hash")

        actual_prev = rec.get("prev_hash")
        if actual_prev != expected_prev:
            phase = rec.get("phase", "?")
            seq = rec.get("sequence", "?")
            errors.append(
                f"{phase} seq={seq}: prev_hash mismatch "
                f"(got={str(actual_prev)[:20]}…, "
                f"expected={str(expected_prev)[:20]}…)"
            )

    return errors
