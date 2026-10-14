"""Command-driven status line for the TUI (``[tui] statusline`` in config.toml).

The command gets a JSON payload on stdin and prints the status line (one or
more lines, ANSI colors allowed). Field names follow Claude Code's statusline
protocol where they map, so one script can serve both; gptme-only data lives
under a ``"gptme"`` key.
"""

import json
import logging
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..message import Message
from ..tools import ToolUse
from ..util.content import is_message_command

logger = logging.getLogger(__name__)

# The command must be quick: it runs on every status change.
TIMEOUT = 2.0


@dataclass
class LogStats:
    """Turn/step/tool counts, updated incrementally as the log grows."""

    turns: int = 0
    steps: int = 0
    tools: Counter = field(default_factory=Counter)
    _seen: int = 0

    def update(self, log: list[Message]) -> None:
        if len(log) < self._seen:  # log rewritten (undo, compact, …): recount
            self.turns, self.steps, self.tools, self._seen = 0, 0, Counter(), 0
        for msg in log[self._seen :]:
            if msg.role == "user" and not is_message_command(msg.content):
                self.turns += 1
            elif msg.role == "assistant":
                self.steps += 1
                for tool_use in ToolUse.iter_from_content(msg.content):
                    if tool_use.is_runnable:
                        self.tools[tool_use.tool] += 1
        self._seen = len(log)


def build_payload(
    *,
    session: str,
    logdir: Path,
    workspace: Path,
    model: str | None,
    context_size: int | None,
    tokens: int | None,
    cost: float,
    duration_ms: int,
    state: str,
    queued: int,
    stats: LogStats,
) -> dict:
    """The JSON sent to the statusline command."""
    payload: dict = {
        "session_id": session,
        "session_name": session,
        "cwd": str(workspace),
        "workspace": {"current_dir": str(workspace), "project_dir": str(workspace)},
        "model": {"id": model, "display_name": model},
        "cost": {
            "total_cost_usd": cost,
            "total_duration_ms": duration_ms,
        },
        "gptme": {
            "logdir": str(logdir),
            "state": state,
            "queued": queued,
            "turns": stats.turns,
            "steps": stats.steps,
            "tools": dict(stats.tools),
        },
    }
    if context_size and tokens is not None:
        payload["context_window"] = {
            "context_window_size": context_size,
            "used_percentage": 100 * tokens / context_size,
            "current_usage": {"input_tokens": tokens},
        }
    return payload


def run_statusline(command: str, payload: dict, cwd: Path) -> str | None:
    """Run the command; its output, or None if it failed (use the built-in)."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=False,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("statusline command failed: %s", e)
        return None
    if result.returncode != 0:
        logger.warning(
            "statusline command exited %d: %s",
            result.returncode,
            result.stderr.strip()[:200],
        )
        return None
    return result.stdout.rstrip("\n") or None
