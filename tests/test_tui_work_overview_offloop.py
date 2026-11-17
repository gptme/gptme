"""The work overview reads child logs off the Textual UI thread.

``subagent_snapshots`` can read a restored child's whole history, so calling it
from the overview's 1s refresh stalled keystrokes and redraws on the UI loop.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("textual")

from gptme.logmanager import LogManager
from gptme.tools.subagent.types import Subagent
from gptme.tui.app import GptmeApp, WorkOverviewScreen

if TYPE_CHECKING:
    from pathlib import Path


def _manager(tmp_path: Path) -> LogManager:
    manager = LogManager([], logdir=tmp_path / "conv", lock=False)
    manager.logdir.mkdir(parents=True, exist_ok=True)
    return manager


def _child(tmp_path: Path, manager: LogManager, agent_id: str) -> Subagent:
    return Subagent(
        agent_id=agent_id,
        prompt=f"task {agent_id}",
        thread=None,
        logdir=tmp_path / agent_id,
        model=None,
        parent_logdir=manager.logdir.resolve(),
        parent_branch=manager.current_branch,
    )


def _recording_fake(real, calls: list[bool]):
    """Wrap ``subagent_snapshots`` and record whether each call ran on the UI thread."""

    def fake(*args, **kwargs):
        calls.append(threading.current_thread() is threading.main_thread())
        return real(*args, **kwargs)

    return fake


@pytest.mark.asyncio
async def test_periodic_refresh_reads_snapshots_off_the_ui_thread(
    tmp_path, monkeypatch
):
    import gptme.tools.subagent as subagent_pkg
    from gptme.tools.subagent.types import _subagents, _subagents_lock

    manager = _manager(tmp_path)
    child = _child(tmp_path, manager, "offloop-a")
    with _subagents_lock:
        _subagents.append(child)
    calls: list[bool] = []
    monkeypatch.setattr(
        subagent_pkg,
        "subagent_snapshots",
        _recording_fake(subagent_pkg.subagent_snapshots, calls),
    )
    # The app's own work indicator also calls subagent_snapshots every second, so
    # count the overview's reads directly to prove *its* interval fired.
    overview_reads: list[bool] = []
    real_read = WorkOverviewScreen._read_inputs

    def recording_read(self, *args, **kwargs):
        overview_reads.append(threading.current_thread() is threading.main_thread())
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(WorkOverviewScreen, "_read_inputs", recording_read)
    try:
        app = GptmeApp(manager, workspace=tmp_path)
        async with app.run_test() as pilot:
            app.push_screen(WorkOverviewScreen(manager.logdir, manager.current_branch))
            await pilot.pause(0.3)  # initial read settles
            settled = len(overview_reads)
            await pilot.pause(1.4)  # past the 1s interval
            assert len(overview_reads) > settled, "interval refresh never ran"
            assert not any(overview_reads), "overview read ran on the UI thread"
            assert not any(calls), "subagent_snapshots ran on the UI thread"
            from textual.widgets import Static

            rendered = str(
                app.screen.query_one("#work-overview-content", Static).render()
            )
            assert "offloop-a" in rendered
    finally:
        with _subagents_lock:
            _subagents[:] = [sa for sa in _subagents if sa.agent_id != "offloop-a"]


@pytest.mark.asyncio
async def test_cursor_moves_reuse_the_last_snapshot(tmp_path, monkeypatch):
    import gptme.tools.subagent as subagent_pkg
    from gptme.tools.subagent.types import _subagents, _subagents_lock

    manager = _manager(tmp_path)
    ids = ["cursor-a", "cursor-b", "cursor-c"]
    with _subagents_lock:
        _subagents.extend(_child(tmp_path, manager, i) for i in ids)
    calls: list[bool] = []
    monkeypatch.setattr(
        subagent_pkg,
        "subagent_snapshots",
        _recording_fake(subagent_pkg.subagent_snapshots, calls),
    )
    try:
        app = GptmeApp(manager, workspace=tmp_path)
        async with app.run_test() as pilot:
            screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
            app.push_screen(screen)
            await pilot.pause(0.3)
            # Pause the periodic refreshes (the app's work indicator and the
            # overview's own) so only cursor-triggered reads can move the count.
            for pump in (app, screen):
                for timer in list(pump._timers):
                    timer.pause()
            before = len(calls)
            await pilot.press("j")
            await pilot.press("j")
            await pilot.press("k")
            await pilot.pause(0.1)
            assert screen._cursor == 1
            assert len(calls) == before, "cursor movement re-read child logs"
            from textual.widgets import Static

            rendered = str(
                app.screen.query_one("#work-overview-content", Static).render()
            )
            marked = [ln for ln in rendered.splitlines() if "cursor-b" in ln]
            assert marked and marked[0].lstrip().startswith(">")
    finally:
        with _subagents_lock:
            _subagents[:] = [sa for sa in _subagents if sa.agent_id not in ids]


@pytest.mark.asyncio
async def test_slow_periodic_read_keeps_keys_responsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keys and overlapping refreshes finish before the pending read is released."""
    from gptme.tools.subagent.types import _subagents, _subagents_lock

    manager = _manager(tmp_path)
    ids = ["slow-a", "slow-b"]
    with _subagents_lock:
        _subagents.extend(_child(tmp_path, manager, i) for i in ids)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = 0
    screen = WorkOverviewScreen(manager.logdir, manager.current_branch)
    real_read = screen._read_inputs

    def slow_read() -> tuple[list, list]:
        nonlocal calls
        calls += 1
        if calls > 1:
            entered.set()
            # Safety timeout only: assertions below run while this gate is shut.
            assert release.wait(15), "test never released the pending snapshot read"
            finished.set()
        return real_read()

    monkeypatch.setattr(screen, "_read_inputs", slow_read)
    try:
        app = GptmeApp(manager, workspace=tmp_path)
        async with app.run_test() as pilot:
            try:
                app.push_screen(screen)
                await pilot.pause(0.3)
                assert len(screen._children) == 2
                assert await asyncio.to_thread(entered.wait, 5), "timer never read"
                assert screen._reading
                screen._start_refresh()
                await asyncio.wait_for(screen._refresh_off_loop(), timeout=2)
                assert calls == 2, "overlapping refresh started another read"
                await asyncio.wait_for(pilot.press("j"), timeout=2)
                assert screen._cursor == 1
                await asyncio.wait_for(pilot.press("escape"), timeout=2)
                assert app.screen is not screen
                assert not release.is_set()
                assert not finished.is_set(), "keys waited for the read to finish"
            finally:
                release.set()
    finally:
        release.set()
        with _subagents_lock:
            _subagents[:] = [sa for sa in _subagents if sa.agent_id not in ids]


