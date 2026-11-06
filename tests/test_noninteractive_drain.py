"""Non-interactive closing window for still-running owned subagents (#4549).

Children are real gated threads registered in the subagent registry; the
model call is a fixture. The wait must be event-driven, bounded by one fixed
deadline, and must never generate on progress.
"""

import logging
import shlex
import sys
import threading
import time
from unittest.mock import patch

import pytest

from gptme.hooks import clear_hooks
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools import clear_tools, init_tools
from gptme.tools.subagent.drain import (
    DRAIN_TIMEOUT_ENV,
    DrainWindow,
    drain_timeout,
    has_owned_completion,
    unfinished_owned_subagents,
    wait_for_owned_completion,
)
from gptme.tools.subagent.hooks import notify_completion, notify_progress
from gptme.tools.subagent.types import (
    ReturnType,
    Subagent,
    _completion_queue,
    _progress_queue,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)

chat = pytest.importorskip("gptme.chat")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_USER_CONFIG", str(tmp_path / "no-config.toml"))
    monkeypatch.delenv(DRAIN_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv("GPTME_MAX_STEPS", raising=False)
    for queue in (_completion_queue, _progress_queue):
        with queue.mutex:
            queue.queue.clear()
    with _subagents_lock:
        saved = list(_subagents)
    clear_hooks()
    clear_tools()
    init_tools(["subagent"], include_mcp=False)
    # _run_chat_loop is called directly, so chat()'s per-invocation reset of
    # the idle-autowake block does not run; an interrupt test must not leak it.
    idle_token = chat._idle_autowake_blocked.set(False)
    with patch("gptme.tools.subagent.hooks._notify_server", return_value=False):
        yield
    chat._idle_autowake_blocked.reset(idle_token)
    clear_hooks()
    clear_tools()
    with _subagents_lock:
        _subagents[:] = saved
    for queue in (_completion_queue, _progress_queue):
        with queue.mutex:
            queue.queue.clear()
    from gptme.util import notification_ready

    notification_ready._listeners.clear()


class Child:
    """A gated thread-mode child owned by ``parent``."""

    def __init__(self, agent_id: str, parent: LogManager):
        self.agent_id = agent_id
        self.parent_logdir = parent.logdir.resolve()
        self.gate = threading.Event()
        self.thread = threading.Thread(target=self.gate.wait, args=(30,), daemon=True)
        self.thread.start()
        with _subagent_results_lock:
            _subagent_results.pop(agent_id, None)
        with _subagents_lock:
            _subagents.append(
                Subagent(
                    agent_id=agent_id,
                    prompt="work",
                    thread=self.thread,
                    logdir=parent.logdir / agent_id,
                    model=None,
                    parent_logdir=self.parent_logdir,
                )
            )

    def finish(self, status="success", summary="child done"):
        notify_completion(
            self.agent_id, status, summary, parent_logdir=self.parent_logdir
        )
        self.gate.set()
        self.thread.join(5)

    def release(self):
        self.gate.set()
        self.thread.join(5)


def manager_at(tmp_path, name="parent"):
    return LogManager(
        [Message("user", "delegate"), Message("assistant", "Final parent text.")],
        logdir=tmp_path / name,
        lock=False,
    )


def run_noninteractive(manager, monkeypatch, process=None):
    """Run the real chat loop headless; return (elapsed, generations, cancelled)."""
    generations: list[int] = []
    cancelled: list[str] = []

    def fake_process(*_a, **_kw):
        generations.append(1)
        if process is not None:
            process(len(generations))
        manager.append(Message("assistant", f"follow-up {len(generations)}"))
        return False

    monkeypatch.setattr(chat, "_process_message_conversation", fake_process)
    start = time.monotonic()
    with patch(
        "gptme.tools.subagent.api.subagent_cancel",
        side_effect=lambda aid: cancelled.append(aid),
    ):
        chat._run_chat_loop(
            manager, [], False, interactive=False, logdir=manager.logdir
        )
    return time.monotonic() - start, generations, cancelled


def later(delay, fn):
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()
    return timer


def completions_in(manager, agent_id):
    return sum(f"Subagent '{agent_id}'" in m.content for m in manager.log)


@pytest.mark.parametrize("late_child", [False, True])
def test_closing_continuation_waits_for_background_shell(
    tmp_path, monkeypatch, late_child
):
    from gptme.hooks import current_conversation_id
    from gptme.tools import shell_background

    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "0.1")
    # The process-wide memory guard is unrelated to this lifecycle fixture.
    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "false")
    manager = manager_at(tmp_path)
    child = Child("first", manager)
    child.finish()
    jobs = []
    calls = []
    timers = []

    def step(*_a, **_kw):
        calls.append(1)
        assert len(calls) <= 2, "late child bypassed the drain deadline"
        if len(calls) == 1:
            command = f"{shlex.quote(sys.executable)} -c 'import time; time.sleep(0.3); print(123456)'"
            jobs.append(shell_background.start_background_job(command))
            if late_child:
                other = Child("late", manager)
                timers.append(later(0.15, other.finish))
        else:
            assert any("123456" in m.content for m in manager.log)
        yield Message("assistant", "done")

    monkeypatch.setattr(chat, "step", step)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_a: False)
    token = current_conversation_id.set(manager.chat_id)
    shell_background.reset_background_jobs()
    try:
        chat._run_chat_loop(
            manager, [], False, interactive=False, logdir=manager.logdir
        )
        assert calls == [1, 1]
        assert jobs[0].process.poll() == 0
        assert sum("Background shell job #" in m.content for m in manager.log) == 1
        if late_child:
            assert has_owned_completion(manager.logdir)
            assert completions_in(manager, "late") == 0
    finally:
        for timer in timers:
            timer.join(5)
        shell_background.reset_background_jobs()
        current_conversation_id.reset(token)


