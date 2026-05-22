"""
Vent/feedback tool — emits in-the-moment friction signals to a durable ledger.

The agent calls this when stuck or frustrated. Signals are written to:
  ~/.local/share/gptme/friction-ledger.jsonl

Rate-limited to one vent per turn to prevent recursive venting spirals
(Lovable found agents can spiral into 43+ vents without this guard).

Usage::

    ```vent
    Stuck on import order: uv run pytest exits 0 with "no tests found"
    even though tests/test_vent.py exists. Tried --co and explicit path.
    Type: Type2a (config/permission)
    ```

Friction types (from Lovable's taxonomy):
  Type 1  — solvable with better prompting / context
  Type 2a — solvable with a tool, permission, or config change
  Type 2b — not solvable in the current stack
"""

from __future__ import annotations

import json
import logging
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


def _get_ledger_path() -> Path:
    """Return the friction ledger path, creating parent dirs if needed."""
    from ..dirs import get_data_dir

    path = get_data_dir() / "friction-ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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

    _vent_this_turn.set(True)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "workspace": str(Path.cwd()),
        "message": code.strip(),
    }

    ledger_path = _get_ledger_path()
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")

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
brief description of what you're trying to do, what's blocking you, and
optionally classify the type:

- **Type 1** — solvable with better prompting or context
- **Type 2a** — solvable with a tool, permission, or config change
- **Type 2b** — not solvable in the current stack

Rate-limited to **one vent per turn** to prevent recursive venting.
Signals are written to the friction ledger for offline analysis.
""".strip(),
    examples="""
> User: fix the failing test
> Assistant:
```vent
Stuck: test expects a dict but execute returns a plain string.
No kwarg to change the return shape; need to rethink the API.
Type: Type1 (need better spec)
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
