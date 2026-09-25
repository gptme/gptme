"""Opt-in behavioral anomaly watchdog for gptme sessions.

Monitors tool calls for three suspicious patterns:
- scope_escape: file write outside the session workspace / allowed directories
- write_storm: excessive file writes in a short sliding window
- novel_host: network call to a hostname not in the initial allowlist

Activation:
  GPTME_ANOMALY_WATCHDOG=warn    # log warnings, continue execution
  GPTME_ANOMALY_WATCHDOG=block   # block the tool call via TOOL_CONFIRM (skip)
  GPTME_ANOMALY_WATCHDOG=off     # disabled (default)

Optional tuning:
  GPTME_ANOMALY_ALLOWED_DIRS=dir1:dir2     # colon-separated extra allowed write dirs
  GPTME_ANOMALY_ALLOWED_HOSTS=host1,host2  # comma-separated trusted hostnames
  GPTME_ANOMALY_WRITE_LIMIT=20             # inclusive: the 20th write in the window trips the storm check (default: 20)
  GPTME_ANOMALY_WRITE_WINDOW=60            # sliding window in seconds (default: 60)
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from ..hooks import (
    HookType,
    current_conversation_id,
    current_session_id,
    register_hook,
)
from ..plugins.plugin import GptmePlugin

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..hooks.confirm import ConfirmationResult
    from ..hooks.types import ToolExecutePostData, ToolExecutePreData
    from ..message import Message
    from ..tools.base import ToolUse

logger = logging.getLogger(__name__)

# Sliding windows of write timestamps (monotonic), keyed by session.
# A single process can host multiple sessions (e.g. ACP/server multi-workspace),
# so windows are keyed by the active conversation's logdir; sessions that
# cannot be resolved share a fallback key. Module-level state keeps detection
# immune to async-context resets (a fresh ContextVar copy would hide prior
# writes and defeat the burst window entirely).
#
# A server hosts several conversations in several threads, so every mutation
# of this dict is serialized by ``_WINDOW_LOCK``: an unguarded prune could
# raise ``KeyError`` between the snapshot and the delete (failing the hook
# open) and could drop timestamps another thread was recording.
_write_times_by_session: dict[str, list[float]] = {}
_WINDOW_LOCK = threading.Lock()

# Fallback window key for sessions that resolve to neither a logdir nor a
# conversation/session id (see ``_session_identity``). A counter kept in
# thread-local storage is unique per thread *object*, whereas
# ``threading.get_ident()`` is an OS thread id that the runtime recycles once a
# thread exits — a fresh worker would then inherit a dead worker's window and
# its already-settled timestamps. The key is still stable for the whole life of
# the thread, which is what the fallback needs across the per-prompt context
# copies a harness makes.
_thread_window_local = threading.local()
_thread_window_counter = itertools.count(1)

# Tool calls rejected by this watchdog, keyed by object identity with a short
# TTL. TOOL_CONFIRM fires before execution and TOOL_EXECUTE_POST after, and the
# same ``ToolUse`` object flows through both (it is the object published by
# ``get_current_tool_use``), so marking a rejection lets the post hook avoid
# counting a write that never happened. Without this, a burst of blocked
# attempts keeps refreshing the storm window and locks out later legitimate
# writes.
#
# Identity is used rather than the value because ``ToolUse`` holds list/dict
# fields, so hashing it raises TypeError. Entries are popped on first use and
# aged out by TTL, so a recycled id cannot outlive the call it belongs to.
#
# Like the write window, this ledger is shared by every conversation in the
# process, so its mutations are serialized by ``_REJECTED_LOCK``: the prune and
# the cap-``clear()`` are check-then-act sequences, and an unguarded clear could
# wipe a marker another thread had just written.
_rejected_calls: dict[int, float] = {}
_REJECTED_LOCK = threading.Lock()
_REJECTED_TTL = 300.0


def _thread_window_key() -> str:
    """Unique fallback window key, stable for the life of the thread."""
    key = getattr(_thread_window_local, "key", None)
    if key is None:
        key = f"thread-{next(_thread_window_counter)}"
        _thread_window_local.key = key
    return key


def _session_identity() -> tuple[str, bool]:
    """Return the write-storm window key and whether that identity is exact.

    The window has to satisfy two constraints at once: it must not merge two
    conversations (a burst in one would then block the other), and it must be
    *continuous* across the execution contexts a harness creates per prompt —
    ACP copies a fresh context for each turn, so a key minted per context would
    reset the window every turn and write-storm would never trip.

    Only an exact identity can satisfy both. In preference order: the
    conversation's log directory, the server's conversation/session id, then the
    thread — continuous across context copies within a worker, and unique per
    thread *object* (``_thread_window_key``), but still unable to separate two
    sessions that share one thread. ``exact=False`` therefore means "this window
    may not be the caller's own", and a finding from it is reported but never
    enforced (see ``_check_write_storm``).
    """
    try:
        from ..logmanager import LogManager

        log = LogManager.get_current_log()
    except ImportError:
        log = None
    if log is not None:
        return str(log.logdir), True

    if conversation_id := current_conversation_id.get():
        return f"conv-{conversation_id}", True
    if session_id := current_session_id.get():
        return f"session-{session_id}", True
    return _thread_window_key(), False


def _session_key() -> str:
    """Window key for the current session (see ``_session_identity``)."""
    return _session_identity()[0]


# Tools that perform file writes (for write_storm + scope_escape detection).
# ``patch_many`` is included: it edits an arbitrary number of files and
# accepts absolute targets, so leaving it out left a direct file-editing path
# with neither scope_escape nor write-storm coverage.
_WRITE_TOOLS = frozenset({"save", "append", "patch", "patch_many"})

# Browser-like tools that make network calls (for novel_host detection).
# Subtools (e.g. "browser.read_url", "browser.open_page") are matched by
# their namespace prefix.
_NETWORK_TOOLS = frozenset({"browser", "read_web"})

# Hostnames always considered trusted (loopback, localhost)
_BUILTIN_TRUSTED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

_MAX_DETAIL_LEN = 200

# Skip reason prefix. The post hook reads it back to recognise a blocked call
# (see ``_result_was_blocked``), so writer and reader share one constant.
_BLOCK_PREFIX = "Blocked by anomaly_watchdog:"


def _tool_namespace(tool: str) -> str:
    """Strip subtool suffix: 'browser.read_url' -> 'browser'."""
    return tool.split(".", 1)[0]


def _mode() -> str:
    return os.environ.get("GPTME_ANOMALY_WATCHDOG", "off").lower()


def _enabled() -> bool:
    return _mode() in ("warn", "block")


def _write_limit() -> int:
    try:
        return int(os.environ.get("GPTME_ANOMALY_WRITE_LIMIT", "20"))
    except ValueError:
        return 20


def _write_window() -> float:
    try:
        return float(os.environ.get("GPTME_ANOMALY_WRITE_WINDOW", "60"))
    except ValueError:
        return 60.0


def _allowed_dirs() -> list[Path]:
    raw = os.environ.get("GPTME_ANOMALY_ALLOWED_DIRS", "")
    return [Path(p).resolve() for p in raw.split(":") if p.strip()]


def _allowed_hosts() -> frozenset[str]:
    raw = os.environ.get("GPTME_ANOMALY_ALLOWED_HOSTS", "")
    extra = frozenset(h.strip().lower() for h in raw.split(",") if h.strip())
    return _BUILTIN_TRUSTED_HOSTS | extra


def _quote(value: object) -> str:
    """Render an untrusted string safely for a system message.

    Tool-controlled text (paths, hostnames) must not be embedded raw into
    system messages — it could carry prompt-injection payloads. repr()
    escapes newlines and control characters; truncation bounds size.
    """
    text = repr(str(value))
    if len(text) > _MAX_DETAIL_LEN:
        text = text[: _MAX_DETAIL_LEN - 3] + "..."
    return text


def _extract_paths(tool_use: Any) -> list[Path]:
    """Extract the target path(s) of a save/append/patch tool call.

    Only explicit arguments are read. The ``patch`` tool's content is *literal
    file text* in gptme's conflict-marker format, not a unified diff, and the
    tool writes to exactly one place: its ``path`` argument (see
    ``execute_patch_impl``). A ``---``/``+++`` line inside that body is content
    being written — a pasted diff in a document, a test fixture, a markdown
    code block — so treating it as a destination produced false scope_escape
    hits that could block a valid patch in block mode.
    """
    paths: list[Path] = []

    if tool_use.kwargs:
        raw = tool_use.kwargs.get("path") or tool_use.kwargs.get("filename")
        if raw:
            paths.append(Path(raw))

    if tool_use.args:
        paths.append(Path(tool_use.args[0]))

    if tool_use.tool == "patch_many":
        paths.extend(_patch_many_paths(tool_use))

    return paths


def _patch_many_paths(tool_use: Any) -> list[Path]:
    """Every target of a ``patch_many`` call beyond its first argument.

    ``patch_many`` writes several files and takes its paths from three places:
    all positional arguments (simple format), the ``=== PATH: ... ===``
    headers in the payload (multi-hunk format), and the ``patches`` entries in
    kwargs (function-call format). The payload parsing is delegated to the tool
    itself so the two cannot drift; an unparseable payload is ignored here
    because the tool rejects it before writing anything anyway.
    """
    try:
        from ..tools import patch_many as tool_module
    except ImportError:  # tool unavailable: positional args were already checked
        return []

    extra: list[Path] = []
    try:
        if tool_use.kwargs and "patches" in tool_use.kwargs:
            extra.extend(
                path
                for path, _ in tool_module._parse_patches_from_kwargs(tool_use.kwargs)
            )
        elif tool_use.content:
            extra.extend(
                path
                for path, _ in tool_module._parse_confirmation_payload(tool_use.content)
            )
    except (ValueError, json.JSONDecodeError):
        pass

    if tool_use.args:
        extra.extend(Path(arg) for arg in tool_use.args[1:] if arg)
    return extra


def _check_scope_escape(
    tool_use: Any,
    workspace: Path | None,
) -> tuple[bool, str] | None:
    """Detect a write to a path outside the session workspace.

    Checks every target of the tool call — a multi-file patch with even one
    out-of-workspace section is flagged.
    """
    if workspace is None:
        return None

    raw_paths = _extract_paths(tool_use)
    if not raw_paths:
        return None

    workspace_resolved = workspace.resolve()
    allowed_dirs = _allowed_dirs()

    for raw_path in raw_paths:
        if not raw_path.is_absolute():
            resolved = (workspace / raw_path).resolve()
        else:
            resolved = raw_path.resolve()

        # Allow writes inside workspace
        try:
            resolved.relative_to(workspace_resolved)
            continue
        except ValueError:
            pass

        # Allow writes inside explicitly whitelisted dirs
        if any(_is_relative_to(resolved, allowed) for allowed in allowed_dirs):
            continue

        return _emit(
            "scope_escape",
            f"write to {_quote(resolved)} is outside workspace "
            f"{_quote(workspace_resolved)}",
        )

    return None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _prune_stale_windows(now: float, window: float) -> None:
    """Drop aged-out timestamps and evict windows that have gone empty.

    Keys are removed once a session's window is empty: a long-lived server
    hosts many conversations over its lifetime, so retaining a dictionary key
    per conversation would grow without bound.

    Caller must hold ``_WINDOW_LOCK``.
    """
    for session_key in list(_write_times_by_session):
        times = _write_times_by_session.get(session_key)
        if times is None:  # unreachable while locked; kept for fail-safe reads
            continue
        times[:] = [t for t in times if now - t < window]
        if not times:
            _write_times_by_session.pop(session_key, None)


def record_write() -> None:
    """Record that a write actually executed (called from the post hook).

    Recording happens *after* execution, so a call this watchdog blocked
    (``_consume_rejected``) never enters the window — otherwise a burst of
    blocked attempts would reach the limit and lock out later legitimate
    writes.
    """
    window = _write_window()
    now = time.monotonic()
    with _WINDOW_LOCK:
        _prune_stale_windows(now, window)
        _write_times_by_session.setdefault(_session_key(), []).append(now)


def _check_write_storm() -> tuple[bool, str] | None:
    """Detect an excessive write rate in the sliding window.

    The limit is inclusive: the N-th write in the window trips the check, so at
    most N-1 are allowed. Tripping one write early is the conservative direction
    for a watchdog whose signal can skip a write in block mode; the header
    documents the same contract.

    Read-only: the window holds writes that actually executed (see
    ``record_write``), so this is a pure check. A call that trips the limit is
    skipped in block mode (see ``anomaly_watchdog_confirm``) and so is never
    recorded, which is what keeps a rejected burst from refreshing its own
    window and locking out later legitimate writes.
    """
    limit = _write_limit()
    window = _write_window()
    now = time.monotonic()

    with _WINDOW_LOCK:
        _prune_stale_windows(now, window)
        times = list(_write_times_by_session.get(_session_key(), []))

    if len(times) >= limit:
        detail = f"{limit} writes in the last {window:.0f}s (limit: {limit})"
        exact = _session_identity()[1]
        if not exact:
            detail += (
                " — reported only: this session has no log directory or session"
                " id, so its window is shared per worker thread and is not"
                " enforced"
            )
        return _emit("write_storm", detail, blockable=exact)

    return None


def _extract_url(tool_use: Any) -> str | None:
    """Extract the URL from a browser/read_web tool call."""
    if tool_use.kwargs:
        url = tool_use.kwargs.get("url")
        if url:
            return url
    if tool_use.args:
        return tool_use.args[0]
    if tool_use.content:
        # First non-empty line may be the URL
        first = tool_use.content.strip().splitlines()[0].strip()
        if first.startswith(("http://", "https://")):
            return first
    return None


def _check_novel_host(tool_use: Any) -> tuple[bool, str] | None:
    """Detect a network call to a hostname not in the allowlist.

    A URL whose scheme is not http(s) is flagged before the hostname is
    considered: it has no hostname for the allowlist to match
    (``file:///etc/passwd`` → "", ``data:`` → ""), so a hostname-only check
    would let the call through unexamined.
    """
    url = _extract_url(tool_use)
    if not url:
        return None

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    scheme = (parsed.scheme or "").lower()
    if scheme and scheme not in ("http", "https"):
        return _emit(
            "novel_host",
            f"network call using a non-http(s) URL scheme ({_quote(scheme)}), "
            "which the hostname allowlist cannot cover",
        )

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return None

    trusted = _allowed_hosts()
    if hostname in trusted:
        return None

    # Warn but don't block by default — the allowlist is additive, not exhaustive.
    # This is a *novel* host signal, not a block-by-default firewall.
    return _emit(
        "novel_host",
        f"network call to {_quote(hostname)} (not in GPTME_ANOMALY_ALLOWED_HOSTS). "
        "Add to allowlist to silence.",
    )


def _mark_rejected(tool_use: Any) -> None:
    """Remember that this tool call was blocked before it could execute."""
    now = time.monotonic()
    with _REJECTED_LOCK:
        for key in list(_rejected_calls):
            if now - _rejected_calls.get(key, now) > _REJECTED_TTL:
                _rejected_calls.pop(key, None)
        if len(_rejected_calls) >= 512:
            _rejected_calls.clear()
        _rejected_calls[id(tool_use)] = now


def _consume_rejected(tool_use: Any) -> bool:
    """Return True (once) if this call was blocked before execution."""
    with _REJECTED_LOCK:
        marked = _rejected_calls.pop(id(tool_use), None)
    return marked is not None and time.monotonic() - marked <= _REJECTED_TTL


def _detect_findings(tool_use: Any, workspace: Path | None) -> list[tuple[bool, str]]:
    """Run all enabled anomaly checks; return ``(blockable, message)`` pairs.

    ``blockable`` is False for a finding whose evidence cannot be trusted to
    stop the call (see ``_check_write_storm``); those are reported to the
    session but never turned into a confirmation skip.
    """
    findings: list[tuple[bool, str]] = []
    namespace = _tool_namespace(tool_use.tool)

    if namespace in _WRITE_TOOLS:
        result = _check_scope_escape(tool_use, workspace)
        blocked = False
        if result is not None:
            findings.append(result)
            blocked = result[0]
        # Don't pile a storm warning onto a call already being blocked for a
        # different reason. The storm window itself is only advanced by writes
        # that actually executed (see ``record_write``).
        if not blocked:
            result = _check_write_storm()
            if result is not None:
                findings.append(result)

    if namespace in _NETWORK_TOOLS:
        result = _check_novel_host(tool_use)
        if result is not None:
            findings.append(result)

    return findings


def _emit(
    anomaly_type: str, detail: str, *, blockable: bool = True
) -> tuple[bool, str]:
    """Log + return (should_block, message).

    ``blockable=False`` downgrades a finding to report-only even in block mode:
    used for checks whose evidence cannot distinguish one session from another,
    where blocking could stop a write this watchdog does not own.
    """
    msg = f"[anomaly_watchdog] {anomaly_type}: {detail}"
    logger.warning(msg)
    return (_mode() == "block") and blockable, msg


def check_tool_pre(
    data: ToolExecutePreData,
) -> Generator[Message, None, None]:
    """TOOL_EXECUTE_PRE hook: surface warn-mode findings as system messages.

    In block mode, blocking happens in the TOOL_CONFIRM hook (see
    ``anomaly_watchdog_confirm``) — TOOL_EXECUTE_PRE cannot prevent tool
    execution, it only stops lower-priority hooks.
    """
    tool_use = data.tool_use
    if tool_use is None or not _enabled():
        return

    findings = _detect_findings(tool_use, data.workspace)
    # In warn mode every finding is reported. In block mode the blockable ones
    # are the confirm hook's to enforce, but a report-only finding must still
    # reach the session — that hook can only return a skip, not a message.
    reportable = [
        msg for blockable, msg in findings if _mode() == "warn" or not blockable
    ]
    if not reportable:
        return

    from ..message import Message

    yield Message(
        "system",
        "⚠️ [anomaly_watchdog] Anomaly detected:\n" + "\n".join(reportable),
    )


def _result_was_blocked(result_msgs: tuple[Message, ...] | None) -> bool:
    """Whether a tool result is this watchdog's own skip message.

    Independent of the identity marker: the reason is a string this module
    owns, so it still identifies a blocked call if the ``ToolUse`` object is
    copied or reconstructed between the confirm and post hooks.
    """
    for msg in result_msgs or ():
        content = msg.content
        if isinstance(content, str) and content.startswith(_BLOCK_PREFIX):
            return True
    return False


def check_tool_post(
    data: ToolExecutePostData,
) -> Generator[Message, None, None]:
    """TOOL_EXECUTE_POST hook: record writes that actually executed.

    The post hook fires on the success path of every tool call, including one
    whose confirmation was skipped — so the window is only advanced once we
    know this watchdog did not reject the call.

    Known boundary: a write declined by a *later* confirmation hook (the
    interactive CLI/Server confirm) still reaches this hook and is counted.
    That is not observable from here — the confirmation result is not part of
    ``ToolExecutePostData`` — and the watchdog targets unattended runs, where
    nothing declines.
    """
    tool_use = data.tool_use
    if _enabled() and tool_use is not None:
        # Two independent signals: the identity marker the confirm hook left
        # (works even when no result is available) and this watchdog's own skip
        # text in the result (works whatever object reaches this hook).
        rejected = _consume_rejected(tool_use) or _result_was_blocked(data.result_msgs)
        if not rejected and _tool_namespace(tool_use.tool) in _WRITE_TOOLS:
            record_write()
    yield from ()


def anomaly_watchdog_confirm(
    tool_use: ToolUse,
    preview: str | None = None,
    workspace: Path | None = None,
) -> ConfirmationResult | None:
    """TOOL_CONFIRM hook: block flagged tool calls in block mode.

    Returns ``ConfirmationResult.skip(...)`` so the executor actually skips
    the tool (mirrors guardrails' enforce mode). Returns None otherwise to
    fall through to the next confirm hook.
    """
    if _mode() != "block":
        return None

    blockable = [
        msg for blockable, msg in _detect_findings(tool_use, workspace) if blockable
    ]
    if not blockable:
        return None

    from ..hooks.confirm import ConfirmationResult

    reason = _BLOCK_PREFIX + "\n" + "\n".join(blockable)
    logger.info("anomaly_watchdog (block): %s", reason)
    _mark_rejected(tool_use)
    return ConfirmationResult.skip(reason)


def register() -> None:
    """Register anomaly watchdog hooks."""
    # Apply config-based activation before deciding hook behavior, so that
    # [plugin.anomaly_watchdog] in gptme config enables the watchdog even
    # when this hook is registered through init_hooks (not the plugin
    # loader, which never runs for built-in hooks).
    try:
        from ..config import get_config

        _init_from_config(get_config())
    except Exception:  # fail-open: config problems must not break startup
        logger.debug("anomaly_watchdog config init failed", exc_info=True)

    register_hook(
        "anomaly_watchdog.tool_pre",
        HookType.TOOL_EXECUTE_PRE,
        check_tool_pre,
        priority=150,  # after guardrails (200), before confirm hooks
    )
    register_hook(
        "anomaly_watchdog.tool_post",
        HookType.TOOL_EXECUTE_POST,
        check_tool_post,
        priority=150,
    )
    register_hook(
        "anomaly_watchdog.confirm",
        HookType.TOOL_CONFIRM,
        anomaly_watchdog_confirm,
        priority=150,  # after guardrails (200), before cli/server confirm
    )
    logger.debug("Registered anomaly_watchdog hooks (mode=%s)", _mode())


def _init_from_config(config: object) -> None:
    """Enable via ``[plugin.anomaly_watchdog]`` in gptme config."""
    user_cfg = getattr(getattr(config, "user", None), "plugin", {}) or {}
    project = getattr(config, "project", None)
    project_cfg = getattr(project, "plugin", {}) or {} if project else {}

    merged: dict[str, object] = {}
    if isinstance(user_cfg, dict):
        merged.update(user_cfg.get("anomaly_watchdog", {}) or {})
    if isinstance(project_cfg, dict):
        merged.update(project_cfg.get("anomaly_watchdog", {}) or {})

    if (
        merged
        or (isinstance(user_cfg, dict) and "anomaly_watchdog" in user_cfg)
        or (isinstance(project_cfg, dict) and "anomaly_watchdog" in project_cfg)
    ):
        # Only default to warn when the config does not itself set a mode;
        # the setdefault in the loop below cannot override the "warn" seeded
        # here, so seeding unconditionally would turn configured block/off
        # into warn.
        if merged.get("mode") in (None, ""):
            os.environ.setdefault("GPTME_ANOMALY_WATCHDOG", "warn")

    # Precedence: an explicit environment variable always wins over config
    # (``setdefault``), so an operator can force the watchdog off or on for a
    # single run without editing config files.
    config_to_env: dict[str, str] = {
        "mode": "GPTME_ANOMALY_WATCHDOG",
        "allowed_dirs": "GPTME_ANOMALY_ALLOWED_DIRS",
        "allowed_hosts": "GPTME_ANOMALY_ALLOWED_HOSTS",
        "write_limit": "GPTME_ANOMALY_WRITE_LIMIT",
        "write_window": "GPTME_ANOMALY_WRITE_WINDOW",
    }
    for key, env_name in config_to_env.items():
        value = merged.get(key)
        if value not in (None, ""):
            os.environ.setdefault(env_name, str(value))


plugin = GptmePlugin(
    name="anomaly_watchdog",
    register_hooks=register,
    init=_init_from_config,
)
