"""Approval gates for destructive tool operations (CLODEx Phase 3a).

When activated via ``--approval-mode``, registers a TOOL_CONFIRM hook that
intercepts DESTRUCTIVE and RISKY tool calls before they execute.  The hook
checks a local SQLite approval registry and, depending on mode:

- ``interactive``: Prompts the user when stdin is a TTY; blocks on denial.
- ``block``:  Always blocks DESTRUCTIVE/RISKY ops unless pre-approved in the DB.
- ``audit``:  Classification only — no blocking; ``approval_class`` is still
  injected into manifest pre-records by ``manifest.py``.

This complements the Phase 2 hash-linked manifest chain (``manifest.py``) by
adding an explicit, durable authorisation trail for irreversible operations.

Operation classes
-----------------
SAFE        Read-only operations: ``read``, ``browser``.
MODIFYING   Non-destructive mutations: ``write``, ``patch``, most shell commands.
DESTRUCTIVE Irreversible deletions or hard resets: ``rm``, ``git reset --hard``,
            ``git push --force``, ``git branch -D``.
RISKY       External-state mutations that are hard to undo: ``gh pr merge``,
            ``git push origin master``, REST DELETE/POST calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from ..tools.base import ToolUse

logger = logging.getLogger(__name__)

# Operation-class constants
OP_SAFE = "SAFE"
OP_MODIFYING = "MODIFYING"
OP_DESTRUCTIVE = "DESTRUCTIVE"
OP_RISKY = "RISKY"

_DESTRUCTIVE_SHELL_PATTERNS: tuple[str, ...] = (
    "rm ",
    "rm -",
    "rmdir ",
    "git reset --hard",
    "git push --force",
    "git push -f ",
    "git branch -D ",
    "git branch -d ",
    "dd if=",
    "mkfs",
    "shred ",
    "truncate -s",
)

_RISKY_SHELL_PATTERNS: tuple[str, ...] = (
    "gh pr merge",
    "gh pr close",
    "gh issue close",
    "gh issue delete",
    "gh release delete",
    "git push origin master",
    "git push origin main",
    "curl -X DELETE",
    "curl -X POST",
    "curl -X PUT",
    "curl --request DELETE",
    "curl --request POST",
    "curl --request PUT",
    "wget --post",
)


def classify_tool(tool: str, args: dict[str, Any]) -> str:
    """Return the operation class for *tool* + *args*.

    Args:
        tool: Tool name (``shell``, ``write``, ``read``, …)
        args: Tool argument dict.

    Returns:
        One of :data:`OP_SAFE`, :data:`OP_MODIFYING`,
        :data:`OP_DESTRUCTIVE`, :data:`OP_RISKY`.
    """
    if tool in ("read", "browser"):
        return OP_SAFE

    if tool == "shell":
        cmd = str(args.get("command") or "")
        # Normalize whitespace so tab/multi-space variants match the same patterns
        normalized = " ".join(cmd.split())
        for pattern in _DESTRUCTIVE_SHELL_PATTERNS:
            if pattern in normalized:
                return OP_DESTRUCTIVE
        for pattern in _RISKY_SHELL_PATTERNS:
            if pattern in normalized:
                return OP_RISKY
        return OP_MODIFYING

    # write/patch modify files but do not delete them
    return OP_MODIFYING


def _intent_hash(tool: str, args: dict[str, Any]) -> str:
    """Deterministic hash of (tool, args) — links approval record to manifest."""
    payload = json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


class ApprovalRegistry:
    """Local SQLite-backed registry for tool-call approval records.

    Each record is keyed by ``intent_hash`` (a deterministic hash of the tool
    name and arguments) so that re-running the same operation with an existing
    approval does not require a second interactive prompt.

    The database is created on first access; no explicit migration is needed.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS approvals (
                    id            TEXT PRIMARY KEY,
                    session_id    TEXT NOT NULL,
                    intent_hash   TEXT NOT NULL UNIQUE,
                    operation_class TEXT NOT NULL,
                    tool          TEXT NOT NULL,
                    status        TEXT NOT NULL DEFAULT 'pending',
                    approved_by   TEXT,
                    approval_mechanism TEXT,
                    workspace     TEXT,
                    created_at    TEXT NOT NULL,
                    approved_at   TEXT,
                    notes         TEXT
                )
            """)
            # Migrate tables created before the workspace column was added
            cols = {row[1] for row in conn.execute("PRAGMA table_info(approvals)")}
            if "workspace" not in cols:
                conn.execute("ALTER TABLE approvals ADD COLUMN workspace TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_intent ON approvals(intent_hash)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session ON approvals(session_id)"
            )

    def get(self, intent_hash: str) -> dict[str, Any] | None:
        """Return approval record for *intent_hash*, or ``None`` if absent."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM approvals WHERE intent_hash = ?",
                (intent_hash,),
            ).fetchone()
            return dict(row) if row else None

    def is_approved(self, intent_hash: str, workspace: Path | None = None) -> bool:
        """Return ``True`` iff an approved record exists for *intent_hash*.

        When *workspace* is provided AND the stored record has a workspace, both
        must resolve to the same path.  This prevents a cross-workspace approval
        (e.g. ``rm ./data`` approved in workspace A) from silently authorising
        the same relative command in workspace B.
        """
        record = self.get(intent_hash)
        if record is None or record["status"] != "approved":
            return False
        stored_ws = record.get("workspace")
        if workspace is not None and stored_ws is not None:
            return str(workspace.resolve()) == stored_ws
        return True

    def approve(
        self,
        session_id: str,
        intent_hash: str,
        operation_class: str,
        tool: str,
        approved_by: str = "user",
        mechanism: str = "interactive_prompt",
        notes: str = "",
        workspace: Path | None = None,
    ) -> str:
        """Insert or replace an approval record.  Returns the approval UUID."""
        approval_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        ws_str = str(workspace.resolve()) if workspace is not None else None
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO approvals
                  (id, session_id, intent_hash, operation_class, tool,
                   status, approved_by, approval_mechanism, workspace,
                   created_at, approved_at, notes)
                VALUES (?, ?, ?, ?, ?, 'approved', ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    session_id,
                    intent_hash,
                    operation_class,
                    tool,
                    approved_by,
                    mechanism,
                    ws_str,
                    now,
                    now,
                    notes,
                ),
            )
        return approval_id

    def list_session(self, session_id: str) -> list[dict[str, Any]]:
        """Return all approval records for *session_id*, ordered by creation."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM approvals WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]


def _default_registry_path() -> Path:
    from ..dirs import get_data_dir

    return get_data_dir() / "approvals.db"


def register_approval_hooks(
    approval_mode: str,
    db_path: Path | None = None,
    session_id: str | None = None,
) -> None:
    """Register a TOOL_CONFIRM hook that gates destructive operations.

    Call this after the manifest hooks have been registered (i.e. when
    ``--manifest-dir`` is provided).  Priority 5 places the approval gate
    *before* ``cli_confirm`` (priority 0) so the approval decision is made
    before the normal tool-preview prompt fires.

    Args:
        approval_mode: ``"audit"`` (classify, no blocking),
            ``"interactive"`` (TTY prompt), or ``"block"`` (always block
            DESTRUCTIVE/RISKY without a prior registry entry).
        db_path: SQLite database path.  Defaults to
            ``<gptme-data-dir>/approvals.db``.
        session_id: Session identifier for the approval record.
    """
    if approval_mode == "audit":
        logger.debug("Approval mode 'audit': classification only, gate not registered")
        return

    from . import HookType, register_hook
    from .confirm import ConfirmAction, ConfirmationResult

    _db_path = db_path or _default_registry_path()
    _session_id = (
        session_id or os.environ.get("GPTME_SESSION_ID") or uuid.uuid4().hex[:8]
    )
    registry = ApprovalRegistry(_db_path)

    def _approval_gate(
        tool_use: ToolUse,
        preview: str | None = None,
        workspace: Path | None = None,
    ) -> ConfirmationResult | None:
        if tool_use is None:
            return None

        # Normalize args: tool-format calls use kwargs; markdown-format shell
        # calls embed the command in content rather than a kwargs["command"] key.
        kw: dict[str, Any] = dict(tool_use.kwargs or {})
        if tool_use.tool == "shell" and "command" not in kw and tool_use.content:
            kw["command"] = tool_use.content

        op_class = classify_tool(tool_use.tool, kw)

        if op_class not in (OP_DESTRUCTIVE, OP_RISKY):
            return None  # safe/modifying ops fall through

        intent = _intent_hash(tool_use.tool, kw)

        if registry.is_approved(intent, workspace=workspace):
            logger.debug(
                "Approval gate: pre-approved %s op %s", op_class, tool_use.tool
            )
            return None  # already approved, fall through

        if approval_mode == "interactive" and sys.stdin.isatty():
            cmd_detail = kw.get("command") or kw.get("path") or tool_use.tool
            print(f"\n⚠️  {op_class} operation requires approval:")
            print(f"   Tool:    {tool_use.tool}")
            print(f"   Details: {str(cmd_detail)[:120]}")
            print(f"   Hash:    {intent[:36]}…")
            try:
                answer = input("   Approve? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"

            if answer in ("y", "yes"):
                registry.approve(
                    session_id=_session_id,
                    intent_hash=intent,
                    operation_class=op_class,
                    tool=tool_use.tool,
                    workspace=workspace,
                )
                return None  # approved — fall through to tool execution
            return ConfirmationResult(
                action=ConfirmAction.SKIP,
                message=f"Denied {op_class} op '{tool_use.tool}' (user said no)",
            )

        # block mode or non-TTY interactive: skip without prompting
        reason = "non-interactive session" if not sys.stdin.isatty() else "block mode"
        logger.warning(
            "Approval gate: blocking %s op '%s' (%s)",
            op_class,
            tool_use.tool,
            reason,
        )
        return ConfirmationResult(
            action=ConfirmAction.SKIP,
            message=(
                f"Blocked {op_class} op '{tool_use.tool}': "
                f"no prior approval in registry ({reason})"
            ),
        )

    register_hook(
        name="approval.gate",
        hook_type=HookType.TOOL_CONFIRM,
        func=_approval_gate,
        priority=5,  # fires before cli_confirm (priority 0)
        enabled=True,
    )
    logger.debug(
        "Approval gate registered (mode=%s, db=%s, session=%s)",
        approval_mode,
        _db_path,
        _session_id,
    )
