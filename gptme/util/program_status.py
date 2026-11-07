"""OSC 7501 Program Status Protocol reports for terminal consumers.

The wire format is supported by herdr-core. tmux needs an output consumer
(e.g. pipe-pane); passthrough alone does not expose reports to tmux formats.
Only fixed state/kind tokens are emitted, never prompts or tool arguments.
"""

import functools
import os
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal, ParamSpec

from ..message import get_output_format

State = Literal["idle", "working", "done", "blocked", "error", "clear"]
Kind = Literal["permission", "question", "auth"]
P = ParamSpec("P")
_current: ContextVar[tuple[State, Kind | None]] = ContextVar(
    "program_status", default=("clear", None)
)
# Set inside a chat session nested in another (e.g. an inline subagent run by
# a parent's tool call): the outer session owns the lifecycle reports.
_nested: ContextVar[bool] = ContextVar("program_status_nested", default=False)


def encode_report(state: State, kind: Kind | None = None) -> str:
    """Encode a report for stdout or a UI-owned terminal driver."""
    fields = f"state={state}:app=gptme"
    if state == "blocked" and kind:
        fields += f":kind={kind}"
    return f"\033]7501;{fields}\033\\"


def report(state: State, kind: Kind | None = None) -> None:
    """Report state on a TTY, unless explicitly disabled via the environment."""
    _current.set((state, kind))
    if (
        threading.current_thread() is not threading.main_thread()
        or not sys.stdout.isatty()
        or get_output_format() != "text"
        or os.environ.get("GPTME_PROGRAM_STATUS") == "0"
    ):
        return
    try:
        sys.stdout.write(encode_report(state, kind))
        sys.stdout.flush()
    except OSError:
        # Optional terminal metadata must not break the conversation.
        pass


@dataclass
class Scope:
    """A reporting scope; ``after`` may be changed before a normal exit."""

    after: State | None


@contextmanager
def program_status(
    state: State,
    kind: Kind | None = None,
    *,
    after: State | None = None,
    success_exceptions: tuple[type[BaseException], ...] = (),
) -> Iterator[Scope]:
    """Report a scope, restoring the enclosing state (or an explicit end state).

    Exceptions in a turn scope leave an error report; session scopes still
    restore their caller, including clear on exit from the outermost chat.
    """
    previous = _current.get()
    scope = Scope(after)
    report(state, kind)
    try:
        yield scope
    except BaseException as exc:
        if scope.after is not None:
            report(scope.after if isinstance(exc, success_exceptions) else "error")
        else:
            report(*previous)
        raise
    else:
        if scope.after is not None:
            report(scope.after)
        else:
            report(*previous)


@contextmanager
def session_status() -> Iterator[None]:
    """Own the terminal status for a chat session: idle, then clear on exit.

    A session started while another one is reporting (e.g. an inline subagent
    chat inside a parent's tool call) is nested: its idle and turn reports are
    skipped, so the parent keeps showing working. Blocking prompts in it are
    still reported, since the user has to answer them.
    """
    if _current.get()[0] == "clear":
        with program_status("idle"):
            yield
        return
    token = _nested.set(True)
    try:
        yield
    finally:
        _nested.reset(token)


@contextmanager
def turn_start() -> Iterator[None]:
    """Report working for work that starts a turn (e.g. turn.pre hooks).

    The turn's own scope (``turn_status``) reports its outcome; this ends
    idle on Ctrl+C and error on failure.
    """
    if _nested.get():
        yield
        return
    report("working")
    try:
        yield
    except KeyboardInterrupt:
        report("idle")
        raise
    except BaseException:
        report("error")
        raise


def turn_status(
    *, success_exceptions: tuple[type[BaseException], ...] = ()
) -> Callable[[Callable[P, bool]], Callable[P, bool]]:
    """Report a turn whose function returns whether it was cancelled or declined.

    A completed turn ends ``done``; a cancelled or declined one is back at the
    prompt waiting for the user, so it ends ``idle`` (as in the TUI). That
    includes a Ctrl+C escaping the turn (e.g. from a turn-end hook), which the
    caller handles by returning to the prompt.
    """

    def decorator(func: Callable[P, bool]) -> Callable[P, bool]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> bool:
            if _nested.get():
                return func(*args, **kwargs)
            with program_status(
                "working",
                after="done",
                success_exceptions=(*success_exceptions, KeyboardInterrupt),
            ) as scope:
                try:
                    interrupted = func(*args, **kwargs)
                except KeyboardInterrupt:
                    scope.after = "idle"
                    raise
                if interrupted:
                    scope.after = "idle"
                return interrupted

        return wrapper

    return decorator
