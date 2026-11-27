"""Deterministic pre-execution guardrail hook.

Provides a TOOL_CONFIRM hook that blocks clearly dangerous tool executions
before they reach the OS, independent of human review or model decisions.

Secret-path ``read`` calls are enforced at the central TOOL_EXECUTE_PRE seam
instead (``guardrail_pre_hook``): ``read`` is read-only and deliberately skips
``execute_with_confirmation()``, so the confirmation chain cannot see it, and
a pre-hook denial can block without ever prompting. Both hooks are registered
under the same name, so ``HOOK_ALLOWLIST`` and ``disable_hook("guardrails")``
apply to both.

This addresses the "confused deputy" problem for headless/autonomous gptme runs:
when ``auto_confirm`` or ``--no-confirm`` is used, there is no human in the
loop to catch dangerous commands. This hook inserts a deterministic layer
BELOW the model and ABOVE the OS that:

1. **Blocks destructive shell commands** — ``rm -rf /``, ``curl | bash``, etc.
2. **Blocks secret-path reads** — ``~/.ssh``, ``~/.aws``, ``.env``, ``*.pem``
   files, etc., from both ``shell`` (cat/grep/less) and ``read`` tools.
3. **Blocks obvious credential output** — shell xtrace, environment/state
   dumps, and sensitive variable expansions printed by echo/printf. Uses
   literal shell syntax, not a sandbox: arbitrary programs, dynamic command
   names and obfuscated expansions remain outside this policy. Quoted prose
   and non-executed heredoc data are not commands. ``printenv PATH/HOME/LANG``
   and other explicitly non-secret diagnostics are allowed; ``compgen -e``
   lists names only. Existing secret-path rules still apply independently.

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

Hook types: TOOL_CONFIRM (priority 200) for the confirmation-gated tools, and
TOOL_EXECUTE_PRE (priority 200) for ``read``'s secret-path rule.
"""

import logging
import os
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from ..message import Message
from ._policy_block import policy_block_message
from .confirm import ConfirmationResult
from .types import StopPropagation, ToolExecutePreData

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
    # Case-folded: case-insensitive filesystems (macOS, Windows) map
    # ``~/.SSH/config`` to ``~/.ssh/config``.
    path, prefix = path.casefold(), prefix.rstrip("/").casefold()
    return path == prefix or path.startswith(prefix + "/")


def _path_spellings(expanded: str, workspace: Path | None = None) -> list[str]:
    """Literal spelling plus its ``..``-collapsed, tool-cwd-anchored form.

    The prefix rules below match text, so ``~/work/../.ssh/config`` and a
    relative ``.ssh/config`` slip past a literal comparison. Symlink targets
    are deliberately not resolved (see ``test_file_symlinks_in_globs``).
    Metacharacters may be literal filename characters after shell quoting
    (``work$/../.ssh/config``), including operands of arbitrary programs that
    the reader-reach analysis does not inspect. Check the collapsed spelling
    too; this does not evaluate substitutions or resolve symlinks.
    """
    if not expanded:
        return [expanded]
    try:
        anchored = os.path.abspath(
            os.path.join(workspace, expanded) if workspace is not None else expanded
        )
    except (OSError, ValueError):
        return [expanded]
    return [expanded] if anchored == expanded else [expanded, anchored]


def _is_secret_path(path: str, workspace: Path | None = None) -> bool:
    """Return True if ``path`` looks like it leads to a secret/credential file."""
    expanded = _normalize_path_token(path)
    return any(_is_secret_spelling(s) for s in _path_spellings(expanded, workspace))


def _is_secret_spelling(expanded: str) -> bool:
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


def _is_secret_target(literal: str) -> bool:
    """True if an absolute tool target, or the file it resolves to, is secret.

    Unlike shell text, a file tool's target is one concrete path, so its
    symlinks are resolved: an in-workspace link to ``~/.ssh/id_rsa`` is the key.
    """
    if _is_secret_spelling(literal):
        return True
    try:
        resolved = os.path.realpath(literal)
    except (OSError, ValueError):
        return False
    return resolved != literal and _is_secret_spelling(resolved)


