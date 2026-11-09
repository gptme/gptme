"""Tests for TUI work-view: children/monitors overview and transcript viewer.

Covers:
- watch_snapshot: owner-scoped, branch-filtered, non-destructive
- Watch.outcome: fired/expired/cancelled set by the right code paths
- subagent_snapshot: owner-scoped filtering by parent_logdir/parent_branch
- WorkOverviewScreen: renders summary, opens/closes cleanly
- WorkScreen: renders child transcript (read-only, no lock acquisition)
"""

import json
import time
from pathlib import Path

import pytest

from gptme.tools.watch import Watch, _fire, _register, watch_snapshot

# ---------------------------------------------------------------------------
# watch_snapshot + Watch.outcome (no TUI needed)
# ---------------------------------------------------------------------------


def _make_watch(
    id: str, logdir: Path | None = None, branch: str = "main", kind: str = "timer"
) -> Watch:
    w = Watch(
        id=id,
        kind=kind,
        description=f"test watch {id}",
        created=time.time(),
        logdir=logdir,
        branch=branch,
    )
    _register(w)
    return w


def test_watch_snapshot_scopes_to_logdir(tmp_path):
    """Watches for a different conversation must not leak into the snapshot."""
    own_logdir = tmp_path / "conv-own"
    other_logdir = tmp_path / "conv-other"
    own_logdir.mkdir()
    other_logdir.mkdir()

    w_own = _make_watch("snap-own", logdir=own_logdir)
    w_other = _make_watch("snap-other", logdir=other_logdir)
    w_global = _make_watch("snap-global", logdir=None)

    result = watch_snapshot(own_logdir)
    ids = {w.id for w in result}
    assert w_own.id in ids, "owner's watch must appear"
    assert w_other.id not in ids, "other conversation's watch must not appear"
    # global (no logdir) only appears when queried with logdir=None
    assert w_global.id not in ids


def test_watch_snapshot_no_logdir_sees_all(tmp_path):
    """watch_snapshot(None) returns all watches (CLI/no-manager mode)."""
    ld = tmp_path / "any"
    ld.mkdir()
    w = _make_watch("snap-all-uniq", logdir=ld)
    result = watch_snapshot(None)
    assert any(x.id == w.id for x in result)


def test_watch_snapshot_does_not_drain_queue(tmp_path):
    """Calling watch_snapshot must not consume pending delivery queue entries."""
    from gptme.tools.watch import _pending_deliveries, _pending_lock

    ld = tmp_path / "nodequeue"
    ld.mkdir()
    w = _make_watch("snap-no-drain", logdir=ld)
    # Inject a fake pending delivery for this watch
    with _pending_lock:
        before = len(_pending_deliveries)
        _pending_deliveries.append((ld, w.id, "test-event", "main"))

    watch_snapshot(ld)  # must not touch the queue

    with _pending_lock:
        after = len(_pending_deliveries)
    assert after == before + 1, "snapshot must not drain pending deliveries"
    # Cleanup
    with _pending_lock:
        # Remove our injected entry
        for item in list(_pending_deliveries):
            if len(item) >= 2 and item[1] == w.id:
                _pending_deliveries.remove(item)
                break


def test_watch_outcome_fired():
    """Normal fire sets outcome='fired'."""
    w = _make_watch("outcome-fired", logdir=None)
    _fire(w, "condition met")
    assert w.outcome == "fired"


def test_watch_outcome_expired():
    """Expired deadline sets outcome='expired'."""
    w = _make_watch("outcome-expired", logdir=None)
    # Set an already-elapsed deadline
    w.deadline = time.time() - 1
    _fire(w, "expired without condition met", expired=True)
    assert w.outcome == "expired"


