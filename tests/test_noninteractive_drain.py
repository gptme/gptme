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
    assert windows
    assert all(window is windows[0] for window in windows)
    assert windows[0].deadline is not None


@pytest.mark.parametrize("timeout", [None, "0"])
def test_default_exit_delivers_already_finished_child(tmp_path, monkeypatch, timeout):
    if timeout is not None:
        monkeypatch.setenv(DRAIN_TIMEOUT_ENV, timeout)
    manager = manager_at(tmp_path)
    child = Child("already-finished", manager)
    calls = []

    def step(*_a, **_kw):
        calls.append(1)
        assert len(calls) <= 2, "completion was delivered more than once"
        if len(calls) == 1:
            child.finish()
        else:
            assert completions_in(manager, child.agent_id) == 1
        yield Message("assistant", "done")

    monkeypatch.setattr(chat, "step", step)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_a: False)

    from gptme.tools.subagent import drain

    monkeypatch.setattr(
        drain,
        "wait_for_owned_completion",
        lambda *_a, **_kw: pytest.fail("disabled drain must not wait"),
    )

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
    assert calls == [1, 1]
    assert completions_in(manager, child.agent_id) == 1
    assert not has_owned_completion(manager.logdir)


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


@pytest.mark.parametrize("timeout", ["0", "0.1"])
def test_progress_queued_during_final_response_does_not_restart(
    tmp_path, monkeypatch, timeout
):
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, timeout)
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


@pytest.mark.parametrize("noise", ["progress", "other-branch"])
def test_stopped_child_grace_ignores_nonterminal_wakes(tmp_path, noise):
    manager = manager_at(tmp_path)
    child = Child("grace-noise", manager)
    child.release()

    def send_noise():
        if noise == "progress":
            notify_progress(child.agent_id, "working", parent_logdir=manager.logdir)
        else:
            notify_completion(
                "other-branch",
                "success",
                "not for this branch",
                parent_logdir=manager.logdir,
                parent_branch="other",
            )

    noise_timer = later(0.01, send_noise)
    terminal_timer = later(0.05, child.finish)
    try:
        assert wait_for_owned_completion(
            manager.logdir, DrainWindow(timeout=2), branch="main"
        )
        assert has_owned_completion(manager.logdir, "main")
    finally:
        noise_timer.join(5)
        terminal_timer.join(5)
        child.release()


def test_stopped_child_grace_is_not_extended_by_progress(tmp_path, monkeypatch):
    from gptme.tools.subagent import drain

    manager = manager_at(tmp_path)
    clock = [100.0]
    monkeypatch.setattr(drain.time, "monotonic", lambda: clock[0])

    class ProgressWake:
        def clear(self):
            pass

        def set(self):
            pass

        def wait(self, timeout):
            # Repeated progress wakes consume time without a terminal record.
            clock[0] += min(timeout, 0.01)
            return True

    monkeypatch.setattr(drain.threading, "Event", ProgressWake)
    assert not wait_for_owned_completion(manager.logdir, DrainWindow(timeout=2))
    assert clock[0] == pytest.approx(100.25)


def test_stopped_child_notification_grace_at_exit(tmp_path, monkeypatch):
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "2")
    manager = manager_at(tmp_path)
    child = Child("notify-after-exit", manager)
    child.release()
    timer = later(0.05, child.finish)
    try:
        _, generations, _ = run_noninteractive(manager, monkeypatch)
        assert generations == [1]
        assert completions_in(manager, child.agent_id) == 1
    finally:
        timer.join(5)
        child.release()


@pytest.mark.parametrize("closing", [False, True])
def test_shell_wait_delivers_only_timely_children(tmp_path, monkeypatch, closing):
    from gptme.hooks import current_conversation_id
    from gptme.tools import shell_background

    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "1" if closing else "0")
    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "false")
    manager = manager_at(tmp_path)
    first = Child("first", manager)
    if closing:
        first.finish()
    calls = []
    jobs = []
    timers = []
    other = None

    def step(*_a, **_kw):
        nonlocal other
        calls.append(1)
        assert len(calls) <= (3 if closing else 2)
        if len(calls) == 1:
            command = f"{shlex.quote(sys.executable)} -c 'import time; time.sleep(1.5); print(987654)'"
            jobs.append(shell_background.start_background_job(command))
            notify_progress("first", "working", parent_logdir=manager.logdir)
            if closing:
                other = Child("timely", manager)
                timers.append(later(0.1, other.finish))
        yield Message("assistant", "done")

    monkeypatch.setattr(chat, "step", step)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_a: False)
    token = current_conversation_id.set(manager.chat_id)
    shell_background.reset_background_jobs()
    try:
        with patch("gptme.tools.subagent.api.subagent_cancel"):
            chat._run_chat_loop(
                manager,
                [] if closing else [Message("user", "go")],
                False,
                interactive=False,
                logdir=manager.logdir,
            )
        assert jobs[0].process.poll() == 0
        assert any("987654" in m.content for m in manager.log)
        assert len(calls) == (3 if closing else 2)
        if closing:
            assert completions_in(manager, "timely") == 1
    finally:
        for timer in timers:
            timer.join(5)
        first.release()
        if other is not None:
            other.release()
        shell_background.reset_background_jobs()
        current_conversation_id.reset(token)


def test_closing_continuation_preserves_unrelated_loop_hooks(tmp_path, monkeypatch):
    from gptme.hooks import HookType, register_hook

    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "1")
    manager = manager_at(tmp_path)
    child = Child("hook-first", manager)
    child.finish()
    hook_calls = []

    def next_task(manager, **kwargs):
        hook_calls.append(1)
        if len(hook_calls) == 1:
            yield Message("user", "next independent task")

    register_hook("independent-task", HookType.LOOP_CONTINUE, next_task)
    _, generations, _ = run_noninteractive(manager, monkeypatch)
    assert generations == [1, 1]
    assert len(hook_calls) == 2
    assert any(m.content == "next independent task" for m in manager.log)


