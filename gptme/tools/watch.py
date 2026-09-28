"""Watch tool — arm event sources and get woken when they fire.

Claude Code Monitor + ScheduleWakeup parity: arm a probe/timer/stream/tmux
source, keep working, and receive one system message when it fires. Events
are delivered through the server wake path (``SessionManager``) when a
server session exists, else queued and drained at STEP_PRE.

Design: knowledge/design/2026-09-10-gptme-monitors-and-completion-events.md
"""

import logging
import re
import shlex
import subprocess
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

from dataclasses import dataclass, field
from pathlib import Path

from ..message import Message
from .base import ToolSpec

logger = logging.getLogger(__name__)

# Storm limits (design §1.2)
_COALESCE_COUNT = 20
_COALESCE_WINDOW = 20.0
_CANCEL_COUNT = 200
_CANCEL_WINDOW = 5 * 60.0
_MAX_EVENTS = 1000
_POLL_INTERVAL = 1.0


@dataclass
class Watch:
    """An armed event source. Fired events are batched and delivered."""

    id: str
    kind: str  # run | until | stream | timer | tmux
    description: str
    created: float
    deadline: float | None = None  # absolute; None = session-length
    events: deque[str] = field(default_factory=deque)
    event_times: deque[float] = field(default_factory=deque)
    cancel_times: deque[float] = field(default_factory=deque)  # never trimmed to 50
    delivered: int = 0
    cancelled: bool = False
    fired: bool = False
    logdir: Path | None = None  # routing key: conversation logdir at arm time
    lock: threading.Lock = field(default_factory=threading.Lock)


_watches: dict[str, Watch] = {}
_watches_lock = threading.Lock()
_watch_seq = 0
# Offline fallback queue drained at STEP_PRE: (watch_id, text)
_pending_deliveries: deque[tuple[Path | None, str, str]] = deque()
_pending_lock = threading.Lock()


def _new_id() -> str:
    global _watch_seq
    with _watches_lock:
        _watch_seq += 1
        return f"w{_watch_seq}"


def _register(watch: Watch) -> None:
    with _watches_lock:
        _watches[watch.id] = watch


def _get(watch_id: str) -> Watch:
    with _watches_lock:
        if watch_id not in _watches:
            raise ValueError(f"unknown watch id: {watch_id}")
        return _watches[watch_id]


def _record_event(watch: Watch, text: str) -> bool:
    """Record an event and apply storm guards.

    Returns True if the event should be delivered, False if it was
    coalesced away or the watch was auto-cancelled.
    """
    now = time.time()
    with watch.lock:
        if watch.cancelled:
            return False
        watch.event_times.append(now)
        watch.cancel_times.append(now)
        watch.events.append(text)
        while len(watch.events) > 50:
            watch.events.popleft()
            watch.event_times.popleft()
        # Trim cancel window bookkeeping
        while watch.cancel_times and now - watch.cancel_times[0] > _CANCEL_WINDOW:
            watch.cancel_times.popleft()
        # Auto-cancel: runaway source
        recent_cancel = len(watch.cancel_times)
        if recent_cancel > _CANCEL_COUNT or watch.delivered > _MAX_EVENTS:
            watch.cancelled = True
            logger.warning("watch %s auto-cancelled by storm guard", watch.id)
            return False
        # Coalesce: too many events inside the window collapse to one
        recent_window = sum(1 for t in watch.event_times if now - t <= _COALESCE_WINDOW)
        if recent_window > _COALESCE_COUNT:
            # Replace the window's pending log with a summary marker
            first = (
                watch.events[-_COALESCE_COUNT].splitlines()[0][:200]
                if len(watch.events) >= _COALESCE_COUNT
                else text.splitlines()[0][:200]
            )
            watch.events.clear()
            watch.events.append(
                f"[{_COALESCE_COUNT}+ events in {_COALESCE_WINDOW:.0f}s "
                f"coalesced; first was: {first}]"
            )
            return False
        return True