def test_watch_outcome_cancelled(tmp_path: Path) -> None:
    """The cancel command, not test-side state mutation, sets the outcome."""
    from gptme.logmanager import LogManager
    from gptme.logmanager.manager import _current_log_var
    from gptme.tools.watch import _watch

    manager = LogManager([], logdir=tmp_path / "cancel-outcome", lock=False)
    w = _make_watch("outcome-cancelled", logdir=manager.logdir)
    token = _current_log_var.set(manager)
    try:
        result = _watch(f"cancel {w.id}", None, None)
    finally:
        _current_log_var.reset(token)
    assert result.content == f"Cancelled watch {w.id}."
    assert w.cancelled
    assert w.cancel_event.is_set()
    assert w.outcome == "cancelled"


@pytest.mark.parametrize("terminal", ["fired", "expired", "cancelled"])
def test_watch_stream_event_keeps_watch_armed(
    terminal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stream progress must not preempt the eventual terminal outcome."""
    from gptme.logmanager.manager import _current_log_var
    from gptme.tools import watch as watch_mod

    delivered: list[str] = []
    monkeypatch.setattr(watch_mod, "_deliver", lambda w, text: delivered.append(text))
    w = _make_watch(f"outcome-progress-{terminal}", kind="stream")
    _fire(w, "progress line", once=False)
    assert delivered == ["progress line"]
    assert w.delivered == 1
    assert not w.fired
    assert w.outcome is None, "a stream line is not a terminal event"

    if terminal == "cancelled":
        token = _current_log_var.set(None)
        try:
            watch_mod._watch(f"cancel {w.id}", None, None)
        finally:
            _current_log_var.reset(token)
    else:
        _fire(w, "stream finished", expired=terminal == "expired")
        assert w.fired
    assert w.outcome == terminal


@pytest.mark.parametrize("kind", ["until", "run", "stream"])
def test_watch_worker_timeout_sets_expired(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deadline expiry inside a running worker must not look like success."""
    import shlex
    import subprocess
    import sys

    from gptme.tools import watch as watch_mod

    delivered: list[str] = []
    monkeypatch.setattr(watch_mod, "_deliver", lambda w, text: delivered.append(text))
    w = _make_watch(f"outcome-worker-timeout-{kind}", kind=kind)
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    popen = subprocess.Popen
    processes: list[subprocess.Popen[str]] = []

    def start_process(*args, **kwargs) -> subprocess.Popen[str]:
        proc = popen(*args, **kwargs)
        processes.append(proc)
        # Expire only AFTER launch: until's pre-probe check must not mask the
        # in-flight probe timeout path. No sleeps or scheduling races needed.
        w.deadline = time.time() - 1
        return proc

    monkeypatch.setattr(watch_mod.subprocess, "Popen", start_process)
    try:
        if kind == "until":
            watch_mod._poll_until(w, "exec " + shlex.join(command), every=1)
        else:
            proc = start_process(command, stdout=subprocess.PIPE, text=True)
            w.proc = proc
            worker = watch_mod._poll_run if kind == "run" else watch_mod._poll_stream
            worker(w, proc)
        assert w.outcome == "expired"
        assert w.fired
        assert not w.cancelled
        assert len(delivered) == 1
        assert "expired without" in delivered[0]
        assert processes and all(proc.poll() is not None for proc in processes)
    finally:
        for proc in processes:
            watch_mod._kill_proc(proc)
            watch_mod._close_stdout(proc)


def test_watch_outcome_not_overwritten_on_double_fire():
    """A second _fire call after outcome is set must not overwrite it."""
    w = _make_watch("outcome-no-overwrite", logdir=None)
    _fire(w, "first event")
    assert w.outcome == "fired"
    # second call with expired=True — should not overwrite the first outcome
    # (once=True default prevents the second fire from going through at all)
    _fire(w, "second event", expired=True)
    assert w.outcome == "fired", "outcome must not be changed on duplicate fire"


# ---------------------------------------------------------------------------
# subagent_snapshot (no TUI needed)
# ---------------------------------------------------------------------------


def test_subagent_snapshot_filters_by_parent_logdir(tmp_path):
    """subagent_snapshot must only return children whose parent_logdir matches."""
    from gptme.tools.subagent import subagent_snapshot
    from gptme.tools.subagent.types import Subagent, _subagents, _subagents_lock

    own_ld = (tmp_path / "parent-own").resolve()
    other_ld = (tmp_path / "parent-other").resolve()
    own_ld.mkdir()
    other_ld.mkdir()

    child_own = Subagent(
        agent_id="snap-child-own",
        prompt="task-own",
        thread=None,
        logdir=tmp_path / "child-own",
        model=None,
        parent_logdir=own_ld,
        parent_branch="main",
    )
    child_other = Subagent(
        agent_id="snap-child-other",
        prompt="task-other",
        thread=None,
        logdir=tmp_path / "child-other",
        model=None,
        parent_logdir=other_ld,
        parent_branch="main",
    )
    with _subagents_lock:
        _subagents.append(child_own)
        _subagents.append(child_other)

    try:
        result = subagent_snapshot(own_ld)
        ids = {sa.agent_id for sa in result}
        assert "snap-child-own" in ids
        assert "snap-child-other" not in ids
    finally:
        with _subagents_lock:
            _subagents[:] = [
                sa
                for sa in _subagents
                if sa.agent_id not in ("snap-child-own", "snap-child-other")
            ]


def test_subagent_snapshot_branch_filter(tmp_path):
    """When branch is given, only matching parent_branch entries are returned."""
    from gptme.tools.subagent import subagent_snapshot
    from gptme.tools.subagent.types import Subagent, _subagents, _subagents_lock

    ld = (tmp_path / "parent-branch").resolve()
    ld.mkdir()

    child_a = Subagent(
        agent_id="snap-branch-a",
        prompt="task",
        thread=None,
        logdir=tmp_path / "child-branch-a",
        model=None,
        parent_logdir=ld,
        parent_branch="feature-x",
    )
    child_b = Subagent(
        agent_id="snap-branch-b",
        prompt="task",
        thread=None,
        logdir=tmp_path / "child-branch-b",
        model=None,
        parent_logdir=ld,
        parent_branch="main",
    )
    with _subagents_lock:
        _subagents.append(child_a)
        _subagents.append(child_b)

    try:
        result = subagent_snapshot(ld, branch="feature-x")
        ids = {sa.agent_id for sa in result}
        assert "snap-branch-a" in ids
        assert "snap-branch-b" not in ids
    finally:
        with _subagents_lock:
            _subagents[:] = [
                sa
                for sa in _subagents
                if sa.agent_id not in ("snap-branch-a", "snap-branch-b")
            ]


# ---------------------------------------------------------------------------
# TUI tests (require textual)
# ---------------------------------------------------------------------------

pytest.importorskip("textual")

from gptme.logmanager import LogManager
from gptme.tui.app import GptmeApp, WorkOverviewScreen, WorkScreen


def _make_manager(tmp_path: Path, name: str = "conv") -> LogManager:
    return LogManager([], logdir=tmp_path / name, lock=False)


# --- WorkScreen ---


@pytest.mark.asyncio
async def test_work_screen_renders_empty_logdir(tmp_path):
    """WorkScreen with no conversation.jsonl shows a fallback message."""
    child_logdir = tmp_path / "child-empty"
    child_logdir.mkdir()
    screen = WorkScreen(child_logdir, "agent-empty")
    app = GptmeApp(_make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        app.push_screen(screen)
        await pilot.pause()
        from textual.widgets import Static

        content = app.screen.query_one("#work-screen-content", Static)
        rendered = str(content.render())
        # Should contain a fallback message, not crash
        assert "No log" in rendered or "log" in rendered.lower()


@pytest.mark.asyncio
async def test_work_screen_renders_jsonl(tmp_path):
    """WorkScreen reads conversation.jsonl and shows roles."""
    child_logdir = tmp_path / "child-with-log"
    child_logdir.mkdir()
    msgs = [
        {"role": "user", "content": "Hello from parent"},
        {"role": "assistant", "content": "Working on it"},
        {"role": "system", "content": "Task complete."},
    ]
    (child_logdir / "conversation.jsonl").write_text(
        "".join(json.dumps(m) + "\n" for m in msgs)
    )

    screen = WorkScreen(child_logdir, "agent-jsonl")
    app = GptmeApp(_make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        app.push_screen(screen)
        await pilot.pause()
        from textual.widgets import Static

        content_widget = app.screen.query_one("#work-screen-content", Static)
        # render() returns the Rich renderable as a string
        rendered = str(content_widget.render())
        assert "Hello from parent" in rendered
        assert "Working on it" in rendered


@pytest.mark.asyncio
async def test_work_screen_escape_returns_to_parent(tmp_path):
    """Pressing Escape from WorkScreen must return to parent without side effects."""
    child_logdir = tmp_path / "child-escape"
    child_logdir.mkdir()
    manager = _make_manager(tmp_path)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        app.push_screen(WorkScreen(child_logdir, "agent-escape"))
        await pilot.pause()
        assert len(app.screen_stack) == 2
        await pilot.press("q")
        await pilot.pause()
        assert len(app.screen_stack) == 1
    # Parent manager must be unchanged
    assert app.manager is manager


# --- WorkOverviewScreen ---


@pytest.mark.asyncio
@pytest.mark.parametrize("draft", ["", "keep this draft"])
async def test_work_shortcut_preserves_focused_input(
    tmp_path: Path, draft: str
) -> None:
    """Ctrl+W opens the overview instead of deleting a word from the draft."""
    from gptme.tui.app import ChatInput

    manager = _make_manager(tmp_path)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        parent = app.screen
        inp = app.query_one("#input", ChatInput)
        inp.text = draft
        inp.move_cursor((0, len(draft)))
        assert app.focused is inp
        await pilot.press("ctrl+w")
        await pilot.pause()
        assert isinstance(app.screen, WorkOverviewScreen)
        assert inp.text == draft
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is parent
        assert app.focused is inp
        assert inp.text == draft
        assert app.manager is manager


@pytest.mark.asyncio
async def test_work_overview_no_children_shows_message(tmp_path):
    """WorkOverviewScreen with no children/watches shows the empty message."""
    app = GptmeApp(_make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        manager = app.manager
        screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
        app.push_screen(screen)
        await pilot.pause()
        from textual.widgets import Static

        content = app.screen.query_one("#work-overview-content", Static)
        rendered = str(content.render())
        assert "(No children or monitors for this conversation)" in rendered


@pytest.mark.asyncio
async def test_work_overview_shows_children(tmp_path):
    """WorkOverviewScreen lists children whose parent_logdir matches the manager."""
    from gptme.tools.subagent.types import Subagent, _subagents, _subagents_lock

    manager = _make_manager(tmp_path)
    # Ensure logdir exists so subagent_snapshot can resolve it
    manager.logdir.mkdir(parents=True, exist_ok=True)

    child = Subagent(
        agent_id="overview-test-child",
        prompt="write tests [/tmp] [bold]literally[/bold]",
        thread=None,
        logdir=tmp_path / "child-overview",
        model=None,
        parent_logdir=manager.logdir.resolve(),
        parent_branch=manager.current_branch,
    )
    with _subagents_lock:
        _subagents.append(child)

    try:
        app = GptmeApp(manager, workspace=tmp_path)
        async with app.run_test() as pilot:
            screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
            app.push_screen(screen)
            await pilot.pause()
            from textual.widgets import Static

            content = app.screen.query_one("#work-overview-content", Static)
            rendered = str(content.render())
            assert "overview-test-child" in rendered
            assert "[/tmp] [bold]literally[/bold]" in rendered
    finally:
        with _subagents_lock:
            _subagents[:] = [
                sa for sa in _subagents if sa.agent_id != "overview-test-child"
            ]


@pytest.mark.asyncio
async def test_work_overview_shows_watch(tmp_path):
    """WorkOverviewScreen lists armed watches scoped to this conversation."""
    manager = _make_manager(tmp_path)
    manager.logdir.mkdir(parents=True, exist_ok=True)

    w = _make_watch(
        "overview-watch",
        logdir=manager.logdir.resolve(),
        branch=manager.current_branch,
        kind="timer",
    )
    # Give it a custom description
    w.description = "wait for CI to pass"

    try:
        app = GptmeApp(manager, workspace=tmp_path)
        async with app.run_test() as pilot:
            screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
            app.push_screen(screen)
            await pilot.pause()
            from textual.widgets import Static

            content = app.screen.query_one("#work-overview-content", Static)
            rendered = str(content.render())
            assert "overview-watch" in rendered
            assert "timer" in rendered
    finally:
        from gptme.tools.watch import _watches, _watches_lock

        with _watches_lock:
            _watches.pop("overview-watch", None)


@pytest.mark.asyncio
async def test_work_overview_unrelated_watches_excluded(tmp_path):
    """Watches for a different logdir must not appear in the overview."""
    manager = _make_manager(tmp_path)
    manager.logdir.mkdir(parents=True, exist_ok=True)

    other_ld = (tmp_path / "other-conv").resolve()
    other_ld.mkdir()
    _make_watch(
        "overview-other-watch",
        logdir=other_ld,
        branch="main",
        kind="until",
    )

    try:
        app = GptmeApp(manager, workspace=tmp_path)
        async with app.run_test() as pilot:
            screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
            app.push_screen(screen)
            await pilot.pause()
            from textual.widgets import Static

            content = app.screen.query_one("#work-overview-content", Static)
            rendered = str(content.render())
            assert "overview-other-watch" not in rendered
    finally:
        from gptme.tools.watch import _watches, _watches_lock

        with _watches_lock:
            _watches.pop("overview-other-watch", None)


@pytest.mark.asyncio
async def test_ctrl_w_opens_work_overview(tmp_path):
    """Ctrl+W binding (or action_show_work) opens WorkOverviewScreen without crashing."""
    manager = _make_manager(tmp_path)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        # Test by calling the action directly (ctrl+w may be captured by terminal)
        app.action_show_work()
        await pilot.pause()
        assert isinstance(app.screen, WorkOverviewScreen)
        await pilot.press("q")
        await pilot.pause()
        assert not isinstance(app.screen, WorkOverviewScreen)
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_work_screen_does_not_acquire_writer_lock(tmp_path):
    """Opening WorkScreen while a writer holds the child logdir lock must succeed.

    This verifies that WorkScreen never tries to acquire the conversation lock.
    We simulate a writer by creating the lock file and holding it exclusively.
    """
    import fcntl

    child_logdir = tmp_path / "child-lock-test"
    child_logdir.mkdir()
    msgs = [{"role": "assistant", "content": "working hard"}]
    (child_logdir / "conversation.jsonl").write_text(json.dumps(msgs[0]) + "\n")
    lock_file = child_logdir / ".lock"
    lock_file.touch()

    # Hold an exclusive lock (simulating a writer)
    lock_fd = lock_file.open("w")
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    try:
        manager = _make_manager(tmp_path)
        app = GptmeApp(manager, workspace=tmp_path)
        async with app.run_test() as pilot:
            app.push_screen(WorkScreen(child_logdir, "agent-locked"))
            await pilot.pause()
            # Must not hang or crash — WorkScreen never touches the lock
            from textual.widgets import Static

            content = app.screen.query_one("#work-screen-content", Static)
            assert content.render() is not None
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


@pytest.mark.asyncio
async def test_work_overview_selection_scrolls_and_opens_selected_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Navigation visibly selects the same child that Enter opens, even below the fold."""
    from textual.containers import VerticalScroll
    from textual.widgets import Static

    from gptme.tools.subagent.types import Subagent

    manager = _make_manager(tmp_path)
    children = []
    for index in range(30):
        logdir = tmp_path / f"child-{index}"
        logdir.mkdir()
        children.append(
            Subagent(
                agent_id=f"child-{index}",
                prompt="inspect logs",
                thread=None,
                logdir=logdir,
                model=None,
                parent_logdir=manager.logdir.resolve(),
                parent_branch=manager.current_branch,
            )
        )
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshot", lambda *a, **kw: list(children)
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
        app.push_screen(screen)
        await pilot.pause()
        content = screen.query_one("#work-overview-content", Static)
        assert "> " in str(content.render()).split("child-0")[0]
        await pilot.press("down")
        assert screen._cursor == 1
        await pilot.press("up")
        assert screen._cursor == 0
        for _ in range(25):
            screen.action_cursor_down()
        await pilot.pause()
        selected_line = next(
            line for line in str(content.render()).splitlines() if "child-25 " in line
        )
        assert selected_line.startswith("> ")
        scroll = screen.query_one("#work-overview-scroll", VerticalScroll)
        assert scroll.scroll_y > 0
        # The selected physical row is inside the viewport, not merely scrolled.
        from rich.text import Text

        lines = str(content.render()).splitlines()
        width = max(1, content.content_size.width)
        selected_row = sum(
            len(Text(line).wrap(app.console, width)) for line in lines[:26]
        )
        assert scroll.scroll_y <= selected_row < scroll.scroll_y + scroll.size.height
        children.insert(0, children.pop())
        await pilot.pause(1.2)
        assert screen._children[screen._cursor].agent_id == "child-25"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, WorkScreen)
        assert app.screen._logdir == tmp_path / "child-25"
        await pilot.press("q")
        await pilot.pause()
        assert app.screen is screen


@pytest.mark.asyncio
async def test_work_overview_refreshes_while_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A watch outcome changes onscreen without closing and reopening the modal."""
    from textual.widgets import Static

    manager = _make_manager(tmp_path)
    watch = Watch(
        id="live-watch",
        kind="until",
        description="wait [/tmp]",
        created=time.time(),
        logdir=manager.logdir,
        branch=manager.current_branch,
    )
    monkeypatch.setattr("gptme.tools.watch.watch_snapshot", lambda *a, **kw: [watch])
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
        app.push_screen(screen)
        await pilot.pause()
        content = screen.query_one("#work-overview-content", Static)
        assert "[armed]" in str(content.render())
        watch.outcome = "expired"
        watch.fired = True
        await pilot.pause(1.2)
        assert "[expired]" in str(content.render())
        assert "wait [/tmp]" in str(content.render())


@pytest.mark.asyncio
@pytest.mark.parametrize("child_count", [0, 1])
async def test_work_overview_scrolls_monitors_with_arrow_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child_count: int
) -> None:
    """Monitor rows remain keyboard-accessible with or without child agents."""
    from textual.containers import VerticalScroll

    from gptme.tools.subagent.types import Subagent

    manager = _make_manager(tmp_path)
    children = [
        Subagent(
            agent_id="child",
            prompt="inspect logs",
            thread=None,
            logdir=tmp_path / "child",
            model=None,
        )
        for _ in range(child_count)
    ]
    watches = [
        Watch(
            id=f"monitor-{index}",
            kind="timer",
            description="wait for an event",
            created=time.time(),
        )
        for index in range(30)
    ]
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshot", lambda *a, **kw: children
    )
    monkeypatch.setattr("gptme.tools.watch.watch_snapshot", lambda *a, **kw: watches)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
        app.push_screen(screen)
        await pilot.pause()
        scroll = screen.query_one("#work-overview-scroll", VerticalScroll)
        assert scroll.max_scroll_y > 0
        for _ in range(5):
            await pilot.press("down")
        await pilot.pause()
        previous_y = scroll.scroll_y
        assert previous_y > 0
        await pilot.press("up")
        await pilot.pause()
        assert scroll.scroll_y < previous_y
        assert screen._cursor == 0
