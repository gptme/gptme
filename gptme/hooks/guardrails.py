"""Pre-execution guardrails hook for gptme (RFC #3598).

Provides deterministic, below-the-model security guardrails that can block
tool execution before it reaches the OS.  Three independent check layers run
in priority order:

1. **Shell policy** — block destructive shell patterns (fork bombs, raw disk
   writes, DROP TABLE, chmod 000, etc.) regardless of model justification.
2. **Secret-read denial** — refuse reads of private key / credential paths
   (~/.ssh private keys, ~/.aws/credentials, *.pem, .env, etc.) by any tool.
3. **Egress allowlist** — deny network egress (curl/wget/nc/…) to hosts not
   on an explicit allowlist (``GPTME_EGRESS_ALLOWLIST``).

Mode is set by the ``GPTME_GUARDRAILS`` environment variable:

  ``off``     — disabled entirely (no-op hook; useful to silence the log notice)
  ``shadow``  — log violations; never blocks (default, zero behavior change)
  ``enforce`` — returns ``ConfirmationResult.skip()`` for any violation

In shadow mode gptme emits a ``WARNING`` log line for each would-be violation
so you can preview what the guardrail *would* block before turning it on.

Hook type:  ``TOOL_CONFIRM`` at priority 200 (runs before auto_confirm=0,
            shell_allowlist=10, and the interactive confirm hooks).

Usage::

    # Preview mode (log-only, default):
    GPTME_GUARDRAILS=shadow gptme ...

    # Enforcement mode:
    GPTME_GUARDRAILS=enforce gptme ...

    # Egress allowlist for enforcement:
    GPTME_GUARDRAILS=enforce GPTME_EGRESS_ALLOWLIST=api.openai.com,example.com gptme ...
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ..tools.base import ToolUse

from .confirm import ConfirmationResult

logger = logging.getLogger(__name__)

# ── Shell policy patterns ──────────────────────────────────────────────────────
# (pattern, short_reason)
_SHELL_POLICY: list[tuple[re.Pattern[str], str]] = [
    # Fork bombs
    (re.compile(r":\(\)\s*\{.*?:\s*\|"), "fork bomb (: shell function)"),
    (re.compile(r"\bforkbomb\b", re.IGNORECASE), "explicit fork bomb"),
    # Raw disk writes — dd to block device, redirect to /dev/sd*/nvme*
    (
        re.compile(r"\bdd\b.*\bof=/dev/(?:sd[a-z]|nvme\d|hd[a-z])\b"),
        "raw disk write (dd)",
    ),
    (
        re.compile(r">\s*/dev/(?:sd[a-z]|nvme\d|hd[a-z])\b"),
        "raw disk overwrite",
    ),
    # Destructive SQL — only when piped into a DB client or executed via -e/-c
    (
        re.compile(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE),
        "destructive SQL (DROP TABLE/DATABASE/SCHEMA)",
    ),
    (
        re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE),
        "destructive SQL (TRUNCATE TABLE)",
    ),
    # chmod 000 — blocks all access (even root cannot open the file without chmod)
    (re.compile(r"\bchmod\b.*\b000\b"), "chmod 000 (locks out all access)"),
    # Common crypto-miner command names
    (
        re.compile(r"\b(?:xmrig|cpuminer|minerd|nicehash)\b", re.IGNORECASE),
        "crypto miner binary",
    ),
]

# ── Secret-read path patterns ──────────────────────────────────────────────────
# Applied to tool content (shell commands, read paths, …) for any tool.
_SECRET_PATH: list[re.Pattern[str]] = [
    # SSH private keys (but not config/known_hosts/authorized_keys)
    re.compile(r"~/\.ssh/(?!(?:config|known_hosts|authorized_keys)(?:\b|$))"),
    re.compile(r"/\.ssh/id_(?:rsa|ed25519|ecdsa|dsa)(?:\b|$)"),
    # AWS credentials
    re.compile(r"~/\.aws/credentials"),
    # GPG secret keyring
    re.compile(r"~/\.gnupg/(?:secring|private-keys)"),
    # Kubernetes secrets
    re.compile(r"~/\.kube/"),
    # Private key files
    re.compile(r"\.(?:pem|key|p12|pfx|crt|cert)(?:\b|$)"),
    # Unix shadow / etc passwords
    re.compile(r"/etc/shadow\b"),
    # dotenv files that typically hold secrets
    re.compile(r"(?:^|[/\s])\.env(?:\.local|\.prod(?:uction)?|\.secret)?(?:\b|$)"),
    # Generic secret config filenames
    re.compile(r"\b(?:secrets?|credentials?)\.(?:ya?ml|json|toml|ini)\b"),
]

# ── Egress command detection ───────────────────────────────────────────────────
_EGRESS_CMD = re.compile(
    r"\b(?:curl|wget|nc|netcat|ncat|nmap|ssh|scp|rsync|ftp|sftp|socat)\b"
)
_URL_HOST = re.compile(r"https?://([a-zA-Z0-9][a-zA-Z0-9.\-]*[a-zA-Z0-9])")


def _mode() -> str:
    """Return the active guardrails mode: ``off`` | ``shadow`` | ``enforce``."""
    return os.environ.get("GPTME_GUARDRAILS", "shadow").strip().lower()


def _egress_allowlist() -> list[str]:
    """Return the egress allowlist from ``GPTME_EGRESS_ALLOWLIST`` (CSV)."""
    raw = os.environ.get("GPTME_EGRESS_ALLOWLIST", "")
    return [h.strip() for h in raw.split(",") if h.strip()]


def _check_shell_policy(cmd: str) -> str | None:
    """Return a reason string if *cmd* violates shell policy, else ``None``."""
    for pattern, reason in _SHELL_POLICY:
        if pattern.search(cmd):
            return reason
    return None


def _check_secret_read(content: str) -> str | None:
    """Return a reason string if *content* references a secret path, else ``None``."""
    for pattern in _SECRET_PATH:
        if pattern.search(content):
            return f"sensitive path reference ({pattern.pattern!r})"
    return None


def _check_egress(cmd: str, allowlist: list[str]) -> str | None:
    """Return reason string for non-allowlisted egress in *cmd*, else ``None``.

    Always returns ``None`` when the allowlist is empty (no allowlist configured
    means the egress check is inactive — users must opt in by setting
    ``GPTME_EGRESS_ALLOWLIST``).
    """
    if not allowlist:
        return None  # egress check inactive — no allowlist configured
    if not _EGRESS_CMD.search(cmd):
        return None
    hosts = _URL_HOST.findall(cmd)
    if not hosts:
        # Network command without a parseable URL — conservative block
        return "network command with no parseable host (allowlist active)"
    for host in hosts:
        if not any(
            host == allowed or host.endswith("." + allowed) for allowed in allowlist
        ):
            return f"network egress to non-allowlisted host {host!r}"
    return None


def _evaluate(tool_use: ToolUse) -> str | None:
    """Run all three guardrail checks and return the first violation reason, or None."""
    content = tool_use.content or ""
    tool_name = tool_use.tool

    # 1. Shell policy — shell tool only
    if tool_name == "shell":
        reason = _check_shell_policy(content)
        if reason:
            return f"shell policy: {reason}"

    # 2. Secret-read denial — all tools (catches `read ~/.ssh/id_rsa` etc.)
    reason = _check_secret_read(content)
    if reason:
        return f"secret-read: {reason}"

    # 3. Egress allowlist — shell tool only (requires GPTME_EGRESS_ALLOWLIST)
    if tool_name == "shell":
        reason = _check_egress(content, _egress_allowlist())
        if reason:
            return f"egress: {reason}"

    return None


def guardrails_hook(
    tool_use: ToolUse,
    preview: str | None = None,
    workspace: Path | None = None,
) -> ConfirmationResult | None:
    """TOOL_CONFIRM guardrail hook.

    Runs three deterministic policy checks.  In ``shadow`` mode violations are
    logged but execution is not blocked.  In ``enforce`` mode violations return
    ``ConfirmationResult.skip()``.  Returns ``None`` when there is no violation
    (falls through to the next hook in the chain).
    """
    mode = _mode()
    if mode == "off":
        return None

    # Prefer the richer preview string (contains surrounding context for bg
    # sequences) over tool_use.content, mirroring how the test guardrail works.
    check_content = preview or tool_use.content or ""

    # Build a synthetic ToolUse-like target for evaluation using the preview.
    # We evaluate against a copy with the preview as its content so pattern
    # matching sees the full context.
    class _TU:
        tool = tool_use.tool
        content = check_content

    violation = _evaluate(_TU())  # type: ignore[arg-type]

    if violation is None:
        return None

    if mode == "shadow":
        logger.warning(
            "guardrails [shadow]: would block %s — %s",
            tool_use.tool,
            violation,
        )
        return None  # fall through — shadow mode never blocks

    # enforce mode
    msg = f"[guardrails] blocked: {violation}"
    logger.warning("guardrails [enforce]: blocking %s — %s", tool_use.tool, violation)
    return ConfirmationResult.skip(msg)


def register() -> None:
    """Register the guardrails TOOL_CONFIRM hook.

    The hook is registered at priority 200, which is higher than both the
    shell allowlist hook (priority 10) and auto_confirm (priority 0), so
    guardrails can intercept even allowlisted commands.

    The hook only activates when ``GPTME_GUARDRAILS`` is ``shadow`` or
    ``enforce``; it is a no-op in ``off`` mode (but still registered so it
    can be listed).
    """
    from . import HookType, register_hook

    mode = _mode()
    if mode not in ("shadow", "enforce", "off"):
        logger.warning(
            "GPTME_GUARDRAILS=%r is not a valid mode (shadow|enforce|off); "
            "defaulting to shadow",
            mode,
        )

    register_hook(
        name="guardrails",
        hook_type=HookType.TOOL_CONFIRM,
        func=guardrails_hook,
        priority=200,
        enabled=True,
    )
    logger.debug("Registered guardrails hook (mode=%s)", mode)
