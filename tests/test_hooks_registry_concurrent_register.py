"""HookRegistry.register sorts the shared hook list in place.

CPython's list.sort empties the list for the duration of the sort, so a
concurrent reader (another server/MCP thread sharing the same registry via
copy_context()) sees zero hooks. For TOOL_CONFIRM that means
get_confirmation() takes the "no confirmation hook registered" branch and
auto-confirms a call that the registered hook would have skipped.
"""

import contextvars
import threading

from gptme.hooks import (
    ConfirmAction,
    ConfirmationResult,
    HookType,
    clear_hooks,
    get_confirmation,
    register_hook,
    trigger_hook,
)
from gptme.hooks.types import Hook
from gptme.message import Message
from gptme.tools.base import ToolUse


def _run_during_concurrent_register(monkeypatch, fn):
    in_sort = threading.Event()
    release = threading.Event()
    orig_lt = Hook.__lt__

    def slow_lt(self, other):
        in_sort.set()
        release.wait(5)
        return orig_lt(self, other)

    monkeypatch.setattr(Hook, "__lt__", slow_lt)
    # Same sharing as the server's step threads: copy_context() carries the
    # same HookRegistry object into the worker thread.
    ctx = contextvars.copy_context()
    t = threading.Thread(
        target=ctx.run,
        args=(
            register_hook,
            "late_tool.hook",
            HookType.TOOL_CONFIRM,
            lambda *a, **k: None,
            -50,
        ),
    )
    t.start()
    assert in_sort.wait(5)
    try:
        return fn()
    finally:
        release.set()
        t.join(5)
        monkeypatch.setattr(Hook, "__lt__", orig_lt)


def test_get_confirmation_not_auto_confirmed_during_concurrent_register(monkeypatch):
    clear_hooks()
    try:
        register_hook(
            "deny_all",
            HookType.TOOL_CONFIRM,
            lambda tool_use, preview=None, workspace=None: ConfirmationResult.skip(
                "denied"
            ),
            priority=10,
        )
        tool_use = ToolUse(tool="shell", args=[], content="rm -rf /tmp/x")
        result = _run_during_concurrent_register(
            monkeypatch, lambda: get_confirmation(tool_use=tool_use)
        )
        assert result.action == ConfirmAction.SKIP, (
            f"registered deny hook bypassed: {result}"
        )
    finally:
        clear_hooks()


def test_trigger_sees_hooks_during_concurrent_register(monkeypatch):
    clear_hooks()
    try:
        register_hook(  # type: ignore[call-overload]
            "a_step_hook",
            HookType.TOOL_CONFIRM,
            lambda *a, **k: Message("system", "from hook"),
            priority=10,
        )
        msgs = _run_during_concurrent_register(
            monkeypatch, lambda: list(trigger_hook(HookType.TOOL_CONFIRM))
        )
        assert [m.content for m in msgs] == ["from hook"]
    finally:
        clear_hooks()
