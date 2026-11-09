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
from dataclasses import dataclass, field
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
# The turn scope a ``turn_status`` function is running in, so the turn itself
# can correct its end state (see ``mark_turn_cancelled``).
_turn_scope: ContextVar["Scope | None"] = ContextVar(
    "program_status_turn_scope", default=None
)


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
    # Set by mark_turn_cancelled / mark_turn_done to prevent the decorator's
    # interrupted→idle default from overriding an explicit outcome.
    _locked: bool = field(default=False)


def mark_turn_cancelled() -> None:
    """End the in-flight turn ``idle``: its work was cancelled, not completed.

    A turn can raise an exception that ``turn_status`` treats as a successful
    end (a completion signal), yet mean cancellation — ``DrainCancelled``
    subclasses ``SessionCompleteException``. Reporting ``done`` there tells
    terminal consumers the work finished, so the turn marks itself instead.
    """
    scope = _turn_scope.get()
    if scope is not None:
        scope.after = "idle"
        scope._locked = True


def mark_turn_done() -> None:
    """End the in-flight turn ``done``: a configured stop, not a user interrupt.

    Use this when a step limit (``GPTME_MAX_STEPS``) stops the turn — the agent
    ran to its configured budget, not user-interrupted, so the terminal consumer
    should see ``done`` rather than ``idle``.
    """
    scope = _turn_scope.get()
    if scope is not None:
        scope.after = "done"
        scope._locked = True


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
                token = _turn_scope.set(scope)
                try:
                    interrupted = func(*args, **kwargs)
                except KeyboardInterrupt:
                    scope.after = "idle"
                    raise
                finally:
                    _turn_scope.reset(token)
                if interrupted and not scope._locked:
                    scope.after = "idle"
                return interrupted

        return wrapper

    return decorator
