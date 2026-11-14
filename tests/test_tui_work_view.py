"""Tests for TUI work-view: children/monitors overview and transcript viewer.

Covers:
- watch_snapshots: owner-scoped, branch-filtered, non-destructive
- Watch.status: fired/expired/cancelled set by the right code paths
- subagent_snapshots: owner-scoped filtering by parent_logdir/parent_branch
- WorkOverviewScreen: renders summary, opens/closes cleanly
- WorkScreen: renders child transcript (read-only, no lock acquisition)
"""

import json
import time
from pathlib import Path

import pytest

from gptme.tools.watch import (
    Watch,
    _expire,
    _fire,
    _register,
    watch_snapshots,
)

# ---------------------------------------------------------------------------
# watch_snapshots + Watch.status (no TUI needed)
# ---------------------------------------------------------------------------


def _make_child_snapshot(
    agent_id: str, logdir: Path | None = None, prompt: str = "task"
):
    """Create a SubagentSnapshot for use in TUI tests that monkeypatch subagent_snapshots."""
    from gptme.tools.subagent.api import SubagentSnapshot

    return SubagentSnapshot(
        run_id=str(logdir or f"/tmp/child-{agent_id}"),
        agent_id=agent_id,
        status="running",
        started_at=time.time(),
        elapsed_s=0,
        execution_mode="thread",
        prompt_preview=prompt[:60],
        logdir=logdir or Path(f"/tmp/child-{agent_id}"),
    )


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
    _make_watch("snap-global", logdir=None)

    result = watch_snapshots(own_logdir)
    ids = {w.id for w in result}
    assert w_own.id in ids, "owner's watch must appear"
    assert w_other.id not in ids, "other conversation's watch must not appear"


def test_watch_snapshot_does_not_drain_queue(tmp_path):
    """Calling watch_snapshots must not consume pending delivery queue entries."""
    from gptme.tools.watch import _pending_deliveries, _pending_lock

    ld = tmp_path / "nodequeue"
    ld.mkdir()
    w = _make_watch("snap-no-drain", logdir=ld)
    # Inject a fake pending delivery for this watch
    with _pending_lock:
        before = len(_pending_deliveries)
        _pending_deliveries.append((ld, w.id, "test-event", "main"))

    watch_snapshots(ld)  # must not touch the queue

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


def test_watch_status_fired():
    """Normal fire sets status='fired'."""
    w = _make_watch("outcome-fired", logdir=None)
    _fire(w, "condition met")
    assert w.status == "fired"


def test_watch_status_expired():
    """Expired deadline sets status='expired'."""
    w = _make_watch("outcome-expired", logdir=None)
    # Set an already-elapsed deadline
    w.deadline = time.time() - 1
    _expire(w, "expired without condition met")
    assert w.status == "expired"


def test_watch_status_cancelled(tmp_path: Path) -> None:
    """The cancel command, not test-side state mutation, sets the status."""
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
    assert w.status == "cancelled"


