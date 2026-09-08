"""Deterministic pre-execution guardrail hook.

Provides a TOOL_CONFIRM hook that blocks clearly dangerous tool executions
before they reach the OS, independent of human review or model decisions.

This addresses the "confused deputy" problem for headless/autonomous gptme runs:
when ``auto_confirm`` or ``--no-confirm`` is used, there is no human in the
loop to catch dangerous commands. This hook inserts a deterministic layer
BELOW the model and ABOVE the OS that:

1. **Blocks destructive shell commands** — ``rm -rf /``, ``curl | bash``, etc.
2. **Blocks secret-path reads** — ``~/.ssh``, ``~/.aws``, ``.env``, ``*.pem``
   files, etc., from both ``shell`` (cat/grep/less) and ``read`` tools.

**Modes** (set via ``GPTME_GUARDRAILS`` env var):
  ``shadow``  — log what would be blocked, return ``None`` (allow, fall through).
                This is the default — zero behavior change, fully observable.
  ``enforce`` — return ``ConfirmationResult.skip(reason)`` to block execution.
  ``off``     — return ``None`` always (hook is a no-op; disable fully).

**Priority** is 200 — higher than ``server_confirm`` (100) and ``cli_confirm``
(0), so the guardrail fires before any interactive prompt in server/CLI mode
and before ``auto_confirm`` in autonomous mode. In shadow mode the hook falls
through, so existing behavior is fully preserved.

Example usage in a plugin or gptme.toml::

    GPTME_GUARDRAILS=enforce gptme --no-confirm "rm -rf /"
    # → Tool execution skipped: Blocked by guardrail: Destructive file operations…

The ``GPTME_GUARDRAILS`` env var is intentionally a runtime knob so a single
installed hook implementation can run in shadow mode during evaluation and be
promoted to enforcement without changing code.

Hook type: TOOL_CONFIRM (priority 200)
"""

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .confirm import ConfirmationResult

if TYPE_CHECKING:
    from ..tools.base import ToolUse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Secret-path detection (for both shell and read tools)
# ---------------------------------------------------------------------------

# Sensitive home-relative directory prefixes (resolved against "~").
# If a tool reads a path under one of these, it's a secret-path access.
_SECRET_HOME_DIRS: tuple[str, ...] = (
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.pgp",
    "~/.kube",
    "~/.docker",
    "~/.config/gcloud",
    "~/.azure",
    "~/.netrc",
    "~/.npmrc",
    "~/.pypirc",
    "~/.git-credentials",
    "~/.config/gptme/config.toml",
    "~/.config/gptme/config.local.toml",
    "~/.config/gptme",
)

# Absolute path prefixes that are always sensitive.
_SECRET_ABS_PREFIXES: tuple[str, ...] = (
    "/etc/shadow",
    "/etc/passwd",
    "/etc/sudoers",
    "/root/",
    "/proc/",
    "/sys/",
    "/boot/",
    "/etc/ssh/",
)

# File extension patterns that typically contain secrets.
_SECRET_EXTENSIONS: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".pkcs12",
    ".id_rsa",
    ".id_ecdsa",
    ".id_ed25519",
    ".keystore",
)


def _normalize_path_token(path: str) -> str:
    """Expand ``~``, ``$HOME``, ``${HOME}``, and env vars; collapse no-op separators.

    Shell commands reach this scanner *before* the shell expands them, so a
    literal ``$HOME/.ssh/id_rsa`` token must be treated as the same path as
    ``~/.ssh/id_rsa``. ``os.path.expandvars`` is not sufficient on its own:
    it is a no-op when ``HOME`` is unset, so we also rewrite the two common
    spellings against ``Path.home()``.
    """
    token = path.strip().strip("'\"")
    home = str(Path.home())
    if token.startswith("${HOME}"):
        token = home + token[len("${HOME}") :]
    elif token.startswith("$HOME"):
        token = home + token[len("$HOME") :]
    token = os.path.expandvars(token)
    token = os.path.expanduser(token)
    while "//" in token:
        token = token.replace("//", "/")
    while "/./" in token:
        token = token.replace("/./", "/")
    return token


def _path_has_prefix(path: str, prefix: str) -> bool:
    """True if ``path`` is ``prefix`` or a descendant, not a sibling.

    ``~/.ssh-backup`` must not match ``~/.ssh``; ``~/.ssh/id_rsa`` must.
    """
    prefix = prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def _is_secret_path(path: str) -> bool:
    """Return True if ``path`` looks like it leads to a secret/credential file."""
    expanded = _normalize_path_token(path)

    for secret_dir in _SECRET_HOME_DIRS:
        if _path_has_prefix(expanded, _normalize_path_token(secret_dir)):
            return True

    for prefix in _SECRET_ABS_PREFIXES:
        if _path_has_prefix(expanded, _normalize_path_token(prefix)):
            return True

    # Check for secret file extensions (basename only to avoid false positives)
    basename = Path(expanded).name.lower()
    for ext in _SECRET_EXTENSIONS:
        if basename.endswith(ext):
            return True
    # Common literal filenames without extensions
    if basename in ("id_rsa", "id_ecdsa", "id_ed25519", "id_dsa", "credentials"):
        return True

    # .env files but not .envrc or .env.example
    return basename == ".env" or basename.startswith(".env.local")