def test_steering_interrupts_drain_without_child_completion(tmp_path, monkeypatch):
    from gptme.prompt_queue import queue_prompt

    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "3")
    manager = manager_at(tmp_path)
    child = Child("steer-grandchild", manager)
    timer = later(
        0.05, lambda: queue_prompt(manager.logdir, "change course", steer=True)
    )

    def process(_n):
        child.release()

    try:
        elapsed, generations, _ = run_noninteractive(manager, monkeypatch, process)
        assert generations == [1]
        assert any(m.content == "change course" for m in manager.log)
        assert elapsed < 2, "accepted steering waited for the entire drain timeout"
    finally:
        timer.join(5)
        child.release()


def test_regular_prompt_resumes_with_pending_steering(tmp_path, monkeypatch):
    from gptme.prompt_queue import queue_prompt

    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "3")
    manager = manager_at(tmp_path)
    child = Child("steer-with-task", manager)
    seen = []

    def enqueue():
        queue_prompt(manager.logdir, "older request")
        queue_prompt(manager.logdir, "change course", steer=True)

    def step(*args, **kwargs):
        seen.append([m.content for m in manager.log])
        child.release()
        yield Message("assistant", "Followed latest instructions.")

    monkeypatch.setattr(chat, "step", step)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_a: False)
    timer = later(0.05, enqueue)
    try:
        chat._run_chat_loop(
            manager, [], False, interactive=False, logdir=manager.logdir
        )
        assert len(seen) == 1
        assert "older request" in seen[0]
        assert "change course" in seen[0], "first resumed step missed steering"
        assert seen[0].index("older request") < seen[0].index("change course")
    finally:
        timer.join(5)
        child.release()


def test_prompt_read_cannot_admit_completion_after_deadline(tmp_path, monkeypatch):
    from gptme.tools.subagent import drain

    manager = manager_at(tmp_path)
    now = [100.0]
    monkeypatch.setattr(drain.time, "monotonic", lambda: now[0])
    window = DrainWindow(timeout=10)

    def slow_prompt_read():
        now[0] = 111.0
        notify_completion("late", "success", "result", parent_logdir=manager.logdir)
        return False

    assert not wait_for_owned_completion(
        manager.logdir, window, prompt_ready=slow_prompt_read
    )
    assert window.deadline == 110.0
    assert has_owned_completion(manager.logdir), "late result must remain queued"


def test_queued_turns_cannot_admit_late_child_through_loop_hook(tmp_path, monkeypatch):
    from gptme.hooks import HookType, register_hook

    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "0.05")
    manager = manager_at(tmp_path)
    first = Child("queue-first", manager)
    first.finish()
    late_child = Child("queue-late", manager)
    hook_calls = []

    def next_tasks(manager, **kwargs):
        hook_calls.append(1)
        if len(hook_calls) == 1:
            yield Message("user", "independent one")
            yield Message("user", "independent two")

    def process(n):
        if n == 2:
            time.sleep(0.08)
            late_child.finish()

    register_hook("independent-tasks", HookType.LOOP_CONTINUE, next_tasks)
    try:
        _, generations, _ = run_noninteractive(manager, monkeypatch, process)
        assert generations == [1, 1, 1], "late child triggered an unbudgeted turn"
        assert completions_in(manager, "queue-late") == 0
        assert has_owned_completion(manager.logdir)
    finally:
        late_child.release()


@pytest.mark.parametrize("file_unavailable", [False, True])
@pytest.mark.parametrize("steer", [False, True])
def test_cancel_during_drain_does_not_run_queued_steering(
    tmp_path, monkeypatch, file_unavailable, steer
):
    from gptme.prompt_queue import queue_prompt
    from gptme.tools.complete import SessionCompleteException
    from gptme.tools.subagent import api, drain

    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "3")
    manager = manager_at(tmp_path)
    grandchild = Child("cancel-queued-grandchild", manager)
    agent = Subagent(
        agent_id="cancel-queued-agent",
        prompt="delegate",
        thread=threading.current_thread(),
        logdir=manager.logdir,
        model=None,
        parent_logdir=tmp_path,
    )
    with _subagents_lock:
        _subagents.append(agent)
    if file_unavailable:

        def fail_write(*args, **kwargs):
            raise OSError("control file unavailable")

        monkeypatch.setattr(api, "append_control_op", fail_write)

    original_check = drain._check_drain_cancellation
    checks = []

    def cancel_at_checkpoint(logdir):
        checks.append(1)
        if len(checks) == 1:
            queue_prompt(manager.logdir, "must not execute", steer=steer)
            api.subagent_cancel(agent.agent_id)
        original_check(logdir)

    monkeypatch.setattr(drain, "_check_drain_cancellation", cancel_at_checkpoint)
    calls = []

    def process(*args, **kwargs):
        calls.append(1)
        # Avoid waiting again on the broken path; the assertion checks that
        # cancellation reaches teardown without ANY additional model call.
        raise SessionCompleteException("unexpected queued generation")

    monkeypatch.setattr(chat, "_process_message_conversation", process)
    try:
        with pytest.raises(SessionCompleteException):
            chat._run_chat_loop(
                manager, [], False, interactive=False, logdir=manager.logdir
            )
        assert not calls, "cancelled drain resumed queued steering"
        with _subagent_results_lock:
            assert _subagent_results[agent.agent_id].status == "cancelled"
    finally:
        grandchild.release()
        with _subagent_results_lock:
            _subagent_results.pop(agent.agent_id, None)