def test_finished_children_are_read_once_across_refreshes(tmp_path, monkeypatch):
    """A terminal child is cached on its first snapshot, however often it refreshes.

    This is what keeps 20 restored finished children from re-reading their
    histories every second; the overview only has to avoid doing the first
    read on the UI thread.
    """
    from gptme.tools.subagent import subagent_snapshots
    from gptme.tools.subagent.types import (
        ReturnType,
        _subagents,
        _subagents_lock,
    )

    manager = _manager(tmp_path)
    ids = [f"done-{i}" for i in range(5)]
    children = [_child(tmp_path, manager, i) for i in ids]
    reads: list[str] = []

    def counting_read_log(self):
        reads.append(self.agent_id)
        return ReturnType("success", "finished")

    monkeypatch.setattr(Subagent, "_read_log", counting_read_log)
    with _subagents_lock:
        _subagents.extend(children)
    try:
        for _ in range(4):
            snaps = subagent_snapshots(manager.logdir, parent_branch="main")
            assert [s.status for s in snaps] == ["success"] * 5
        assert sorted(reads) == sorted(ids), "a finished child was re-read"
    finally:
        with _subagents_lock:
            _subagents[:] = [sa for sa in _subagents if sa.agent_id not in ids]


@pytest.mark.asyncio
async def test_resize_before_first_read_keeps_loading_placeholder(
    tmp_path, monkeypatch
):
    """A resize ahead of the first off-loop read must not show "No children"."""
    from textual.widgets import Static

    manager = _manager(tmp_path)
    release = threading.Event()
    real_read = WorkOverviewScreen._read_inputs

    def slow_read(self, *args, **kwargs):
        release.wait(5)
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(WorkOverviewScreen, "_read_inputs", slow_read)
    app = GptmeApp(manager, workspace=tmp_path)
    try:
        async with app.run_test() as pilot:
            app.push_screen(WorkOverviewScreen(manager.logdir, manager.current_branch))
            await pilot.pause(0.2)
            assert isinstance(app.screen, WorkOverviewScreen)
            app.screen.on_resize()
            await pilot.pause(0.2)
            rendered = str(
                app.screen.query_one("#work-overview-content", Static).render()
            )
            assert "Loading" in rendered
            assert "No children" not in rendered
            release.set()
            # Bounded wait for the worker to land and render, not a fixed pause.
            for _ in range(100):
                if app.screen._loaded:
                    break
                await pilot.pause(0.05)
            assert app.screen._loaded
            await pilot.pause()
            rendered = str(
                app.screen.query_one("#work-overview-content", Static).render()
            )
            assert "No children" in rendered
    finally:
        release.set()