def _deliver(watch: Watch, text: str) -> None:
    """Deliver a fired event: server wake if possible, else STEP_PRE queue."""
    conversation_id = watch.logdir.name if watch.logdir else None
    if conversation_id:
        try:
            from ..server.api_v2_common import WatchEvent
            from ..server.session_models import SessionManager

            SessionManager.add_event(
                conversation_id,
                WatchEvent(
                    type="watch_event",
                    kind=watch.kind,
                    status="fired",
                    ref=watch.id,
                    message=text,
                ),
            )
            woke = SessionManager.request_watch_wake(
                conversation_id,
                Message("system", f"Watch {watch.id} ({watch.kind}) fired: {text}"),
            )
            if woke:
                return
            # No live server session (CLI or idle-free) — fall through to
            # the STEP_PRE queue so the event still reaches the model.
        except Exception:
            logger.debug("server delivery failed for %s", watch.id, exc_info=True)
    with _pending_lock:
        _pending_deliveries.append((watch.logdir, watch.id, text))


def _fire(watch: Watch, text: str, once: bool = True) -> None:
    """Mark a watch fired (for `once` sources) and deliver."""
    with watch.lock:
        if watch.cancelled or (once and watch.fired):
            return
        watch.fired = watch.fired or once
    if not _record_event(watch, text):
        return
    watch.delivered += 1
    _deliver(watch, text)


# --- sources ---------------------------------------------------------------


def _poll_until(watch: Watch, command: str, every: float) -> None:
    """Poll a shell command until it exits 0; fire once."""
    while not watch.cancelled and not watch.fired:
        if watch.deadline is not None and time.time() > watch.deadline:
            _fire(watch, f"expired without condition met ({watch.description})")
            return
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=every * 4,
            check=False,
        )
        if proc.returncode == 0:
            out = (proc.stdout or "").strip() or command
            _fire(watch, f"condition met: {out[:500]}")
            return
        time.sleep(every)


def _poll_stream(watch: Watch, proc: subprocess.Popen[str]) -> None:
    """Each stdout line is an event; process exit ends the watch."""
    assert proc.stdout is not None
    for line in iter(proc.stdout.readline, ""):
        if watch.cancelled:
            break
        line = line.rstrip("\n")
        if line and _record_event(watch, line):
            _fire(watch, line, once=False)
    rc = proc.wait()
    if not watch.cancelled:
        _fire(watch, f"stream ended (rc={rc})")


def _poll_run(watch: Watch, proc: subprocess.Popen[str]) -> None:
    rc = proc.wait()
    if watch.cancelled:
        return
    tail = ""
    try:
        if proc.stdout is not None:
            tail = proc.stdout.read() or ""
            tail = tail[-1000:]
    except Exception:
        pass
    _fire(watch, f"process exited rc={rc} {tail}".strip())


def _poll_timer(watch: Watch, seconds: float) -> None:
    end = time.time() + seconds
    while not watch.cancelled and time.time() < end:
        time.sleep(min(_POLL_INTERVAL, max(0.1, end - time.time())))
    if not watch.cancelled:
        _fire(watch, f"timer elapsed ({watch.description})")


def _poll_tmux(
    watch: Watch, session: str, pattern: re.Pattern[str] | None, stable: float
) -> None:
    """Fire when pane output matches pattern or has been stable for `stable` s."""
    last = None
    last_change = time.time()
    while not watch.cancelled and not watch.fired:
        if watch.deadline is not None and time.time() > watch.deadline:
            _fire(watch, "expired without match")
            return
        try:
            proc = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", session],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            out = proc.stdout
        except Exception as exc:
            _fire(watch, f"tmux capture failed: {exc}")
            return
        if pattern is not None and pattern.search(out):
            line = next(
                (ln for ln in out.splitlines() if pattern.search(ln)), ""
            ).strip()
            _fire(watch, f"pattern matched: {line[:300]}")
            return
        if out != last:
            last = out
            last_change = time.time()
        elif stable > 0 and time.time() - last_change >= stable:
            _fire(watch, f"pane output stable for {stable:.0f}s")
            return
        time.sleep(0.5)


