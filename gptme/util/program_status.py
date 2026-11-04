"""OSC 7501 Program Status Protocol reports for terminal consumers.

The wire format is supported by herdr-core. tmux needs an output consumer
(e.g. pipe-pane); passthrough alone does not expose reports to tmux formats.
Only fixed state/kind tokens are emitted, never prompts or tool arguments.
"""

import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from ..message import get_output_format

State = Literal["idle", "working", "done", "blocked", "error", "clear"]
Kind = Literal["permission", "question", "auth"]
_current: ContextVar[tuple[State, Kind | None]] = ContextVar(
    "program_status", default=("clear", None)
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


@contextmanager
def program_status(
    state: State,
    kind: Kind | None = None,
    *,
    after: State | None = None,
    success_exceptions: tuple[type[BaseException], ...] = (),
) -> Iterator[None]:
    """Report a scope, restoring the enclosing state (or an explicit end state).

    Exceptions in a turn scope leave an error report; session scopes still
    restore their caller, including clear on exit from the outermost chat.
    """
    previous = _current.get()
    report(state, kind)
    try:
        yield
    except BaseException as exc:
        if after is not None:
            report(after if isinstance(exc, success_exceptions) else "error")
        else:
            report(*previous)
        raise
    else:
        if after is not None:
            report(after)
        else:
            report(*previous)
