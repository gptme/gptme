"""Tool-call manifest writer.

When activated via --manifest-dir, writes a JSON record before and after each
tool call. Records can be committed alongside session artifacts for attribution
at tool-call granularity (complementing ``scripts/analysis/sessions-blame.py``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from . import HookType, register_hook

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from ..hooks.types import StopPropagation, ToolExecutePostData, ToolExecutePreData
    from ..message import Message

logger = logging.getLogger(__name__)


def _sha256_hex(data: str) -> str:
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


def register_manifest_hooks(manifest_dir: Path) -> None:
    """Register pre/post tool-call hooks that write JSON manifests to *manifest_dir*.

    Each tool call produces two files::

        <session_id>-<seq:04d>-<tool>-pre.json
        <session_id>-<seq:04d>-<tool>-post.json

    The pre record captures the tool name, a hash of the args/content, the
    model, and a timestamp.  The post record adds a hash of the tool result.
    Together they form a chain that ``sessions-blame.py`` can resolve to
    tool-call granularity.
    """
    manifest_dir.mkdir(parents=True, exist_ok=True)
    # Resolve session_id once at registration time so all records in this session share
    # the same identifier. Fallback generates a unique id per registration to avoid
    # overwriting files from other anonymous sessions in the same manifest-dir.
    session_id = os.environ.get("GPTME_SESSION_ID") or f"anon-{uuid.uuid4().hex[:8]}"
    # Mutable counter captured by both closures so pre and post share the same seq.
    seq: list[int] = [0]

    def _pre(
        data: ToolExecutePreData,
    ) -> Generator[Message | StopPropagation, None, None]:
        if data.tool_use is None:
            yield from ()
            return
        seq[0] += 1
        model = os.environ.get("GPTME_MODEL", os.environ.get("CC_MODEL", "unknown"))
        args_str = json.dumps(data.tool_use.args, default=str)
        record: dict = {
            "session_id": session_id,
            "model": model,
            "sequence": seq[0],
            "tool": data.tool_use.tool,
            "args_hash": _sha256_hex(args_str),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "phase": "pre",
        }
        if data.tool_use.content:
            record["content_hash"] = _sha256_hex(data.tool_use.content)
        fname = f"{session_id}-{seq[0]:04d}-{data.tool_use.tool}-pre.json"
        try:
            (manifest_dir / fname).write_text(json.dumps(record, indent=2))
        except OSError as e:
            logger.warning("manifest pre-write failed: %s", e)
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
        record = {
            "session_id": session_id,
            "model": model,
            "sequence": seq[0],
            "tool": data.tool_use.tool,
            "result_hash": _sha256_hex(result_text),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "phase": "post",
        }
        fname = f"{session_id}-{seq[0]:04d}-{data.tool_use.tool}-post.json"
        try:
            (manifest_dir / fname).write_text(json.dumps(record, indent=2))
        except OSError as e:
            logger.warning("manifest post-write failed: %s", e)
        yield from ()

    register_hook("manifest.pre", HookType.TOOL_EXECUTE_PRE, _pre, priority=0)
    register_hook("manifest.post", HookType.TOOL_EXECUTE_POST, _post, priority=0)
    logger.debug("Manifest hooks registered, writing to %s", manifest_dir)
