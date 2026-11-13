"""Regressions for gptme/gptme#4896.

``subagent_cancel()`` queues a cancel op in ``control.jsonl`` and stops the
child. If the child stops before its next STEP_PRE, nothing drains the op, and
an ``allow_cancelled=True`` continuation used to consume it on its first step
and end without generating anything.
"""

import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gptme.message import Message
from gptme.tools.complete import SessionCompleteException
from gptme.tools.subagent import api, persistence, types
from gptme.tools.subagent.control import (
    CONTROL_FILENAME,
    append_control_op,
    discard_control_ops,
)
from gptme.tools.subagent.hooks import _subagent_control_hook


@pytest.fixture(autouse=True)
def clean_registry() -> Iterator[None]:
    def clear() -> None:
        with types._subagents_lock:
            types._subagents.clear()
        with types._subagent_results_lock:
            types._subagent_results.clear()
        types._registry_rehydrated = False

    clear()
    yield
    clear()


class _ChildLog:
    """Minimal stand-in for the child's LogManager at a STEP_PRE boundary."""

    def __init__(self, logdir: Path) -> None:
        self.logdir = logdir

    def append(self, msg: Message) -> None:
        with (self.logdir / "conversation.jsonl").open("a") as f:
            f.write(json.dumps(msg.to_dict()) + "\n")


def _run_child_step(logdir: Path, generated: list[str]) -> None:
    """Simulate the child's chat loop: STEP_PRE control hook, then generation.

    Mirrors ``chat()``: a ``SessionCompleteException`` from the hook ends the
    session before any assistant message is produced.
    """
    log = _ChildLog(logdir)
    try:
        for msg in _subagent_control_hook(log):  # type: ignore[arg-type]
            log.append(msg)
    except SessionCompleteException:
        return
    reply = "Done.\n\n```complete\nfinished new instruction\n```"
    log.append(Message("assistant", reply))
    generated.append(reply)


def _cancelled_subagent(logdir: Path, mode: str) -> types.Subagent:
    logdir.mkdir(exist_ok=True)
    original = [
        Message("user", "Run `sleep 600`"),
    ]
    with (logdir / "conversation.jsonl").open("w") as f:
        for msg in original:
            f.write(json.dumps(msg.to_dict()) + "\n")
    proc = None
    if mode == "subprocess":
        # The cancelled child has already been terminated.
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
    sa = types.Subagent(
        "worker",
        "Run `sleep 600`",
        None,
        logdir,
        None,
        execution_mode=mode,  # type: ignore[arg-type]
    )
    if proc is not None:
        object.__setattr__(sa, "process", proc)
    with types._subagents_lock:
        types._subagents.append(sa)
    persistence.persist_subagent_meta(sa)
    cancelled = types.ReturnType("cancelled", "Cancelled by orchestrator")
    assert types.set_subagent_result_if_absent("worker", cancelled)
    persistence.persist_subagent_outcome(sa, cancelled)
    # The op the old child never drained (cancelled before its next STEP_PRE).
    append_control_op(logdir, "cancel", agent_id="worker")
    return sa


def _wait_current(timeout: float = 10) -> types.Subagent:
    with types._subagents_lock:
        current = types._subagents[-1]
    assert current.thread is not None
    current.thread.join(timeout)
    assert not current.thread.is_alive()
    return current


def test_continue_requires_override_for_cancelled(tmp_path: Path) -> None:
    _cancelled_subagent(tmp_path / "subagent-worker", "thread")
    with pytest.raises(ValueError, match="allow_cancelled=True"):
        api.subagent_continue("worker", "finish now")


def test_thread_continuation_ignores_stale_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logdir = tmp_path / "subagent-worker"
    _cancelled_subagent(logdir, "thread")
    generated: list[str] = []

    def execute(**kwargs: Any) -> None:
        _run_child_step(kwargs["logdir"], generated)

    monkeypatch.setattr(api._exec, "_create_subagent_thread", execute)
    api.subagent_continue("worker", "Do not sleep again", allow_cancelled=True)
    current = _wait_current()

    assert generated, "continuation ended before any assistant generation"
    result = current.status()
    assert result.status == "success"
    assert str(result.result).startswith("finished new instruction")
    assert persistence.load_subagent_outcome(current) == result
    assert not (logdir / CONTROL_FILENAME).exists()


def test_subprocess_continuation_ignores_stale_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logdir = tmp_path / "subagent-worker"
    _cancelled_subagent(logdir, "subprocess")
    generated: list[str] = []
    control_at_launch: list[bool] = []

    def launch(**kwargs: Any) -> subprocess.Popen:
        lease = kwargs.get("writer_lease")
        assert lease is not None, "continuation must hold the writer lease"
        control_at_launch.append((kwargs["logdir"] / CONTROL_FILENAME).exists())
        # The resumed child's first STEP_PRE, then its generation.
        _run_child_step(kwargs["logdir"], generated)
        lease.close()
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc

    monkeypatch.setattr(api._exec, "_run_subagent_subprocess", launch)
    api.subagent_continue("worker", "Do not sleep again", allow_cancelled=True)
    current = _wait_current()

    assert control_at_launch == [False], "stale cancel op leaked into new attempt"
    assert generated, "continuation ended before any assistant generation"
    result = current.status()
    assert result.status == "success", result
    assert str(result.result).startswith("finished new instruction")


def test_fresh_cancel_still_stops_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logdir = tmp_path / "subagent-worker"
    _cancelled_subagent(logdir, "thread")
    started = threading.Event()
    proceed = threading.Event()
    generated: list[str] = []

    def execute(**kwargs: Any) -> None:
        started.set()
        assert proceed.wait(5)
        _run_child_step(kwargs["logdir"], generated)

    monkeypatch.setattr(api._exec, "_create_subagent_thread", execute)
    api.subagent_continue("worker", "Do not sleep again", allow_cancelled=True)
    assert started.wait(5)
    assert "cancelled" in api.subagent_cancel("worker")
    proceed.set()
    current = _wait_current()

    assert generated == []
    assert current.status().status == "cancelled"


def test_discard_control_ops_keeps_other_ops(tmp_path: Path) -> None:
    append_control_op(tmp_path, "cancel", agent_id="a")
    append_control_op(tmp_path, "other", value=1)
    append_control_op(tmp_path, "cancel", agent_id="a")
    assert discard_control_ops(tmp_path, "cancel") == 2
    lines = (tmp_path / CONTROL_FILENAME).read_text().splitlines()
    assert [json.loads(line)["op"] for line in lines] == ["other"]
    assert discard_control_ops(tmp_path, "other") == 1
    assert not (tmp_path / CONTROL_FILENAME).exists()
    assert discard_control_ops(tmp_path, "cancel") == 0
