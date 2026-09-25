"""Tests for concurrent dispatch of all-read-only tool turns.

``execute_msg`` runs tool calls sequentially by default. When every runnable
call in an assistant message is marked ``read_only``, they are dispatched
concurrently instead: read-only tools hold no shared mutable state, so the
common "read A + read B + read C" turn can overlap.

These tests use fake read-only tools driven by a ``threading.Barrier`` so a
genuine overlap is distinguishable from "ran fast enough to look parallel".
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar

import pytest

from gptme.hooks import clear_hooks
from gptme.message import Message
from gptme.tools import clear_tools, execute_msg, get_tools, set_tools
from gptme.tools.base import ToolSpec, set_tool_format

# A ContextVar outside gptme's own registry: proves that an arbitrary variable
# set by the caller is visible inside the worker threads, not just the tool list.
_marker: ContextVar[str] = ContextVar("parallel_dispatch_marker", default="unset")


class _Probe:
    """Records overlap between tool executions."""

    def __init__(self, parties: int = 1, timeout: float = 5.0):
        self.barrier = threading.Barrier(parties, timeout=timeout)
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.started: list[str] = []

    def enter(self, name: str) -> None:
        with self.lock:
            self.started.append(name)
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def leave(self) -> None:
        with self.lock:
            self.active -= 1


def _make_spec(
    name: str,
    probe: _Probe,
    *,
    read_only: bool,
    sync: bool = False,
    delay: float = 0.0,
    fail: bool = False,
) -> ToolSpec:
    """Build a fake tool that reports its execution to ``probe``."""

    def execute(code=None, args=None, kwargs=None):
        probe.enter(name)
        try:
            if sync:
                # Blocks until its peer also arrives — impossible if the turn
                # is being executed one call at a time.
                probe.barrier.wait()
            if delay:
                time.sleep(delay)
            if fail:
                raise RuntimeError(f"{name} exploded")
            yield Message("system", f"{name} result")
        finally:
            probe.leave()

    return ToolSpec(
        name=name,
        desc=f"{name} fake tool",
        execute=execute,
        read_only=read_only,
    )


@pytest.fixture(autouse=True)
def _isolate_runtime():
    """Keep tool format, hooks and loaded tools local to each test."""
    saved_tools = list(get_tools())
    set_tool_format("tool")
    clear_hooks()
    clear_tools()
    yield
    clear_tools()
    set_tools(saved_tools)
    clear_hooks()
    set_tool_format("markdown")


def _call(tool: str, call_id: str) -> str:
    return f'@{tool}({call_id}): {{"x": "1"}}'


def test_all_readonly_turn_dispatches_concurrently():
    """Two read-only calls must be in flight at the same time."""
    probe = _Probe(parties=2)
    set_tools(
        [
            _make_spec("alpha", probe, read_only=True, sync=True),
            _make_spec("beta", probe, read_only=True, sync=True),
        ]
    )

    content = "\n".join([_call("alpha", "toolu_a1"), _call("beta", "toolu_b2")])
    results = list(execute_msg(Message("assistant", content)))

    assert probe.max_active == 2, (
        "read-only calls did not overlap (max concurrent = "
        f"{probe.max_active}); both peers waiting on the barrier never met"
    )
    contents = {m.content for m in results}
    assert "alpha result" in contents
    assert "beta result" in contents
    # Each structured call still gets exactly one paired result.
    assert sum(1 for m in results if m.call_id == "toolu_a1") == 1
    assert sum(1 for m in results if m.call_id == "toolu_b2") == 1


def test_mixed_turn_stays_sequential():
    """A write-bearing sibling must keep the whole turn sequential."""
    probe = _Probe()
    set_tools(
        [
            _make_spec("reader", probe, read_only=True, delay=0.1),
            _make_spec("writer", probe, read_only=False, delay=0.1),
        ]
    )

    content = "\n".join([_call("reader", "toolu_r1"), _call("writer", "toolu_w2")])
    results = list(execute_msg(Message("assistant", content)))

    assert probe.max_active == 1, "mixed turn must not overlap"
    assert probe.started == ["reader", "writer"], "mixed turn must keep call order"
    contents = {m.content for m in results}
    assert "reader result" in contents
    assert "writer result" in contents


def test_single_readonly_call_stays_sequential():
    """The parallel path needs at least two read-only calls."""
    probe = _Probe()
    set_tools([_make_spec("solo", probe, read_only=True)])

    results = list(execute_msg(Message("assistant", _call("solo", "toolu_s1"))))

    assert probe.max_active == 1
    assert any(m.content == "solo result" for m in results)


def test_results_are_yielded_in_call_order():
    """A slow first call must still be yielded before a fast second one."""
    probe = _Probe()
    set_tools(
        [
            _make_spec("slow", probe, read_only=True, delay=0.2),
            _make_spec("fast", probe, read_only=True, delay=0.0),
        ]
    )

    content = "\n".join([_call("slow", "toolu_slow"), _call("fast", "toolu_fast")])
    results = list(execute_msg(Message("assistant", content)))

    ordered = [m.content for m in results if m.content.endswith("result")]
    assert ordered == ["slow result", "fast result"]


def test_exception_in_one_parallel_call_is_paired():
    """A failing read-only call must not swallow or unbalance pairing."""
    probe = _Probe()
    set_tools(
        [
            _make_spec("bad", probe, read_only=True, fail=True),
            _make_spec("good", probe, read_only=True),
        ]
    )

    content = "\n".join([_call("bad", "toolu_bad"), _call("good", "toolu_good")])
    results = list(execute_msg(Message("assistant", content)))

    assert any(m.content == "good result" for m in results), (
        "the healthy sibling's result must still be yielded"
    )
    assert sum(1 for m in results if m.call_id == "toolu_bad") == 1
    assert sum(1 for m in results if m.call_id == "toolu_good") == 1


def test_parent_contextvars_are_visible_in_workers():
    """Worker threads must inherit the caller's contextvars.

    Worker threads start with an empty context, so without an explicit replay
    the loaded-tool registry (and anything else context-local) reads its
    default — the tools would appear not to be loaded at all.
    """
    probe = _Probe()

    def marker_tool_execute(code=None, args=None, kwargs=None):
        yield Message("system", f"marker={_marker.get()}")

    set_tools(
        [
            _make_spec("one", probe, read_only=True),
            ToolSpec(
                name="two",
                desc="marker reader",
                execute=marker_tool_execute,
                read_only=True,
            ),
        ]
    )
    _marker.set("propagated")

    content = "\n".join([_call("one", "toolu_1"), _call("two", "toolu_2")])
    results = list(execute_msg(Message("assistant", content)))

    assert any("marker=propagated" in m.content for m in results), (
        "contextvars set by the caller must be visible in the worker threads; "
        f"got {[m.content for m in results]}"
    )