def _find_secret_path_in_cmd(cmd: str, workspace: Path | None = None) -> str | None:
    """Return the first token in ``cmd`` that looks like a secret path, or None."""
    import shlex

    try:
        # punctuation_chars splits shell operators glued to a word, so
        # `cd ~/.config/gptme;` yields the path rather than `~/.config/gptme;`.
        lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        # Match shlex.split's comments=False; a word-internal # is literal in shell.
        lexer.commenters = ""
        lexer.whitespace_split = True
        tokens = list(lexer)
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
                if value and _is_secret_path(value, workspace):
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
        if _is_secret_path(token, workspace):
            return token

    from ._secret_reach import find_secret_reach

    return find_secret_reach(
        cmd, _is_secret_path, _normalize_path_token, workspace=workspace
    )


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


def _read_secret_path_reason(
    tool_use: "ToolUse", preview: str | None = None
) -> str | None:
    """Return the block reason if a ``read`` call references a secret path.

    Shared by the TOOL_CONFIRM hook (for callers that do route ``read``
    through the confirmation chain) and the TOOL_EXECUTE_PRE seam, so the two
    cannot drift apart.
    """
    path_candidates = _read_paths_from_tool_use(tool_use)
    if preview:
        path_candidates = path_candidates or [
            line.strip()
            for line in preview.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    from ..tools.read import _configured_read_root
    from ..util.workspace import current_workspace

    for path_arg in path_candidates:
        # read expands ~, not shell variables or substitutions. Check its
        # literal spelling too: work$/../.ssh/config still reaches ~/.ssh.
        literal = os.path.expanduser(path_arg)
        try:
            if not os.path.isabs(literal):
                read_root, _ = _configured_read_root()
                literal = os.path.join(read_root or current_workspace(), literal)
            # Keep symlink/.. ordering: collapsing .. before realpath can
            # check a different file than read's Path.resolve() opens.
        except (OSError, ValueError, RuntimeError):
            # A bad relative operand must not hide a later absolute secret
            # in a batch (e.g. when the process cwd has been deleted).
            pass
        if _is_secret_path(path_arg) or _is_secret_target(literal):
            return (
                f"Secret path access blocked: read tool references {path_arg!r}. "
                "Use a dedicated secrets manager or explicit user approval."
            )
    return None


def _patch_many_secret_path_reason(
    tool_use: "ToolUse", preview: str | None = None
) -> str | None:
    """Return the block reason if a ``patch_many`` call writes a secret path.

    Uses the tool's public targets accessor, so the paths checked are exactly
    the ones the call would write, whichever of the three input shapes it uses.
    """
    from ..tools.base import ToolUse
    from ..tools.patch_many import get_patch_many_targets

    # execute_with_confirmation previews the canonical editable payload. MCP
    # previews serialized kwargs instead; in that case inspect the actual call.
    if preview is not None and preview.lstrip().startswith("=== PATH: "):
        tool_use = ToolUse("patch_many", [], preview)
    for resolve_symlinks in (False, True):
        for path, _ in (
            get_patch_many_targets(tool_use, resolve_symlinks=resolve_symlinks) or []
        ):
            if _is_secret_path(str(path)):
                return (
                    f"Secret path access blocked: patch_many writes {str(path)!r}. "
                    "Use a dedicated secrets manager or explicit user approval."
                )
    return None


# Tools whose target is a single path argument (see ``tools.base.get_path``).
_FILE_PATH_TOOLS: tuple[str, ...] = (
    "save",
    "append",
    "patch",
    "morph",
    "hashline_edit",
)


def _file_tool_paths(tool_use: "ToolUse") -> list[str]:
    """Every spelling of the target a save/append/patch/morph call may write."""
    candidates: list[str] = []
    kwargs = getattr(tool_use, "kwargs", None) or {}
    if kwargs.get("path"):
        candidates.append(str(kwargs["path"]))
    args = getattr(tool_use, "args", None)
    if args:
        fn = " ".join(args)
        if fn.startswith(("save ", "append ", "patch ")):
            fn = fn.split(" ", 1)[1]
        candidates.append(fn)
    return [c for c in candidates if c.strip()]


def _file_tool_secret_path_reason(tool_use: "ToolUse") -> str | None:
    """Return the block reason if a file tool targets a secret path.

    Checked before the tool runs: ``patch`` reads the file and ``morph``
    uploads it before any confirmation, so a later check would be too late.
    """
    from ..util.workspace import current_workspace

    for path_arg in _file_tool_paths(tool_use):
        literal = os.path.expanduser(path_arg)
        try:
            if not os.path.isabs(literal):
                literal = os.path.join(current_workspace(), literal)
            # Do not abspath/normpath here: realpath must follow symlinks
            # before interpreting .., just like resolve_in_workspace().
        except (OSError, ValueError, RuntimeError):
            pass
        if _is_secret_path(path_arg) or _is_secret_target(literal):
            return (
                f"Secret path access blocked: {tool_use.tool} targets {path_arg!r}. "
                "Use a dedicated secrets manager or explicit user approval."
            )
    return None


def _shell_text_block_reason(
    cmd: str, subject: str, workspace: Path | None = None
) -> str | None:
    """Return the block reason for shell text, or None if it is allowed.

    The one rule set for anything that ends up in a shell: ``shell`` commands,
    and the text ``tmux`` hands to one (``new-session`` commands, ``send-keys``).
    ``subject`` names the source in the message.
    """
    from ..tools.shell_validation import is_denylisted  # local import for speed

    denied, reason, matched = is_denylisted(cmd)
    if denied:
        block_reason = f"Destructive shell command blocked: {reason}"
        if matched:
            block_reason += f" (matched: {matched!r})"
        return block_reason

    from ._credential_output import credential_output_reason

    credential_reason = credential_output_reason(cmd)
    if credential_reason:
        return credential_reason
    secret = _find_secret_path_in_cmd(cmd, workspace)
    if secret:
        return (
            f"Secret path access blocked: {subject} references {secret!r}. "
            "Use a dedicated secrets manager or explicit user approval."
        )
    return None


def _tmux_block_reason(tool_use: "ToolUse") -> str | None:
    """Return the block reason if a ``tmux`` call runs a disallowed shell text.

    ``tmux`` starts shells (``new-session``) and types into them
    (``send-keys``), so it would otherwise bypass every rule that polices the
    ``shell`` tool. The operations come from the tool's public accessor.
    """
    from ..tools.tmux import (
        get_tmux_key_text,
        get_tmux_operations,
        get_tmux_pane_cwd,
    )

    for verb, arguments in get_tmux_operations(tool_use) or []:
        directory: Path | None
        if verb == "new-session":
            # new_session passes this workspace explicitly via tmux's -c option;
            # the server process cwd may belong to a different conversation.
            from ..util.workspace import current_workspace

            text = arguments
            try:
                directory = current_workspace()
            except OSError:
                directory = None
        elif verb == "send-keys":
            parts = arguments.split(maxsplit=1)
            text = get_tmux_key_text(parts[1]) if len(parts) > 1 else ""
            directory = get_tmux_pane_cwd(parts[0]) if parts else None
        else:
            continue
        # This is a snapshot, not shell emulation: a preceding cd or dynamic
        # shell code can still change how later input resolves paths.
        reason = _shell_text_block_reason(text.strip(), f"tmux {verb}", directory)
        if reason:
            return reason
    return None


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
        cmd = (preview or tool_use.preview_content or "").strip()
        if cmd:
            block_reason = _shell_text_block_reason(cmd, "shell command", workspace)

    elif tool_use.tool == "read":
        # Not dead code: reads dispatched by ToolUse.execute() are policed at
        # the TOOL_EXECUTE_PRE seam, but direct executors (the in-process MCP
        # server) call tool.execute() themselves and dispatch TOOL_CONFIRM.
        # Dropping this branch would silently reopen that MCP gap.
        block_reason = _read_secret_path_reason(tool_use, preview)

    elif tool_use.tool == "patch_many":
        block_reason = _patch_many_secret_path_reason(tool_use, preview)

    elif tool_use.tool == "tmux":
        block_reason = _tmux_block_reason(tool_use)

    elif tool_use.tool in _FILE_PATH_TOOLS:
        block_reason = _file_tool_secret_path_reason(tool_use)

    if block_reason is None:
        return None  # Allow — fall through to next hook

    if mode == "shadow":
        logger.warning("Guardrail (shadow): would block — %s", block_reason)
        return None  # Shadow mode: log but don't actually block

    # Enforce mode — policy_block_message logs the block once at WARNING
    message = f"Blocked by guardrail: {block_reason}"
    if tool_use.tool == "shell":
        from ..tools.shell_validation import format_denial  # fmt: skip

        message = format_denial(message)
    return ConfirmationResult.skip(policy_block_message("guardrail", message).content)


def guardrail_pre_hook(
    data: ToolExecutePreData,
) -> Generator[Message | StopPropagation, None, None]:
    """TOOL_EXECUTE_PRE hook: deny secret-path file tool calls and disallowed ``tmux`` shell text.

    ``read`` is read-only and never calls ``execute_with_confirmation()``, so
    the confirmation chain cannot see it. Blocking here keeps that no-prompt
    guarantee: nothing in this path can ask for consent, and in shadow/off
    modes it does not block at all. A denial is reported to the model by
    ``ToolUse.execute`` as the call's result.

    ``patch_many`` and ``tmux`` are checked here too, through their public
    structured-input accessors: one secret-path target denies the whole
    (atomic) ``patch_many`` call, and ``tmux`` ``new-session``/``send-keys``
    text gets the same rules as the ``shell`` tool. ``save``, ``append``,
    ``patch`` and ``morph`` targets are checked here, before the tool can read
    or upload the file.

    These are literal-input policies, not a sandbox for arbitrary programs.
    """
    mode = _get_mode()
    if mode == "off":  # pragma: no cover - register() skips registration
        return

    tool_use = data.tool_use
    if tool_use is None or tool_use.tool not in (
        "read",
        "patch_many",
        "tmux",
        *_FILE_PATH_TOOLS,
    ):
        return

    if tool_use.tool == "read":
        block_reason = _read_secret_path_reason(tool_use)
    elif tool_use.tool in _FILE_PATH_TOOLS:
        block_reason = _file_tool_secret_path_reason(tool_use)
    elif tool_use.tool == "patch_many":
        block_reason = _patch_many_secret_path_reason(tool_use)
    else:
        block_reason = _tmux_block_reason(tool_use)
    if block_reason is None:
        return

    if mode == "shadow":
        logger.warning("Guardrail (shadow): would block — %s", block_reason)
        return

    data.deny(
        policy_block_message(
            "guardrail", f"Blocked by guardrail: {block_reason}"
        ).content
    )
    return
    yield  # make generator (hook protocol shape; the registry always iterates)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def is_guardrail_active() -> bool:
    """True when a guardrail hook is registered and enabled in the registry.

    The guardrail registers under one name at two seams: TOOL_CONFIRM for the
    tools that route through ``execute_with_confirmation()``, and
    TOOL_EXECUTE_PRE for ``read``. Both are gated by ``HOOK_ALLOWLIST`` and
    ``disable_hook``, so this answers whether *any* guardrail enforcement is
    live rather than assuming a particular hook type.
    """
    from . import HookType, get_hooks

    return any(
        h.name == "guardrails" and h.enabled
        for hook_type in (HookType.TOOL_CONFIRM, HookType.TOOL_EXECUTE_PRE)
        for h in get_hooks(hook_type)
    )


def register() -> None:
    """Register the guardrail hooks with the hook registry."""
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
    # read never reaches TOOL_CONFIRM, so its secret-path policy lives on the
    # central pre-execution seam instead. patch_many and tmux are checked at
    # both seams: MCP dispatches only TOOL_CONFIRM, and patch_many edits must
    # be checked again using the canonical confirmation payload.
    register_hook(
        name="guardrails",
        hook_type=HookType.TOOL_EXECUTE_PRE,
        func=guardrail_pre_hook,
        priority=200,  # before anomaly_watchdog (150) and auto snapshots (90)
        enabled=True,
    )
    logger.debug(f"Registered guardrail hooks (mode={mode})")
