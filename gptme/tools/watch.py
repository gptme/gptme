"""Watch tool — arm event sources and get woken when they fire.

Claude Code Monitor + ScheduleWakeup parity: arm a probe/timer/stream/tmux
source, keep working, and receive one system message when it fires. Events
are delivered through the server wake path (``SessionManager``) when a
server session exists, else queued and drained at STEP_PRE.

Design: knowledge/design/2026-09-10-gptme-monitors-and-completion-events.md
"""

import logging
import queue
import re
import shlex
import subprocess
import threading
import time
from collections import deque
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

from ..message import Message
from ..util.ask_execute import execute_with_confirmation
from .base import ToolSpec
from .shell_validation import is_allowlisted, is_denylisted

logger = logging.getLogger(__name__)

# Storm limits (design §1.2)
_COALESCE_COUNT = 20
_COALESCE_WINDOW = 20.0
_CANCEL_COUNT = 200
_CANCEL_WINDOW = 5 * 60.0
_MAX_EVENTS = 1000
_POLL_INTERVAL = 1.0
# Bound in-flight stream lines so a firehose cannot grow the reader queue
# without limit while the consumer is applying storm guards.
_STREAM_LINE_QUEUE = 256
# `run` only reports a tail; never retain the child's full stdout.
_RUN_TAIL_CHARS = 1000

# Option names the watch parser consumes. Anything else starting with `--`
# belongs to the command being watched (e.g. `gh pr checks 42 --repo x`).
_WATCH_OPT_NAMES = frozenset({"every", "timeout", "pattern", "stable"})


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
    seen: int = 0  # every recorded event, including coalesced
    cancelled: bool = False
    fired: bool = False
    logdir: Path | None = None  # routing key: conversation logdir at arm time
    proc: subprocess.Popen | None = None  # set for run/stream; killed on cancel
    lock: threading.Lock = field(default_factory=threading.Lock)


_watches: dict[str, Watch] = {}
_watches_lock = threading.Lock()
_watch_seq = 0
# Offline fallback queue drained at STEP_PRE: (logdir, watch_id, text)
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


def _visible(watch: Watch, logdir: Path | None) -> bool:
    """True if `logdir`'s conversation may see/act on `watch`.

    Strictly per-conversation: a server process hosts many conversations and
    the global registry must not leak IDs, descriptions, or output across
    them. A `None` logdir means "no conversation context" (CLI arming without
    a manager), which sees everything.
    """
    return logdir is None or watch.logdir == logdir


def _get(watch_id: str, logdir: Path | None = None) -> Watch:
    with _watches_lock:
        watch = _watches.get(watch_id)
    if watch is None or not _visible(watch, logdir):
        # Same error whether it is unknown or another conversation's watch,
        # so the message does not confirm the ID exists elsewhere.
        raise ValueError(f"unknown watch id: {watch_id}")
    return watch