def test_default_exits_immediately_and_warns(tmp_path, monkeypatch, caplog, capsys):
    manager = manager_at(tmp_path)
    child = Child("slow", manager)
    try:
        with caplog.at_level(logging.WARNING):
            elapsed, generations, cancelled = run_noninteractive(manager, monkeypatch)
    finally:
        child.release()
    assert elapsed < 3.0
    assert generations == []
    assert cancelled == ["slow"]
    assert "[subagent]" in capsys.readouterr().out.replace("\n", " ")
    assert any(
        "cancelling 1 subagent(s)" in r.getMessage()
        and DRAIN_TIMEOUT_ENV in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.parametrize("status", ["success", "failure", "timeout"])
def test_window_delivers_terminal_event_once_and_continues(
    tmp_path, monkeypatch, status
):
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "10")
    manager = manager_at(tmp_path)
    child = Child("worker", manager)
    later(0.2, lambda: child.finish(status, "result payload"))
    elapsed, generations, cancelled = run_noninteractive(manager, monkeypatch)
    assert generations == [1]
    assert completions_in(manager, "worker") == 1
    assert cancelled == []
    assert not has_owned_completion(manager.logdir)
    # Child is gone, so the loop exits without spending the rest of the window.
    assert elapsed < 3.0


def test_multiple_children_each_delivered(tmp_path, monkeypatch):
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "10")
    manager = manager_at(tmp_path)
    first, second = Child("a", manager), Child("b", manager)
    later(0.1, first.finish)
    later(0.4, second.finish)
    _, generations, cancelled = run_noninteractive(manager, monkeypatch)
    assert completions_in(manager, "a") == 1
    assert completions_in(manager, "b") == 1
    assert 1 <= len(generations) <= 2
    assert cancelled == []


def test_progress_neither_generates_nor_extends_window(tmp_path, monkeypatch):
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "0.6")
    manager = manager_at(tmp_path)
    child = Child("chatty", manager)
    stop = threading.Event()

    def chatter():
        while not stop.wait(0.05):
            notify_progress(
                "chatty", "still working", parent_logdir=child.parent_logdir
            )

    threading.Thread(target=chatter, daemon=True).start()
    try:
        elapsed, generations, cancelled = run_noninteractive(manager, monkeypatch)
    finally:
        stop.set()
        child.release()
    assert generations == []
    assert cancelled == ["chatty"]
    # Endless progress would never end the wait if it extended the window.
    assert 0.5 <= elapsed < 3.0