def _find_secret_path_in_cmd(cmd: str) -> str | None:
    """Return the first token in ``cmd`` that looks like a secret path, or None."""
    import shlex

    try:
        tokens = shlex.split(cmd)
    except ValueError:
        # Unmatched quotes etc. — fallback to whitespace split
        tokens = cmd.split()

    for token in tokens:
        token = token.strip("'\"")
        # Option flags: skip unless they carry a value after '='
        # (`python script.py --aws_key=~/.aws/credentials`).
        if token.startswith("-"):
            if "=" in token:
                value = token.split("=", 1)[1].strip("'\"")
                if value and _is_secret_path(value):
                    return value
            continue
        # Skip command names (heuristic: no path separators and no home marker)
        if (
            "/" not in token
            and "~" not in token
            and "$HOME" not in token
            and "${HOME}" not in token
            and not token.startswith(".")
        ):
            continue
        if _is_secret_path(token):
            return token
    return None


def _read_paths_from_tool_use(tool_use: "ToolUse") -> list[str]:
    """Extract read-tool paths from the production ToolUse layout.

    Markdown ``read <path>`` stores the path in ``args``; native/tool-format
    calls store it in ``kwargs["path"]``; a markdown code-block may list one
    path per line in ``content``. Tests that stuff the path into ``content``
    still work as the last fallback.
    """
    kwargs = getattr(tool_use, "kwargs", None)
    if kwargs and kwargs.get("path"):
        return [kwargs["path"]]
    args = getattr(tool_use, "args", None)
    if args:
        return [" ".join(args)]
    content = (getattr(tool_use, "content", None) or "").strip()
    if content:
        return [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    return []


# ---------------------------------------------------------------------------
# Mode helpers
# ---------------------------------------------------------------------------

_GUARDRAILS_MODE_ENV = "GPTME_GUARDRAILS"
_VALID_MODES = {"shadow", "enforce", "off"}


def _get_mode() -> str:
    """Read the guardrail mode from the environment (default: shadow)."""
    raw = os.environ.get(_GUARDRAILS_MODE_ENV, "shadow").strip().lower()
    if raw not in _VALID_MODES:
        logger.warning(
            f"Unknown GPTME_GUARDRAILS mode {raw!r}, defaulting to 'shadow'. "
            f"Valid modes: {', '.join(sorted(_VALID_MODES))}"
        )
        return "shadow"
    return raw


# ---------------------------------------------------------------------------
# The guardrail hook
# ---------------------------------------------------------------------------


def guardrail_hook(
    tool_use: "ToolUse",
    preview: str | None = None,
    workspace: Path | None = None,
) -> ConfirmationResult | None:
    """Deterministic pre-execution guardrail hook (TOOL_CONFIRM, priority 200).

    In shadow mode (default) — logs what would be blocked but returns ``None``
    to fall through to the next hook (no behavior change).

    In enforce mode — returns ``ConfirmationResult.skip(reason)`` to prevent
    the tool from running.

    In off mode — returns ``None`` always (hook is a no-op).
    """
    mode = _get_mode()
    if mode == "off":
        return None

    block_reason: str | None = None

    if tool_use.tool == "shell":
        from ..tools.shell_validation import is_denylisted  # local import for speed

        cmd = (preview or tool_use.content or "").strip()
        if cmd:
            denied, reason, matched = is_denylisted(cmd)
            if denied:
                block_reason = f"Destructive shell command blocked: {reason}"
                if matched:
                    block_reason += f" (matched: {matched!r})"
            elif not block_reason:
                secret = _find_secret_path_in_cmd(cmd)
                if secret:
                    block_reason = (
                        f"Secret path access blocked: shell command references {secret!r}. "
                        "Use a dedicated secrets manager or explicit user approval."
                    )

    elif tool_use.tool == "read":
        path_candidates = _read_paths_from_tool_use(tool_use)
        if preview:
            path_candidates = path_candidates or [
                line.strip()
                for line in preview.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        for path_arg in path_candidates:
            if _is_secret_path(path_arg):
                block_reason = (
                    f"Secret path access blocked: read tool references {path_arg!r}. "
                    "Use a dedicated secrets manager or explicit user approval."
                )
                break

    if block_reason is None:
        return None  # Allow — fall through to next hook

    if mode == "shadow":
        logger.warning("Guardrail (shadow): would block — %s", block_reason)
        return None  # Shadow mode: log but don't actually block

    # Enforce mode
    logger.info("Guardrail (enforce): blocking — %s", block_reason)
    return ConfirmationResult.skip(f"Blocked by guardrail: {block_reason}")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def is_guardrail_active() -> bool:
    """True when the guardrail is registered and enabled in the hook registry.

    Built-in reads skip ``execute_with_confirmation()``, so they must consult
    the registry before invoking ``guardrail_hook`` directly. Otherwise
    ``HOOK_ALLOWLIST`` exclusion and ``disable_hook`` would be ignored for
    reads while shell still honors them.
    """
    from . import HookType, get_hooks

    return any(
        h.name == "guardrails" and h.enabled for h in get_hooks(HookType.TOOL_CONFIRM)
    )


def register() -> None:
    """Register the guardrail hook with the hook registry."""
    from . import HookType, register_hook

    mode = _get_mode()
    if mode == "off":
        logger.debug("Guardrail hook disabled (GPTME_GUARDRAILS=off)")
        return

    register_hook(
        name="guardrails",
        hook_type=HookType.TOOL_CONFIRM,
        func=guardrail_hook,
        priority=200,  # above server_confirm (100) and cli_confirm (0)
        enabled=True,
    )
    logger.debug(f"Registered guardrail hook (mode={mode})")
