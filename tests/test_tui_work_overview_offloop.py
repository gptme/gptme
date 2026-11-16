"""The work overview reads child logs off the Textual UI thread.

``subagent_snapshots`` can read a restored child's whole history, so calling it
from the overview's 1s refresh stalled keystrokes and redraws on the UI loop.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

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
    try:
        app = GptmeApp(manager, workspace=tmp_path)
        async with app.run_test() as pilot:
            app.push_screen(WorkOverviewScreen(manager.logdir, manager.current_branch))
            await pilot.pause(1.4)  # past the 1s interval
            assert len(calls) >= 2, "interval refresh never ran"
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