def test_new_delegation_does_not_reset_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "2.0")
    manager = manager_at(tmp_path)
    first = Child("first", manager)
    spawned: list[Child] = []
    later(1.5, first.finish)

    def delegate_again(_n):
        spawned.append(Child("second", manager))

    try:
        elapsed, generations, cancelled = run_noninteractive(
            manager, monkeypatch, process=delegate_again
        )
    finally:
        for child in spawned:
            child.release()
    assert generations == [1]
    assert cancelled == ["second"]
    # A reset window would end at >= 3.5s; the fixed deadline ends near 2.0s.
    assert 1.9 <= elapsed < 3.2


def test_foreign_parent_record_neither_wakes_nor_is_consumed(tmp_path, monkeypatch):
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "0.4")
    manager = manager_at(tmp_path)
    foreign = (tmp_path / "foreign").resolve()
    child = Child("mine", manager)
    later(
        0.05,
        lambda: notify_completion("theirs", "success", "x", parent_logdir=foreign),
    )
    try:
        _, generations, cancelled = run_noninteractive(manager, monkeypatch)
    finally:
        child.release()
    assert generations == []
    assert cancelled == ["mine"]
    assert has_owned_completion(foreign)


def test_explicit_complete_skips_window(tmp_path, monkeypatch):
    from gptme.tools.complete import SessionCompleteException as Complete

    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "30")
    manager = manager_at(tmp_path)
    child = Child("slow", manager)

    def complete(*_a, **_kw):
        raise Complete("done")

    monkeypatch.setattr(chat, "_process_message_conversation", complete)
    start = time.monotonic()
    try:
        with pytest.raises(Complete):
            chat._run_chat_loop(
                manager,
                [Message("user", "finish")],
                False,
                interactive=False,
                logdir=manager.logdir,
            )
    finally:
        child.release()
    assert time.monotonic() - start < 5.0


def test_interactive_never_opens_window(monkeypatch):
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "30")
    assert chat._new_drain_window(interactive=True).timeout == 0
    assert chat._new_drain_window(interactive=False).timeout == 30


def test_wait_returns_on_signal_without_waiting_out_window(tmp_path):
    manager = manager_at(tmp_path)
    child = Child("fast", manager)
    later(0.1, child.finish)
    start = time.monotonic()
    assert wait_for_owned_completion(manager.logdir, DrainWindow(timeout=10))
    assert time.monotonic() - start < 5.0
    # The record is enqueued before the child thread exits; wait for exit.
    child.thread.join(5)
    assert unfinished_owned_subagents(manager.logdir) == []


def test_wait_skips_completion_for_other_branch(tmp_path):
    manager = manager_at(tmp_path)
    child = Child("branchy", manager)
    notify_completion(
        "branchy",
        "success",
        "x",
        parent_logdir=child.parent_logdir,
        parent_branch="other",
    )
    try:
        assert not wait_for_owned_completion(
            manager.logdir, DrainWindow(timeout=0.2), branch="main"
        )
        assert has_owned_completion(manager.logdir, "other")
    finally:
        child.release()


@pytest.mark.parametrize(
    ("env", "config", "expected"),
    [
        (None, None, 0.0),
        ("15", None, 15.0),
        ("bogus", None, 0.0),
        ("-1", None, 0.0),
        ("inf", None, 0.0),
        (None, 7, 7.0),
        ("3", 7, 3.0),
        (None, True, 0.0),
    ],
)
def test_drain_timeout_resolution(monkeypatch, env, config, expected):
    from gptme.config import get_config
    from gptme.config.models import ProjectConfig, SubagentConfig

    if env is not None:
        monkeypatch.setenv(DRAIN_TIMEOUT_ENV, env)
    cfg = get_config()
    monkeypatch.setattr(
        cfg,
        "project",
        ProjectConfig(subagent=SubagentConfig(drain_timeout=config)),
    )
    assert drain_timeout() == expected


