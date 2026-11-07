"""Cancellation must interrupt a thread agent draining its own child."""

import threading
import time
from pathlib import Path

import pytest

from gptme.tools.complete import SessionCompleteException
from gptme.tools.subagent import api
from gptme.tools.subagent.drain import DrainWindow, wait_for_owned_completion
from gptme.tools.subagent.types import (
    Subagent,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)
from gptme.util import notification_ready


@pytest.mark.parametrize("file_unavailable", [False, True])
def test_cancel_agent_while_draining_grandchild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_unavailable: bool
) -> None:
    logdir = tmp_path / "subagent-draining-random-suffix"
    logdir.mkdir()
    release_grandchild = threading.Event()
    stopped = threading.Event()
    outcome: list[BaseException | bool] = []
    window = DrainWindow(timeout=10)

    def drain() -> None:
        try:
            outcome.append(wait_for_owned_completion(logdir, window))
        except BaseException as exc:
            outcome.append(exc)
        finally:
            stopped.set()

    grandchild_thread = threading.Thread(
        target=release_grandchild.wait, args=(15,), daemon=True
    )
    agent_thread = threading.Thread(target=drain, daemon=True)
    agent = Subagent(
        agent_id="drain-cancel-agent",
        prompt="delegate and wait",
        thread=agent_thread,
        logdir=logdir,
        model=None,
        parent_logdir=tmp_path,
    )
    grandchild = Subagent(
        agent_id="drain-cancel-grandchild",
        prompt="still working",
        thread=grandchild_thread,
        logdir=logdir / "grandchild",
        model=None,
        parent_logdir=logdir,
    )
    if file_unavailable:

        def fail_write(*args: object, **kwargs: object) -> None:
            raise OSError("control file unavailable")

        monkeypatch.setattr(api, "append_control_op", fail_write)

    with _subagents_lock:
        _subagents.extend([agent, grandchild])
    grandchild_thread.start()
    agent_thread.start()
    try:
        # Wait until the real drain has opened its window, not just until the
        # thread starts. No model calls or replacement cancellation hooks.
        deadline = time.monotonic() + 2
        while window.deadline is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert window.deadline is not None
        assert api.subagent_cancel(agent.agent_id)
        assert agent.cancel_event.is_set() is file_unavailable
        assert stopped.wait(2), "cancelled agent remained blocked draining its child"
        assert len(outcome) == 1
        assert isinstance(outcome[0], SessionCompleteException)
        assert grandchild_thread.is_alive(), "cancellation must not wait for the child"
        with _subagent_results_lock:
            assert _subagent_results[agent.agent_id].status == "cancelled"
        with notification_ready._lock:
            assert logdir.resolve() not in notification_ready._listeners
    finally:
        release_grandchild.set()
        grandchild_thread.join(3)
        agent_thread.join(3)
        with _subagents_lock:
            _subagents.remove(agent)
            _subagents.remove(grandchild)
        with _subagent_results_lock:
            _subagent_results.pop(agent.agent_id, None)
            _subagent_results.pop(grandchild.agent_id, None)
    assert not agent_thread.is_alive()