def _kill_proc(proc: subprocess.Popen | None) -> None:
    """Best-effort terminate then kill a child, so no worker outlives cancel."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        logger.debug("failed to kill watched process", exc_info=True)


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
        watch.seen += 1
        while len(watch.events) > 50:
            watch.events.popleft()
            watch.event_times.popleft()
        # Trim cancel window bookkeeping
        while watch.cancel_times and now - watch.cancel_times[0] > _CANCEL_WINDOW:
            watch.cancel_times.popleft()
        # Auto-cancel: runaway source. `seen` is lifetime total (design
        # ">1000 total"); `delivered` only counts events that actually fire.
        recent_cancel = len(watch.cancel_times)
        if recent_cancel > _CANCEL_COUNT or watch.seen > _MAX_EVENTS:
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
            # Conversation is busy (generating/tool-active); the event is
            # re-attempted at step completion by retry_deferred_watch_wakes,
            # and drained at STEP_PRE as a fallback.
        except Exception:
            logger.debug("server delivery failed for %s", watch.id, exc_info=True)
    with _pending_lock:
        _pending_deliveries.append((watch.logdir, watch.id, text))


def take_queued_watch_events(logdir: Path | None) -> list[tuple[str, str]]:
    """Pop offline-queued watch events for `logdir` (server retry path).

    Mirrors ``take_queued_completions``: the server re-attempts a live wake
    when a conversation goes idle, so an event that arrived mid-step is not
    stranded waiting for a turn that never starts.
    """
    returned: list[tuple[str, str]] = []
    with _pending_lock:
        retained = type(_pending_deliveries)()
        for item in _pending_deliveries:
            item_logdir, watch_id, text = item
            if logdir is None or item_logdir == logdir:
                returned.append((watch_id, text))
            else:
                retained.append(item)
        _pending_deliveries.clear()
        _pending_deliveries.extend(retained)
    return returned


def requeue_watch_event(logdir: Path | None, watch_id: str, text: str) -> None:
    """Put an event back at the head of the offline queue after a failed retry."""
    with _pending_lock:
        _pending_deliveries.appendleft((logdir, watch_id, text))


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
        # The per-invocation timeout is a hang guard, not a max runtime: a
        # slow-but-legitimate command must keep the watch polling, not kill it.
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=every * 4,
                check=False,
            )
        except subprocess.TimeoutExpired:
            time.sleep(every)
            continue
        if proc.returncode == 0:
            out = (proc.stdout or "").strip() or command
            _fire(watch, f"condition met: {out[:500]}")
            return
        time.sleep(every)


def _poll_stream(watch: Watch, proc: subprocess.Popen[str]) -> None:
    """Each stdout line is an event; process exit ends the watch.

    A reader thread decouples the blocking ``readline`` from the loop so the
    watch deadline is still enforced when the source goes quiet. The queue is
    bounded: if the consumer cannot keep up, the watch auto-cancels instead of
    buffering an unbounded firehose.
    """
    assert proc.stdout is not None
    stdout = proc.stdout
    lines: queue.Queue[str | None] = queue.Queue(maxsize=_STREAM_LINE_QUEUE)

    def _reader() -> None:
        try:
            for line in iter(stdout.readline, ""):
                if watch.cancelled:
                    return
                try:
                    lines.put(line, timeout=0.5)
                except queue.Full:
                    with watch.lock:
                        watch.cancelled = True
                    logger.warning(
                        "watch %s stream queue full; auto-cancelled", watch.id
                    )
                    return
        finally:
            # The end-of-stream sentinel must reach the consumer or the watch
            # hangs on lines.get() forever. Retry while the queue is full; the
            # consumer drains steadily, and if it cancelled the watch (queue
            # overflow), the loop exits without needing the sentinel.
            while not watch.cancelled:
                try:
                    lines.put(None, timeout=0.5)
                    break
                except queue.Full:
                    pass

    threading.Thread(target=_reader, daemon=True).start()
    while True:
        if watch.cancelled:
            break
        if watch.deadline is not None and time.time() > watch.deadline:
            _kill_proc(proc)
            _fire(watch, f"expired without event ({watch.description})")
            return
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            continue
        if line is None:
            break
        line = line.rstrip("\n")
        if line:
            _fire(watch, line, once=False)
    if watch.cancelled:
        _kill_proc(proc)
        return
    rc = proc.wait()
    _fire(watch, f"stream ended (rc={rc})")


def _poll_run(watch: Watch, proc: subprocess.Popen[str]) -> None:
    """Wait for a process to exit, keeping only a bounded output tail.

    Reading concurrently avoids the stdout-pipe deadlock; dropping older
    chunks keeps a verbose child's full output out of memory.
    """
    assert proc.stdout is not None
    stdout = proc.stdout
    tail = ""
    read_error: str | None = None
    buf_lock = threading.Lock()
    reader_done = threading.Event()

    def _reader() -> None:
        nonlocal tail, read_error
        try:
            while True:
                piece = stdout.read(4096)
                if not piece:
                    return
                with buf_lock:
                    tail = (tail + piece)[-_RUN_TAIL_CHARS:]
        except Exception as exc:
            read_error = str(exc)
            logger.debug("watch %s run reader failed", watch.id, exc_info=True)
        finally:
            reader_done.set()

    threading.Thread(target=_reader, daemon=True).start()
    while not reader_done.wait(timeout=0.2):
        if watch.cancelled:
            _kill_proc(proc)
            reader_done.wait(timeout=2)
            return
        if watch.deadline is not None and time.time() > watch.deadline:
            _kill_proc(proc)
            reader_done.wait(timeout=2)
            _fire(watch, f"expired without exit ({watch.description})")
            return
    if watch.cancelled:
        return
    if read_error:
        _kill_proc(proc)
        _fire(watch, f"watch errored: {read_error}")
        return
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _kill_proc(proc)
    with buf_lock:
        out = tail
    _fire(watch, f"process exited rc={proc.returncode} {out}".strip())


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
    for watch_id, text in take_queued_watch_events(manager_logdir):
        yield Message("system", f"Watch {watch_id} fired: {text}")


# --- duration parsing ------------------------------------------------------


def _parse_duration(text: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)?", text.strip())
    if not m:
        raise ValueError(f"invalid duration: {text!r} (use e.g. 30s, 5m)")
    value = float(m.group(1))
    return value * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}.get(m.group(2) or "s", 1)


def _parse_opts(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    """Split watch options from the command being watched.

    Only the known watch option names are consumed; any other ``--flag``
    (e.g. ``--repo`` on a ``gh pr checks`` probe) stays with the command.
    """
    opts: dict[str, str] = {}
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            name = tok[2:]
            if "=" in name:
                key, val = name.split("=", 1)
                if key in _WATCH_OPT_NAMES:
                    opts[key] = val
                    i += 1
                    continue
            elif (
                name in _WATCH_OPT_NAMES
                and i + 1 < len(tokens)
                and not tokens[i + 1].startswith("--")
            ):
                opts[name] = tokens[i + 1]
                i += 2
                continue
        rest.append(tok)
        i += 1
    return opts, rest


_SHELL_OPERATORS = frozenset(
    {"|", "||", "&&", "&", ";", ";;", ">", ">>", "<", "<<", "|&", "(", ")", "{", "}"}
)


def _join_command(positional: list[str]) -> str:
    """Join command tokens, rejecting shell operators.

    ``shlex.join`` would quote an operator token as a literal argument, so
    ``watch run echo ok > status.txt`` would silently not write the file and
    report a result for a different command than requested. Reject instead.
    """
    if any(tok in _SHELL_OPERATORS for tok in positional):
        raise ValueError(
            "watch commands do not support shell operators (pipes, redirections); "
            "watch a single command, or wrap the pipeline in a script"
        )
    return shlex.join(positional)


def _require_safe_command(command: str) -> None:
    """Reject commands the shell tool would deny, before arming a worker."""
    denied, reason, matched = is_denylisted(command)
    if denied:
        raise ValueError(f"Command denied: `{matched}`\n\n{reason}")


def _command_from_watch_content(content: str) -> str | None:
    """Return the inner shell command for ``run``/``until``/``stream``, else None."""
    try:
        tokens = shlex.split(content or "")
    except ValueError:
        return None
    if not tokens or tokens[0] not in ("run", "until", "stream"):
        return None
    _, positional = _parse_opts(tokens[1:])
    try:
        command = _join_command(positional)
    except ValueError:
        # Falls through to the normal confirmation path; `_watch` raises the
        # operator error to the user there.
        return None
    return command or None


def watch_allowlist_hook(
    tool_use,
    preview: str | None = None,
    workspace: Path | None = None,
):
    """Auto-approve allowlisted ``run``/``until``/``stream`` commands.

    Same allowlist as the shell tool, applied to the inner command (not the
    watch verb). Non-command verbs fall through; they never reach confirmation.
    """
    from ..hooks.confirm import ConfirmationResult

    if getattr(tool_use, "tool", None) != "watch":
        return None
    content = (preview or getattr(tool_use, "content", None) or "").strip()
    command = _command_from_watch_content(content)
    if command is None:
        return None
    # Resolve relative sensitive-path rules against the watched child's actual
    # working directory (it inherits the process cwd), not the conversation
    # logdir — the two can differ, and a logdir-relative check could
    # auto-approve a relative path the command does not actually resolve
    # against.
    if is_allowlisted(command, cwd=Path.cwd()):
        logger.debug("Watch command allowlisted, auto-confirming: %s", command[:50])
        return ConfirmationResult.confirm()
    return None


def _get_path_fn(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> Path | None:
    from ..logmanager import LogManager

    manager = LogManager.get_current_log()
    return manager.logdir if manager and manager.logdir else None


def _execute_confirmed(
    content: str, path: Path | None
) -> Generator[Message, None, None]:
    yield _watch(content, None, None)


def execute_watch(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> Generator[Message, None, None]:
    """Arm a watch, confirming ``run``/``until``/``stream`` like the shell tool."""
    content = (
        code if code is not None else (kwargs.get("content", "") if kwargs else "")
    )
    command = _command_from_watch_content(content or "")
    if command is not None:
        yield from execute_with_confirmation(
            code,
            args,
            kwargs,
            execute_fn=_execute_confirmed,
            get_path_fn=_get_path_fn,
            preview_fn=lambda preview, path: preview,
            preview_lang="bash",
            confirm_msg="Arm watch command?",
            allow_edit=True,
            confirmation_workspace=_get_path_fn(code, args, kwargs),
        )
        return
    yield _watch(code, args, kwargs)


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
            visible = [w for w in _watches.values() if _visible(w, logdir)]
            if not visible:
                return Message("system", "No armed watches.")
            lines = []
            for w in visible:
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
        w = _get(rest[0], logdir)
        with w.lock:
            w.cancelled = True
            proc = w.proc
        # A cancelled run/stream worker is blocked in communicate/readline;
        # kill the child so the daemon thread and its pipes are released.
        _kill_proc(proc)
        return Message("system", f"Cancelled watch {w.id}.")

    if verb == "wait":
        # Blocking wait for a fired event (design: the explicit escape hatch
        # from "keep working"). Observation-only: never kills the source.
        if not rest:
            raise ValueError("usage: watch wait <id> [timeout]")
        w = _get(rest[0], logdir)
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
        command = _join_command(positional)
        if not command:
            raise ValueError(f"usage: watch {verb} <command> [--every 30s] ...")
        _require_safe_command(command)
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
                errors="replace",
            )
            w.proc = proc
            return _spawn(w, _poll_run, proc)
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
            bufsize=1,
        )
        w.proc = proc
        return _spawn(w, _poll_stream, proc)

    if verb == "tmux":
        opts, positional = _parse_opts(rest)
        if not positional:
            raise ValueError("usage: watch tmux <session> [--pattern re] [--stable 5s]")
        session = positional[0]
        pattern = re.compile(opts["pattern"]) if "pattern" in opts else None
        stable = _parse_duration(opts["stable"]) if "stable" in opts else 0.0
        if pattern is None and stable <= 0:
            raise ValueError(
                "tmux watch needs --pattern <re> or --stable <duration>; "
                "without either it would poll forever without firing"
            )
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
        "- `watch list`\n"
        "- `watch cancel <id>`: disarm. For `run`/`stream`, also stop the child "
        "we spawned (otherwise the worker and pipes leak). `until`/`timer`/`tmux` "
        "have no long-lived child to kill.\n"
        "- `watch wait <id> [timeout]`: blocking observation-only wait; never kills.\n"
        "All options: --every, --timeout, --pattern, --stable (other `--flags` "
        "belong to the watched command). Prefer keep-working and let the event "
        "wake you. Never poll with `sleep N; check` — arm a watch instead. "
        "Runaway sources are auto-cancelled by storm guards. `run`/`until`/"
        "`stream` use the same confirmation and denylist as the shell tool "
        "(allowlisted probes auto-confirm; others prompt). `list`/`cancel`/"
        "`wait`/`timer`/`tmux` do not execute a user command, so they skip it."
    ),
    examples=(
        "> User: Wait for CI on PR 42 without blocking\n"
        "```watch\n"
        "until gh pr checks 42 --repo gptme/gptme --every 60s --timeout 30m\n"
        "```\n"
        "System: Watch w1 (until) fired: condition met: All checks passed"
    ),
    execute=execute_watch,
    block_types=["watch"],
    disabled_by_default=True,
    hooks={
        "drain_step_pre": ("step.pre", _watch_drain_hook, 940),
        "allowlist": ("tool.confirm", watch_allowlist_hook, 10),
    },
)

__doc__ = tool.get_doc(__doc__)

__all__ = ["tool", "Watch"]