def test_wait_is_interruptible_and_ctrl_c_closes_window(tmp_path, monkeypatch):
    """SIGINT only raises while interruptible; an interrupt ends the window."""
    from gptme.tools.subagent import drain
    from gptme.util.interrupt import _interruptible_var

    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "30")
    manager = manager_at(tmp_path)
    child = Child("slow", manager)
    seen: list[bool] = []

    def interrupted_wait(*_a, **_kw):
        seen.append(_interruptible_var.get())
        raise KeyboardInterrupt

    monkeypatch.setattr(drain, "wait_for_owned_completion", interrupted_wait)
    try:
        elapsed, generations, cancelled = run_noninteractive(manager, monkeypatch)
    finally:
        child.release()
    assert seen == [True]
    assert _interruptible_var.get() is False
    assert generations == []
    assert cancelled == ["slow"]
    assert elapsed < 5.0


def test_interrupted_queued_response_skips_window(tmp_path, monkeypatch):
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "30")
    manager = manager_at(tmp_path)
    child = Child("slow", manager)
    calls: list[int] = []

    def stopped(*_a, **_kw):
        calls.append(1)
        return True  # _process_message_conversation reports an interrupt

    monkeypatch.setattr(chat, "_process_message_conversation", stopped)
    start = time.monotonic()
    try:
        with patch("gptme.tools.subagent.api.subagent_cancel"):
            chat._run_chat_loop(
                manager,
                [Message("user", "go")],
                False,
                interactive=False,
                logdir=manager.logdir,
            )
    finally:
        child.release()
    assert calls == [1]
    assert time.monotonic() - start < 5.0


def test_last_answer_queued_before_exit_check_is_delivered(tmp_path, monkeypatch):
    """A child that already exited still has its queued answer delivered."""
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "10")
    manager = manager_at(tmp_path)
    child = Child("quick", manager)
    child.finish()
    assert unfinished_owned_subagents(manager.logdir) == []
    elapsed, generations, cancelled = run_noninteractive(manager, monkeypatch)
    assert generations == [1]
    assert completions_in(manager, "quick") == 1
    assert cancelled == []
    assert elapsed < 3.0


def test_other_branch_record_stays_queued_through_continuation(tmp_path, monkeypatch):
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "10")
    manager = manager_at(tmp_path)
    child = Child("main-child", manager)
    notify_completion(
        "side-child",
        "success",
        "belongs elsewhere",
        parent_logdir=child.parent_logdir,
        parent_branch="side",
    )
    later(0.1, child.finish)
    _, generations, _ = run_noninteractive(manager, monkeypatch)
    assert generations == [1]
    assert completions_in(manager, "main-child") == 1
    assert completions_in(manager, "side-child") == 0
    assert has_owned_completion(manager.logdir, "side")


def test_loop_hook_defaults_to_active_branch(tmp_path):
    from gptme.tools.subagent.hooks import _subagent_loop_completion_hook

    manager = manager_at(tmp_path)
    parent = manager.logdir.resolve()
    notify_completion("m", "success", "x", parent_logdir=parent, parent_branch="main")
    notify_completion("s", "success", "y", parent_logdir=parent, parent_branch="side")
    delivered = [
        m.content for m in _subagent_loop_completion_hook(manager, interactive=True)
    ]
    assert len(delivered) == 1 and "'m'" in delivered[0]
    assert has_owned_completion(parent, "side")


def test_queued_answer_opens_fixed_window_before_return(tmp_path, monkeypatch):
    from gptme.tools.subagent import drain

    manager = manager_at(tmp_path)
    now = [100.0]
    monkeypatch.setattr(drain.time, "monotonic", lambda: now[0])
    notify_completion("first", "success", "ready", parent_logdir=manager.logdir)
    window = DrainWindow(timeout=10)
    assert wait_for_owned_completion(manager.logdir, window)
    assert window.deadline == 110.0
    now[0] = 111.0
    # A continuation/re-delegation cannot admit even a queued answer after expiry.
    assert not wait_for_owned_completion(manager.logdir, window)
    assert has_owned_completion(manager.logdir)
    assert window.deadline == 110.0


@pytest.mark.parametrize(
    "window", [DrainWindow(timeout=0), DrainWindow(timeout=10, closed=True)]
)
def test_queued_answer_cannot_bypass_closed_window(tmp_path, window):
    manager = manager_at(tmp_path)
    notify_completion("ready", "success", "result", parent_logdir=manager.logdir)
    assert not wait_for_owned_completion(manager.logdir, window)
    assert has_owned_completion(manager.logdir)