def _spawn(watch: Watch, target, *args) -> "Message":
    _register(watch)
    t = threading.Thread(target=_watch_thread, args=(watch, target, *args), daemon=True)
    t.start()
    return Message(
        "system",
        (
            f"Armed watch {watch.id} ({watch.kind}): {watch.description}. "
            "You will receive one system message when it fires; keep working "
            "in the meantime (no polling, no sleep). `watch list` to inspect."
        ),
    )


def _watch_thread(watch: Watch, target, *args) -> None:
    try:
        target(watch, *args)
    except Exception as exc:
        if not watch.cancelled:
            try:
                _fire(watch, f"watch errored: {exc}")
            except Exception:
                logger.exception("watch %s delivery failed", watch.id)


# --- delivery hook ---------------------------------------------------------


def _watch_drain_hook(manager, **kwargs) -> "Generator[Message, None, None]":
    """STEP_PRE: drain offline-delivered watch events as system messages.

    Only drains records routed to this manager's logdir, mirroring the
    subagent completion queue's per-conversation scoping.
    """

    manager_logdir = getattr(manager, "logdir", None)
    drained: list[tuple[str, str]] = []
    with _pending_lock:
        retained = type(_pending_deliveries)()
        for item in _pending_deliveries:
            logdir, watch_id, text = item
            if logdir is None or logdir == manager_logdir:
                drained.append((watch_id, text))
            else:
                retained.append(item)
        _pending_deliveries.clear()
        _pending_deliveries.extend(retained)
    for watch_id, text in drained:
        yield Message("system", f"Watch {watch_id} fired: {text}")


# --- duration parsing ------------------------------------------------------


def _parse_duration(text: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)?", text.strip())
    if not m:
        raise ValueError(f"invalid duration: {text!r} (use e.g. 30s, 5m)")
    value = float(m.group(1))
    return value * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}.get(m.group(2) or "s", 1)


