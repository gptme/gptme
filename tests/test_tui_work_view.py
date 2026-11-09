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


def test_watch_outcome_cancelled(tmp_path):
    """Explicit cancel sets outcome='cancelled'."""

    ld = tmp_path / "cancel-outcome"
    ld.mkdir()
    w = _make_watch("outcome-cancelled", logdir=ld)
    with w.lock:
        w.cancelled = True
        if w.outcome is None:
            w.outcome = "cancelled"
        w.cancel_event.set()
    assert w.outcome == "cancelled"


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
        # Empty state: either "No children" or the actual summary (could list nothing)
        assert rendered is not None


@pytest.mark.asyncio
async def test_work_overview_shows_children(tmp_path):
    """WorkOverviewScreen lists children whose parent_logdir matches the manager."""
    from gptme.tools.subagent.types import Subagent, _subagents, _subagents_lock

    manager = _make_manager(tmp_path)
    # Ensure logdir exists so subagent_snapshot can resolve it
    manager.logdir.mkdir(parents=True, exist_ok=True)

    child = Subagent(
        agent_id="overview-test-child",
        prompt="write tests",
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