@pytest.mark.parametrize("queued", [False, True])
def test_max_steps_stop_does_not_drain_or_restart(tmp_path, monkeypatch, queued):
    from gptme.tools.subagent import drain

    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "30")
    monkeypatch.setenv("GPTME_MAX_STEPS", "1")
    manager = manager_at(tmp_path)
    child = Child("limited", manager)
    calls = []

    def step(*_a, **_kw):
        calls.append(1)
        assert len(calls) == 1, "child completion restarted generation after step limit"
        if queued:
            child.finish()
        yield Message("assistant", "step complete")

    def unexpected_wait(*_a, **_kw):
        pytest.fail("explicit step-limit stop must not enter the drain")

    monkeypatch.setattr(chat, "step", step)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_a: False)
    monkeypatch.setattr(drain, "wait_for_owned_completion", unexpected_wait)
    try:
        chat._run_chat_loop(
            manager,
            [Message("user", "go")],
            False,
            interactive=False,
            logdir=manager.logdir,
        )
    finally:
        child.release()
    assert calls == [1]
    assert any((m.metadata or {}).get("max_steps_stop") for m in manager.log)


def test_queued_redelegation_after_deadline_does_not_restart(tmp_path, monkeypatch):
    from gptme.tools.subagent import drain

    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "10")
    manager = manager_at(tmp_path)
    first = Child("first", manager)
    first.finish()
    now = [100.0]
    monkeypatch.setattr(drain.time, "monotonic", lambda: now[0])

    def delegate_again(n):
        assert n == 1, "LOOP_CONTINUE bypassed the expired closing window"
        second = Child("second", manager)
        now[0] = 111.0
        second.finish()

    _, generations, _ = run_noninteractive(manager, monkeypatch, process=delegate_again)
    assert generations == [1]
    assert completions_in(manager, "first") == 1
    assert has_owned_completion(manager.logdir)


def test_initial_queued_completion_enters_drain_before_hook(tmp_path, monkeypatch):
    from gptme.tools.subagent import drain

    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "10")
    manager = manager_at(tmp_path)
    child = Child("initial", manager)
    calls = []
    windows = []
    original_wait = drain.wait_for_owned_completion

    def step(*_a, **_kw):
        calls.append(1)
        if len(calls) == 1:
            child.finish()
        yield Message("assistant", "done")

    def wait(logdir, window, **kwargs):
        windows.append(window)
        return original_wait(logdir, window, **kwargs)

    monkeypatch.setattr(chat, "step", step)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_a: False)
    monkeypatch.setattr(drain, "wait_for_owned_completion", wait)
    chat._run_chat_loop(
        manager,
        [Message("user", "go")],
        False,
        interactive=False,
        logdir=manager.logdir,
    )
    assert len(calls) == 2
    assert len(windows) == 1
    assert windows[0].deadline is not None


def test_cached_result_still_live_waits_for_notification(tmp_path):
    manager = manager_at(tmp_path)
    child = Child("cached", manager)
    with _subagent_results_lock:
        _subagent_results[child.agent_id] = ReturnType(status="success")
    timer = later(0.4, child.finish)
    try:
        assert wait_for_owned_completion(manager.logdir, DrainWindow(timeout=5))
    finally:
        timer.join(5)
        child.release()
        with _subagent_results_lock:
            _subagent_results.pop(child.agent_id, None)


def test_progress_queued_during_final_response_does_not_restart(tmp_path, monkeypatch):
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "0.1")
    manager = manager_at(tmp_path)
    child = Child("progress-only", manager)
    calls = []

    def step(*_a, **_kw):
        calls.append(1)
        assert len(calls) == 1, "queued progress restarted generation"
        notify_progress("progress-only", "working", parent_logdir=manager.logdir)
        yield Message("assistant", "done")

    monkeypatch.setattr(chat, "step", step)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_a: False)
    try:
        with patch("gptme.tools.subagent.api.subagent_cancel"):
            chat._run_chat_loop(
                manager,
                [Message("user", "go")],
                False,
                interactive=False,
                logdir=manager.logdir,
            )
    finally:
        child.release()
    assert calls == [1]
