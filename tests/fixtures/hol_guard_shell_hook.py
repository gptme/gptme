"""Opt-in HOL Guard shell-classification hook for gptme ``TOOL_CONFIRM``.

Copy this module into a plugin (for example ``hol_guard/hooks/shell_classify.py``)
and call ``register()``. gptme does not bundle or enable HOL Guard.

This hook classifies shell command text with the official CLI::

    hol-guard command test <command> --json

It does not auto-approve commands. An explicitly benign classification with
``minimum_action == "allow"`` returns ``None`` so remaining confirmation hooks
still run. Every other classification, timeout, missing executable, or invalid
output skips the shell action.

This CLI reports ``policy_evaluation: "not_run"``: it is command classification,
not final policy enforcement, approval creation, receipts, or protection of
other tools. The CLI can attempt daemon startup as a side effect of inspection.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import TYPE_CHECKING

from gptme.hooks import HookType, register_hook
from gptme.hooks.confirm import ConfirmationResult

if TYPE_CHECKING:
    from pathlib import Path

    from gptme.tools.base import ToolUse

logger = logging.getLogger(__name__)

HOOK_NAME = "hol_guard.shell_classify"
# Out-rank every built-in TOOL_CONFIRM hook. Current built-ins:
#   guardrails                 200
#   anomaly_watchdog.confirm   150
#   watch.sleep_poll_guard     150
#   server_confirm / tui_confirm 100
#   shell.allowlist / watch.allowlist 10  (these auto-confirm)
#   cli_confirm / auto_confirm 0
# Returning None for a benign classification still lets those hooks run.
HOOK_PRIORITY = 250
HOL_GUARD_EXECUTABLE = "hol-guard"
TIMEOUT_SECONDS = 10.0


def _shell_command(tool_use: ToolUse, preview: str | None) -> str:
    """Return the command text gptme would confirm for this shell call."""
    if preview and preview.strip():
        return preview.strip()
    content = getattr(tool_use, "preview_content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    return (getattr(tool_use, "content", None) or "").strip()


def _skip(reason: str) -> ConfirmationResult:
    logger.warning("HOL Guard skipped shell command: %s", reason)
    return ConfirmationResult.skip(reason)


def hol_guard_shell_hook(
    tool_use: ToolUse,
    preview: str | None = None,
    workspace: Path | None = None,
) -> ConfirmationResult | None:
    """Classify shell commands; leave every other tool unchanged."""
    if tool_use.tool != "shell":
        return None

    command = _shell_command(tool_use, preview)
    if not command:
        return _skip("HOL Guard: shell command text is missing or invalid.")

    executable = shutil.which(HOL_GUARD_EXECUTABLE)
    if executable is None:
        return _skip("HOL Guard: hol-guard executable was not found.")

    try:
        completed = subprocess.run(
            [executable, "command", "test", command, "--json"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return _skip("HOL Guard: command inspection timed out.")
    except OSError:
        return _skip("HOL Guard: command inspection was unavailable.")
    except UnicodeDecodeError:
        return _skip("HOL Guard: command inspection returned invalid output.")

    if completed.returncode != 0:
        return _skip("HOL Guard: command inspection failed.")

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return _skip("HOL Guard: command inspection returned invalid JSON.")

    if not isinstance(result, dict):
        return _skip("HOL Guard: command inspection returned invalid output.")

    classification = result.get("classification")
    explicitly_benign = (
        isinstance(classification, dict)
        and classification.get("explicitly_benign") is True
    )
    if explicitly_benign and result.get("minimum_action") == "allow":
        # Fall through so cli/server/allowlist confirmation hooks still run.
        return None

    return _skip("HOL Guard: command requires review before execution.")


def register() -> None:
    """Register the opt-in HOL Guard shell-classification hook."""
    register_hook(
        HOOK_NAME,
        HookType.TOOL_CONFIRM,
        hol_guard_shell_hook,
        priority=HOOK_PRIORITY,
    )