@pytest.mark.parametrize("terminal", ["fired", "expired", "cancelled"])
def test_watch_stream_event_keeps_watch_armed(
    terminal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stream progress must not preempt the eventual terminal status."""
    from gptme.logmanager.manager import _current_log_var
    from gptme.tools import watch as watch_mod

    delivered: list[str] = []
    monkeypatch.setattr(
        watch_mod, "_deliver", lambda w, text, **kw: delivered.append(text)
    )
    w = _make_watch(f"outcome-progress-{terminal}", kind="stream")
    _fire(w, "progress line", once=False)
    assert delivered == ["progress line"]
    assert w.delivered == 1
    assert not w.fired
    assert w.status == "armed", "a stream line is not a terminal event"

    if terminal == "cancelled":
        token = _current_log_var.set(None)
        try:
            watch_mod._watch(f"cancel {w.id}", None, None)
        finally:
            _current_log_var.reset(token)
    else:
        if terminal == "expired":
            _expire(w, "stream finished")
        else:
            _fire(w, "stream finished")
        assert w.fired
    assert w.status == terminal


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
    monkeypatch.setattr(
        watch_mod, "_deliver", lambda w, text, **kw: delivered.append(text)
    )
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
        assert w.status == "expired"
        assert w.fired
        assert not w.cancelled
        assert len(delivered) == 1
        assert "expired without" in delivered[0]
        assert processes and all(proc.poll() is not None for proc in processes)
    finally:
        for proc in processes:
            watch_mod._kill_proc(proc)
            watch_mod._close_stdout(proc)


def test_watch_status_not_overwritten_on_double_fire():
    """A second _expire call after fired must not overwrite the fired status."""
    w = _make_watch("outcome-no-overwrite", logdir=None)
    _fire(w, "first event")
    assert w.status == "fired"
    # _expire returns early when watch.fired is already True
    _expire(w, "second event")
    assert w.status == "fired", "status must not be changed once fired"


# ---------------------------------------------------------------------------
# subagent_snapshots (no TUI needed)
# ---------------------------------------------------------------------------


def test_subagent_snapshot_filters_by_parent_logdir(tmp_path):
    """subagent_snapshots must only return children whose parent_logdir matches."""
    from gptme.tools.subagent import subagent_snapshots
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
        result = subagent_snapshots(own_ld)
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
    from gptme.tools.subagent import subagent_snapshots
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
        result = subagent_snapshots(ld, parent_branch="feature-x")
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
        assert "No log" in rendered


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


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_work_screen_preserves_unicode_separators(
    tmp_path: Path, separator: str
) -> None:
    """JSON string separators are content, not JSONL record boundaries."""
    message = {"role": "assistant", "content": f"before{separator}after"}
    (tmp_path / "conversation.jsonl").write_text(
        json.dumps(message, ensure_ascii=False) + "\n"
    )
    transcript = WorkScreen(tmp_path, "unicode-child")._load_transcript()
    assert f"assistant: before{separator}after" in transcript


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
@pytest.mark.parametrize("inline", [False, True], ids=["fullscreen", "inline"])
@pytest.mark.parametrize("draft", ["", "keep this draft", "line one\nline two"])
async def test_work_shortcut_preserves_focused_input(
    tmp_path: Path, draft: str, inline: bool
) -> None:
    """Ctrl+T opens the overview from the focused input without editing the draft."""
    from gptme.tui.app import ChatInput

    manager = _make_manager(tmp_path)
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    async with app.run_test() as pilot:
        parent = app.screen
        inp = app.query_one("#input", ChatInput)
        inp.text = draft
        inp.move_cursor(inp.document.end)
        assert app.focused is inp
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert isinstance(app.screen, WorkOverviewScreen)
        assert inp.text == draft
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is parent
        assert app.focused is inp
        assert inp.text == draft
        assert app.manager is manager


@pytest.mark.parametrize(
    ("key", "before", "cursor", "after"),
    [
        # Word deletion: Ctrl+W is what (some) macOS terminals send for
        # Cmd/Option+Backspace, so it must stay an editing key (#4899).
        ("ctrl+w", "alpha beta gamma", (0, 11), "alpha gamma"),
        ("ctrl+backspace", "alpha beta gamma", (0, 11), "alpha gamma"),
        ("alt+backspace", "alpha beta gamma", (0, 11), "alpha gamma"),
        # Line deletion (Ghostty maps Cmd+Backspace to Ctrl+U by default).
        ("ctrl+u", "alpha beta gamma", (0, 11), "gamma"),
        ("super+backspace", "alpha beta gamma", (0, 11), "gamma"),
        ("ctrl+k", "alpha beta gamma", (0, 11), "alpha beta "),
        # Plain character deletion.
        ("backspace", "alpha beta gamma", (0, 11), "alpha betagamma"),
        ("delete", "alpha beta gamma", (0, 11), "alpha beta amma"),
        # Multiline drafts: word deletion only touches the cursor's line.
        ("ctrl+w", "first line\nalpha beta gamma", (1, 11), "first line\nalpha gamma"),
        ("ctrl+u", "first line\nalpha beta gamma", (1, 11), "first line\ngamma"),
    ],
)
@pytest.mark.asyncio
async def test_draft_editing_keys_edit_instead_of_opening_work(
    tmp_path: Path, key: str, before: str, cursor: tuple[int, int], after: str
) -> None:
    """Editing keys reach the draft via real key dispatch, never the work pane."""
    from gptme.tui.app import ChatInput

    app = GptmeApp(_make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        parent = app.screen
        inp = app.query_one("#input", ChatInput)
        inp.text = before
        inp.move_cursor(cursor)
        assert app.focused is inp
        await pilot.press(key)
        await pilot.pause()
        assert app.screen is parent
        assert not isinstance(app.screen, WorkOverviewScreen)
        assert app.focused is inp
        assert inp.text == after


def test_priority_app_bindings_do_not_steal_editor_keys() -> None:
    """App-level priority bindings must not shadow the draft editor's keys.

    A priority binding fires before the focused ChatInput sees the key, so a
    collision silently disables that editing command (Ctrl+W, #4899).
    Ctrl+C is the deliberate exception: interrupt/quit must always win.
    """
    from textual.binding import Binding

    from gptme.tui.app import ChatInput

    def keys(bindings) -> set[str]:
        out: set[str] = set()
        for b in bindings:
            key = b.key if isinstance(b, Binding) else b[0]
            out.update(k.strip() for k in key.split(","))
        return out

    editor_keys: set[str] = set()
    for cls in ChatInput.__mro__:
        editor_keys |= keys(cls.__dict__.get("BINDINGS", []))
    priority_keys = keys(
        b for b in GptmeApp.BINDINGS if isinstance(b, Binding) and b.priority
    )
    assert priority_keys & editor_keys == {"ctrl+c"}


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
    # Ensure logdir exists so subagent_snapshots can resolve it
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

            monitors = app.screen.query_one("#work-overview-monitors", Static)
            rendered = str(monitors.render())
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

            rendered = "\n".join(
                str(app.screen.query_one(view_id, Static).render())
                for view_id in ("#work-overview-content", "#work-overview-monitors")
            )
            assert "overview-other-watch" not in rendered
    finally:
        from gptme.tools.watch import _watches, _watches_lock

        with _watches_lock:
            _watches.pop("overview-other-watch", None)


@pytest.mark.asyncio
async def test_ctrl_t_opens_work_overview(tmp_path):
    """The Ctrl+T key (dispatched, not the action) opens WorkOverviewScreen."""
    manager = _make_manager(tmp_path)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+t")
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
@pytest.mark.parametrize("prompt", ["inspect logs", "first\nsecond\rthird\u2028fourth"])
async def test_work_overview_selection_scrolls_and_opens_selected_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prompt: str
) -> None:
    """Navigation visibly selects the same child that Enter opens, even below the fold."""
    from textual.containers import VerticalScroll
    from textual.widgets import Static

    manager = _make_manager(tmp_path)
    children = []
    for index in range(30):
        logdir = tmp_path / f"child-{index}"
        logdir.mkdir()
        children.append(
            _make_child_snapshot(f"child-{index}", logdir=logdir, prompt=prompt)
        )
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: list(children)
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
        # The selected physical row is inside the viewport, not merely scrolled
        # (lines never wrap, so a line's index is its row).
        lines = str(content.render()).splitlines()
        selected_row = next(
            index for index, line in enumerate(lines) if line.startswith("> ")
        )
        assert selected_row == screen._selected_row()
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


def test_work_summary_finished_child_does_not_tick() -> None:
    """A finished child reports the snapshot duration, not its growing start age."""
    from dataclasses import replace

    from gptme.tui.app import _build_work_summary

    running = _make_child_snapshot("runner")
    done = replace(
        _make_child_snapshot("finisher"),
        status="success",
        started_at=time.time() - 3600,
        elapsed_s=83,
        latest_progress="Tests pass\nincluding [literal] markup",
    )
    text = _build_work_summary(None, None, children=[running, done])
    assert "[running] 0s" in text
    assert "[success] 1m23s" in text
    assert "Tests pass including [literal] markup" in text
    assert len(text.splitlines()) == 3
    assert "[success] 1h" not in text


@pytest.mark.asyncio
async def test_work_overview_follows_selected_child_when_it_moves_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a refresh moves the selected child to the top, the viewport follows it."""
    from textual.containers import VerticalScroll

    manager = _make_manager(tmp_path)
    children = []
    for index in range(30):
        logdir = tmp_path / f"child-{index}"
        logdir.mkdir()
        children.append(
            _make_child_snapshot(f"child-{index}", logdir=logdir, prompt="inspect logs")
        )
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: list(children)
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
        app.push_screen(screen)
        await pilot.pause()
        for _ in range(25):
            screen.action_cursor_down()
        await pilot.pause()
        scroll = screen.query_one("#work-overview-scroll", VerticalScroll)
        assert scroll.scroll_y > 0
        children.insert(0, children.pop(25))
        await pilot.pause(1.2)
        assert screen._cursor == 0
        row = screen._selected_row()
        assert scroll.scroll_y <= row < scroll.scroll_y + scroll.size.height


@pytest.mark.asyncio
async def test_work_overview_keeps_selection_on_reused_logdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Planner runs share a logdir (same run_id); the cursor must not snap to the first."""
    from dataclasses import replace

    manager = _make_manager(tmp_path)
    logdir = tmp_path / "planner"
    logdir.mkdir()
    first = _make_child_snapshot("planner", logdir=logdir)
    second = replace(first, started_at=first.started_at + 5)
    assert first.run_id == second.run_id
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: [first, second]
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
        app.push_screen(screen)
        await pilot.pause()
        await pilot.press("down")
        assert screen._cursor == 1
        screen._refresh_summary()
        assert screen._cursor == 1


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
    monkeypatch.setattr("gptme.tools.watch.watch_snapshots", lambda *a, **kw: [watch])
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
        app.push_screen(screen)
        await pilot.pause()
        monitors = screen.query_one("#work-overview-monitors", Static)
        assert "[armed]" in str(monitors.render())
        watch.expired = True
        watch.fired = True
        await pilot.pause(1.2)
        assert "[expired]" in str(monitors.render())
        assert "wait [/tmp]" in str(monitors.render())


@pytest.mark.asyncio
@pytest.mark.parametrize("child_count", [0, 1])
async def test_work_overview_scrolls_monitors_with_arrow_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child_count: int
) -> None:
    """Monitor rows remain keyboard-accessible with or without child agents."""
    from textual.containers import VerticalScroll

    manager = _make_manager(tmp_path)
    children = [
        _make_child_snapshot("child", logdir=tmp_path / "child", prompt="inspect logs")
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
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: children
    )
    monkeypatch.setattr("gptme.tools.watch.watch_snapshots", lambda *a, **kw: watches)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 30), (80, 24), (60, 20), (40, 16), (26, 16)])
async def test_work_overview_renders_one_physical_row_per_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: tuple[int, int]
) -> None:
    """Long children are cut at the right edge, never wrapped onto more rows."""
    from dataclasses import replace

    from rich.cells import cell_len
    from textual.containers import VerticalScroll
    from textual.widgets import Static

    manager = _make_manager(tmp_path)
    children = []
    statuses = ("running", "success", "clarification_needed")
    for index, status in enumerate(statuses):
        logdir = tmp_path / f"child-{index}"
        logdir.mkdir()
        children.append(
            replace(
                _make_child_snapshot(
                    f"child-{index}-with-a-rather-long-identifier",
                    logdir=logdir,
                    prompt="分析日志 and [literal] markup " * 3,
                ),
                status=status,
                elapsed_s=3725,
                latest_progress="全角の進捗レポート [bold]not markup[/bold] " * 6,
            )
        )
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: list(children)
    )
    app = GptmeApp(manager, workspace=tmp_path)
    # Finished children are unread until their transcript is opened.
    monkeypatch.setattr(
        app, "_work_unread", lambda logdir, branch, child: child.status != "running"
    )
    async with app.run_test(size=size) as pilot:
        screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
        app.push_screen(screen)
        await pilot.pause()
        content = screen.query_one("#work-overview-content", Static)
        scroll = screen.query_one("#work-overview-scroll", VerticalScroll)
        width = content.content_size.width
        assert width < size[0]
        # Header + one row per child: nothing wrapped, nothing scrolls sideways.
        assert content.size.height == 1 + len(children)
        assert scroll.max_scroll_x == 0
        rows = [
            "".join(segment.text for segment in strip).rstrip()
            for strip in content.render_lines(content.region.reset_offset)
        ]
        assert len(rows) == 1 + len(children)
        assert rows[1].startswith("> ⏳")
        assert rows[2].startswith("  ✓")
        assert rows[3].startswith("  ?")
        for index, row in enumerate(rows[1:]):
            assert cell_len(row) <= width
            assert row.endswith("…")
            assert "……" not in row
            # Below the label/elapsed floor, status and unread become icons
            # and elapsed gives way to identity.
            if width >= 26:
                assert "1h02m" in row
            assert ("•" in row or "unread" in row) == (index > 0)
            assert f"child-{index}" in row
            if width >= 45:
                assert "分析" in row
        if width >= 60:
            assert "[running]" in rows[1] and "[success] 1h02m · unread" in rows[2]
        # Navigation tracks the physical row of the selected child.
        await pilot.press("down", "down")
        assert screen._cursor == 2
        assert screen._selected_row() == 3
        rows = [
            "".join(segment.text for segment in strip).rstrip()
            for strip in content.render_lines(content.region.reset_offset)
        ]
        assert rows[3].startswith("> ")
        # Shrinking the terminal re-cuts the rows instead of wrapping them.
        await pilot.resize_terminal(36, size[1])
        await pilot.pause()
        assert content.size.height == 1 + len(children)
        assert screen._selected_row() == 3
        width = content.content_size.width
        rows = [
            "".join(segment.text for segment in strip).rstrip()
            for strip in content.render_lines(content.region.reset_offset)
        ]
        for index, row in enumerate(rows[1:]):
            assert cell_len(row) <= width
            assert "1h02m" in row and f"child-{index}" in row


@pytest.mark.asyncio
async def test_work_overview_wraps_monitor_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Monitors have no detail view, so their descriptions wrap instead of being cut."""
    from textual.widgets import Static

    manager = _make_manager(tmp_path)
    child = _make_child_snapshot(
        "child", logdir=tmp_path / "child", prompt="inspect logs " * 10
    )
    description = "wait until " + "the deployment pipeline finishes " * 2 + "END"
    watch = Watch(
        id="long-watch",
        kind="until",
        description=description,
        created=time.time(),
    )
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: [child]
    )
    monkeypatch.setattr("gptme.tools.watch.watch_snapshots", lambda *a, **kw: [watch])
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test(size=(60, 20)) as pilot:
        screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
        app.push_screen(screen)
        await pilot.pause()
        content = screen.query_one("#work-overview-content", Static)
        monitors = screen.query_one("#work-overview-monitors", Static)
        assert content.size.height == 2
        assert monitors.size.height > 3
        rendered = "".join(
            "".join(segment.text for segment in strip)
            for strip in monitors.render_lines(monitors.region.reset_offset)
        )
        # The whole description is on screen, wrapped (not cut with an ellipsis).
        assert "".join(description.split()) in "".join(rendered.split())
        assert "…" not in rendered


@pytest.mark.parametrize("width", [None, 120, 64, 45, 28, 20])
def test_format_child_row_fits_width(width: int | None) -> None:
    """Status, unread and id fit the width; only the task/progress tail is cut."""
    from dataclasses import replace

    from rich.cells import cell_len
    from rich.text import Text

    from gptme.tui.app import _format_child_row

    child = replace(
        _make_child_snapshot(
            "an-agent-identifier-that-goes-on-and-on",
            prompt="调查 [red]bug[/red]\nacross\tlines " * 4,
        ),
        status="clarification_needed",
        elapsed_s=83,
        latest_progress="step 3/9\nreading files " * 20,
    )
    row = _format_child_row(child, selected=True, unread=True, width=width)
    assert "\n" not in row and "\t" not in row
    assert row.startswith("> ?")
    if width is None:
        # Unfitted: the whole line, for the overview's CSS to cut.
        assert "[clarification_needed] 1m23s · unread  an-agent-identifier" in row
        assert "step 3/9" in row
        return
    # What the overview shows: the row cut at its edge (text-overflow: ellipsis).
    visible = Text(row)
    visible.truncate(width, overflow="ellipsis")
    shown = visible.plain
    assert cell_len(shown) <= width
    if width >= 28:
        assert "1m23s" in shown
    assert "unread" in shown or "•" in shown
    if width >= 28:
        assert "an-agen" in shown
    if width >= 45:
        assert "调查" in shown
    # The id is cut in the head, never by the overview's edge cut.
    head = row.split("  ")[-2] if width >= 45 else row
    assert cell_len(head) <= width


@pytest.mark.parametrize("width", range(25))
@pytest.mark.parametrize(
    "status", ["running", "clarification_needed", "budget_exceeded", "blocked_external"]
)
def test_format_child_row_ultranarrow_keeps_identity(width: int, status: str) -> None:
    """Below the normal prefix width, prefer identity over elapsed and task text."""
    from dataclasses import replace

    from rich.cells import cell_len

    from gptme.tui.app import _format_child_row

    child = replace(
        _make_child_snapshot("child-2-long-identifier", prompt="inspect the logs"),
        status=status,
        elapsed_s=3725,
    )
    row = _format_child_row(child, selected=True, unread=True, width=width)
    assert cell_len(row) <= width
    if width >= 9:
        assert "ch" in row
        assert row.startswith("> ")
        assert "•" in row
    if 14 <= width <= 20:
        assert "child-2" in row


@pytest.mark.parametrize("width", [30, 40, 80])
@pytest.mark.parametrize("unread", [False, True])
def test_format_child_row_blocked_status_stays_visible(
    width: int, unread: bool
) -> None:
    """Blocked children retain status, elapsed and identity in narrow rows."""
    from dataclasses import replace

    from rich.text import Text

    from gptme.tui.app import _format_child_row

    child = replace(
        _make_child_snapshot("child-2-long-identifier", prompt="inspect the logs"),
        status="blocked_external",
        elapsed_s=3725,
    )
    row = _format_child_row(child, selected=True, unread=unread, width=width)
    visible = Text(row)
    visible.truncate(width, overflow="ellipsis")
    shown = visible.plain
    assert shown.startswith("> ⏸")
    assert "[blocked]" in shown or "[blocked_external]" in shown
    assert "1h02m" in shown
    assert "child-2" in shown
    assert ("•" in shown or "unread" in shown) == unread


def test_format_child_row_short_fields_are_not_cut() -> None:
    """Rows that already fit are left whole, with no ellipsis."""
    from gptme.tui.app import _format_child_row

    child = _make_child_snapshot("explore", prompt="read the docs")
    row = _format_child_row(child, selected=False, unread=False, width=80)
    assert row == "  ⏳ [running] 0s  explore  read the docs"
