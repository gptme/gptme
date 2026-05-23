"""
Vent/feedback tool — emits in-the-moment friction signals to a durable ledger.

The agent calls this when stuck or frustrated. Signals are written to:
  ~/.local/share/gptme/friction-ledger.jsonl

Rate-limited to one vent per turn to prevent recursive venting spirals
(Lovable found agents can spiral into 43+ vents without this guard).

Usage::

    ```vent
    pytest exits 0 with "no tests found" even though tests/test_vent.py
    exists. Tried --co and an explicit path; the discovery config is wrong.
    Owner: tooling
    ```

Resolution owner (axis 1 — who/what unblocks this) is an optional, small,
stable enum captured at vent time. Richer theme/cause clustering happens later
at analysis time, so keep the capture label thin:

  self          Solvable now with better prompting / context / reasoning
  tooling       Needs a tool / permission / config / env change
  operator      Needs a human (decision, credential, approval, account action)
  upstream      Needs a fix in a dependency we don't own
  architectural Not solvable in the current stack design

The ``Type`` keyword accepts both deprecated legacy aliases
(Type1->self, Type2a->tooling, Type2b->architectural, Type0->operator) and
current taxonomy values (e.g. ``Type: self``, ``Type: tooling``).
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..hooks import HookType
from ..message import Message
from .base import ToolSpec

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..logmanager import LogManager

logger = logging.getLogger(__name__)

# Per-turn rate-limit: blocks a second vent within the same agent turn.
_vent_this_turn: ContextVar[bool] = ContextVar("vent_this_turn", default=False)

# Axis 1: who/what unblocks this. Small, stable, captured at vent time.
VALID_OWNERS = ("self", "tooling", "operator", "upstream", "architectural")

# Deprecated Lovable-style type labels -> resolution owner.
_DEPRECATED_TYPE_MAP = {
    "type0": "operator",
    "type1": "self",
    "type2a": "tooling",
    "type2b": "architectural",
}

# A trailing "Owner: X" / "Type: X" / "Resolution: X" line (case-insensitive).
_OWNER_LINE_RE = re.compile(r"^(?:owner|type|resolution)\s*[:=]\s*(.+)$", re.IGNORECASE)


def _get_ledger_path() -> Path:
    """Return the friction ledger path, creating parent dirs if needed."""
    from ..dirs import get_data_dir

    path = get_data_dir() / "friction-ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _parse_resolution_owner(code: str) -> tuple[str, str | None]:
    """Split an optional trailing owner tag off a vent message.

    Recognizes a final line like ``Owner: tooling``, ``Type: Type2a``, or
    ``Resolution: operator`` (case-insensitive). Returns the message with that
    line removed and the normalized resolution owner, or the original message
    and ``None`` when no valid tag is present (an unrecognized value is left
    as part of the message rather than silently dropped).
    """
    lines = code.rstrip().splitlines()
    if not lines:
        return code.strip(), None

    match = _OWNER_LINE_RE.match(lines[-1].strip())
    if not match:
        return code.strip(), None

    # Drop any parenthetical note, e.g. "tooling (need API key)" -> "tooling".
    raw = match.group(1).strip()
    before_paren = raw.split("(", 1)[0]
    token = before_paren.split()[0].lower() if before_paren.split() else ""
    owner = _DEPRECATED_TYPE_MAP.get(token, token)
    if owner not in VALID_OWNERS:
        return code.strip(), None

    message = "\n".join(lines[:-1]).strip()
    return message, owner


def execute_vent(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Message:
    """Append a friction entry to the ledger."""
    if not code or not code.strip():
        return Message("system", "vent: no message — nothing recorded.", quiet=True)

    if _vent_this_turn.get():
        return Message(
            "system",
            "vent: rate limit — only one vent per turn; signal not recorded.",
            quiet=True,
        )

    message, resolution_owner = _parse_resolution_owner(code)
    if not message:
        return Message("system", "vent: no message — nothing recorded.", quiet=True)

    entry: dict[str, str] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "workspace": str(Path.cwd()),
        "message": message,
    }
    if resolution_owner:
        entry["resolution_owner"] = resolution_owner

    ledger_path = _get_ledger_path()
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")

    _vent_this_turn.set(True)
    logger.info("Vent recorded to %s", ledger_path)
    return Message("system", f"Friction signal recorded to {ledger_path}", quiet=True)


def _reset_vent_limit(manager: LogManager) -> Generator[Message, None, None]:
    """Reset per-turn rate limit before each generation step."""
    _vent_this_turn.set(False)
    yield from ()


tool = ToolSpec(
    name="vent",
    desc="Emit a real-time friction signal when stuck or frustrated",
    instructions="""
Use when you are stuck, frustrated, or hitting a repeated failure. Write a
brief description of what you're trying to do and what's blocking you, then
optionally tag who/what would unblock it on a final line (``Owner: <owner>``):

- **self** — solvable now with better prompting / context / reasoning
- **tooling** — needs a tool, permission, config, or env change
- **operator** — needs a human (decision, credential, approval, account action)
- **upstream** — needs a fix in a dependency we don't own
- **architectural** — not solvable in the current stack design

Tag it when you know who would unblock you — the tag is optional, and an
imprecise guess is more useful than none. Using this tool creates a durable
record so recurring blockers can be identified and fixed, improving your
future performance on similar tasks.
""".strip(),
    examples="""
> User: fix the failing test
> Assistant:
```vent
Stuck: test expects a dict but execute returns a plain string.
No kwarg to change the return shape; need to rethink the API.
Owner: self
```
> System: Friction signal recorded to /home/user/.local/share/gptme/friction-ledger.jsonl
""".strip(),
    execute=execute_vent,
    block_types=["vent"],
    available=True,
    hooks={
        "reset_vent_limit": (
            HookType.STEP_PRE.value,
            _reset_vent_limit,
            10,
        ),
    },
)