def _parse_opts(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    opts: dict[str, str] = {}
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            name = tok[2:]
            if "=" in name:
                key, val = name.split("=", 1)
                opts[key] = val
                i += 1
                continue
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                opts[name] = tokens[i + 1]
                i += 2
            else:
                opts[name] = "true"
                i += 1
        else:
            rest.append(tok)
            i += 1
    return opts, rest


def _deadline(opts: dict[str, str]) -> float | None:
    if "timeout" in opts:
        return time.time() + _parse_duration(opts["timeout"])
    return None


# --- tool ------------------------------------------------------------------


def _watch(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> "Message":
    from ..logmanager import LogManager

    manager = LogManager.get_current_log()
    logdir = getattr(manager, "logdir", None) if manager else None
    tokens = shlex.split(code or "")
    if not tokens:
        raise ValueError(
            "usage: watch <run|until|stream|timer|tmux|list|cancel|wait> ..."
        )
    verb, *rest = tokens

    if verb == "list":
        with _watches_lock:
            if not _watches:
                return Message("system", "No armed watches.")
            lines = []
            for w in _watches.values():
                status = (
                    "cancelled" if w.cancelled else ("fired" if w.fired else "armed")
                )
                lines.append(
                    f"{w.id} [{w.kind}] {status}: {w.description} "
                    f"(delivered={w.delivered})"
                )
            return Message("system", "\n".join(lines))

    if verb == "cancel":
        if not rest:
            raise ValueError("usage: watch cancel <id>")
        w = _get(rest[0])
        w.cancelled = True
        return Message("system", f"Cancelled watch {w.id}.")

    if verb == "wait":
        # Non-blocking fallback: poll the watch until fired or timeout.
        if not rest:
            raise ValueError("usage: watch wait <id> [timeout]")
        w = _get(rest[0])
        timeout = _parse_duration(rest[1]) if len(rest) > 1 else 60.0
        end = time.time() + timeout
        while time.time() < end:
            with w.lock:
                if w.fired or w.cancelled:
                    break
            time.sleep(0.2)
        with w.lock:
            if w.cancelled:
                return Message("system", f"Watch {w.id} was cancelled.")
            if not w.fired:
                return Message(
                    "system",
                    f"Watch {w.id} has not fired within {timeout:.0f}s; still armed.",
                )
            return Message(
                "system", f"Watch {w.id} fired: {' | '.join(list(w.events)[-3:])}"
            )

    if verb == "timer":
        opts, positional = _parse_opts(rest)
        if not positional:
            raise ValueError("usage: watch timer <duration> [description]")
        seconds = _parse_duration(positional[0])
        w = Watch(
            id="",
            kind="timer",
            description=" ".join(positional[1:]) or f"timer {positional[0]}",
            created=time.time(),
            deadline=_deadline(opts),
            logdir=logdir,
        )
        w.id = _new_id()
        return _spawn(w, _poll_timer, seconds)

    if verb in ("run", "until", "stream"):
        if not rest:
            raise ValueError(f"usage: watch {verb} <command> [--every 30s] ...")
        opts, positional = _parse_opts(rest)
        command = " ".join(positional)
        w = Watch(
            id="",
            kind=verb,
            description=command,
            created=time.time(),
            deadline=_deadline(opts),
            logdir=logdir,
        )
        w.id = _new_id()
        if verb == "until":
            every = _parse_duration(opts.get("every", "30s"))
            return _spawn(w, _poll_until, command, every)
        if verb == "run":
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            return _spawn(w, _poll_run, proc)
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        return _spawn(w, _poll_stream, proc)

    if verb == "tmux":
        opts, positional = _parse_opts(rest)
        if not positional:
            raise ValueError("usage: watch tmux <session> [--pattern re] [--stable 5s]")
        session = positional[0]
        pattern = re.compile(opts["pattern"]) if "pattern" in opts else None
        stable = _parse_duration(opts["stable"]) if "stable" in opts else 0.0
        w = Watch(
            id="",
            kind="tmux",
            description=f"tmux {session} pattern={opts.get('pattern', '-')} "
            f"stable={stable:.0f}s",
            created=time.time(),
            deadline=_deadline(opts),
            logdir=logdir,
        )
        w.id = _new_id()
        return _spawn(w, _poll_tmux, session, pattern, stable)

    raise ValueError(
        f"unknown watch verb: {verb!r} "
        "(use run|until|stream|timer|tmux|list|cancel|wait)"
    )


tool = ToolSpec(
    name="watch",
    desc="Arm event sources (run/until/stream/timer/tmux) and get woken when they fire",
    instructions=(
        "Arm a watch to be woken by an event instead of polling or blocking.\n"
        "- `watch until <cmd> --every 30s`: fire once when cmd exits 0 (e.g. `gh pr checks`)\n"
        "- `watch run <cmd>`: fire when the process exits, with rc + output tail\n"
        "- `watch stream <cmd>`: each stdout line is an event (Monitor)\n"
        "- `watch timer 10m [desc]`: fire once after a duration (ScheduleWakeup)\n"
        "- `watch tmux <session> --pattern <re> --stable 5s`: fire on pane match/stability\n"
        "- `watch list` / `watch cancel <id>` / `watch wait <id> [timeout]`\n"
        "All options: --every, --timeout, --pattern, --stable. Never poll with "
        "`sleep N; check` — arm a watch instead. Runaway sources are "
        "auto-cancelled by storm guards."
    ),
    examples=(
        "> User: Wait for CI on PR 42 without blocking\n"
        "```watch\n"
        "until gh pr checks 42 --repo gptme/gptme --every 60s --timeout 30m\n"
        "```\n"
        "System: Watch w1 (until) fired: condition met: All checks passed"
    ),
    execute=_watch,
    block_types=["watch"],
    disabled_by_default=True,
    hooks={
        "drain_step_pre": ("step.pre", _watch_drain_hook, 940),
    },
)

__doc__ = tool.get_doc(__doc__)

__all__ = ["tool", "Watch"]
