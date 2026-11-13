"""Work visibility and user acknowledgement, independent of model delivery."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets import Static

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools.subagent.api import SubagentSnapshot
from gptme.tools.watch import Watch
from gptme.tui.app import GptmeApp, WorkOverviewScreen, WorkScreen


def _manager(tmp_path: Path) -> LogManager:
    return LogManager([], logdir=tmp_path / "parent", lock=False)


def _child(tmp_path: Path) -> SubagentSnapshot:
    logdir = tmp_path / "child"
    logdir.mkdir()
    (logdir / "conversation.jsonl").write_text(
        json.dumps(Message("assistant", "Finished the investigation").to_dict()) + "\n"
    )
    return SubagentSnapshot(
        run_id=str(logdir),
        agent_id="researcher",
        status="running",
        started_at=100.0,
        elapsed_s=12,
        execution_mode="thread",
        prompt_preview="Investigate the bug",
        logdir=logdir,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_indicator_and_completion_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool
) -> None:
    """Only inspecting a finished child acknowledges it, not polling or model delivery."""
    manager = _manager(tmp_path)
    child = _child(tmp_path)
    children = [child]
    watch = Watch(id="deadline", kind="until", description="Wait for CI", created=100.0)
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: list(children)
    )
    monkeypatch.setattr("gptme.tools.watch.watch_snapshots", lambda *a, **kw: [watch])
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    async with app.run_test() as pilot:
        indicator = app.query_one("#work-indicator", Static)
        assert indicator.display
        assert "1 running" in str(indicator.render())
        assert "1 armed" in str(indicator.render())
        assert "Ctrl+T" in str(indicator.render())
        # A configured statusline must not hide the independent work indicator.
        app._show_statusline("custom status")
        assert "custom status" in str(app.query_one("#status", Static).render())
        app.action_show_work()
        await pilot.pause()
        overview = app.screen
        assert isinstance(overview, WorkOverviewScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, WorkScreen)
        # Looking at a running child does not acknowledge its future completion.
        children[:] = [replace(child, status="success", elapsed_s=14)]
        watch.expired = True
        watch.fired = True
        await pilot.pause(1.2)
        assert "1 unread" in str(indicator.render())
        assert "0 armed" in str(indicator.render())
        await pilot.press("escape")
        await pilot.pause()
        overview._refresh_summary()
        assert "unread" in str(
            overview.query_one("#work-overview-content", Static).render()
        )
        # Closing/reopening the overview alone does not acknowledge anything.
        await pilot.press("escape")
        app.action_show_work()
        await pilot.pause()
        assert "unread" in str(
            app.screen.query_one("#work-overview-content", Static).render()
        )
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, WorkScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert "unread" not in str(
            app.screen.query_one("#work-overview-content", Static).render()
        )
        assert "0 unread" in str(indicator.render())
        # The same transcript path can contain another planner run.
        children[:] = [replace(children[0], started_at=200.0)]
        await pilot.pause(1.2)
        assert "1 unread" in str(indicator.render())
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_acknowledgement_does_not_cross_parent_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    child = replace(_child(tmp_path), status="failure")
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: [child]
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        app.push_screen(WorkOverviewScreen(manager.logdir, "main"))
        await pilot.pause()
        await pilot.press("enter", "escape")
        await pilot.pause()
        assert "unread" not in str(
            app.screen.query_one("#work-overview-content", Static).render()
        )
        await pilot.press("escape")
        app.push_screen(WorkOverviewScreen(manager.logdir, "other"))
        await pilot.pause()
        assert "unread" in str(
            app.screen.query_one("#work-overview-content", Static).render()
        )
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_empty_indicator_is_hidden(tmp_path: Path) -> None:
    app = GptmeApp(_manager(tmp_path), workspace=tmp_path)
    async with app.run_test():
        assert not app.query_one("#work-indicator", Static).display


@pytest.mark.asyncio
async def test_real_snapshots_keep_unread_and_progress_after_model_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Progress/completion queue drains cannot acknowledge the user's work view."""
    import threading
    from types import SimpleNamespace
    from typing import cast

    from gptme.tools.subagent.hooks import (
        notify_completion,
        notify_progress,
        take_queued_completions,
        take_queued_progress,
    )
    from gptme.tools.subagent.types import ReturnType, Subagent

    manager = _manager(tmp_path)
    child = _child(tmp_path)
    live = cast(threading.Thread, SimpleNamespace(is_alive=lambda: True))
    agents = [
        Subagent(
            agent_id=agent_id,
            prompt="Investigate",
            logdir=tmp_path / agent_id,
            thread=live,
            model=None,
            parent_logdir=owner,
            parent_branch=branch,
        )
        for agent_id, owner, branch in (
            ("child", manager.logdir, manager.current_branch),
            ("second", manager.logdir, manager.current_branch),
            ("decoy", tmp_path / "foreign", manager.current_branch),
            ("branch-decoy", manager.logdir, "side"),
        )
    ]
    for module in ("api", "hooks"):
        monkeypatch.setattr(f"gptme.tools.subagent.{module}._subagents", agents)
    results: dict[str, ReturnType] = {}
    for module in ("api", "hooks", "types"):
        monkeypatch.setattr(f"gptme.tools.subagent.{module}._subagent_results", results)
    monkeypatch.setattr(
        "gptme.tools.subagent.api._maybe_rehydrate_registry", lambda: None
    )
    monkeypatch.setattr(
        "gptme.tools.subagent.hooks._notify_server", lambda *a, **kw: False
    )
    app = GptmeApp(manager, workspace=tmp_path)
    # Hold a modal while injecting notifications so no real model generation starts.
    async with app.run_test() as pilot:
        app.action_show_work()
        await pilot.pause()
        assert isinstance(app.screen, WorkOverviewScreen)
        owner = manager.logdir
        branch = manager.current_branch
        from gptme.tools.subagent.hooks import _remember_progress

        _remember_progress(
            "child",
            "Checked [queue]\nwithout consuming it",
            owner,
            branch,
            run=agents[0],
        )
        notify_progress(
            "child",
            "Checked [queue]\nwithout consuming it",
            parent_logdir=owner,
            parent_branch=branch,
        )
        assert take_queued_progress(owner, branch=branch) == [
            ("child", "Checked [queue]\nwithout consuming it")
        ]
        app.screen._refresh_summary()
        assert "Checked [queue] without consuming it" in str(
            app.screen.query_one("#work-overview-content", Static).render()
        )
        from gptme.tools.subagent.hooks import _subagent_results

        _subagent_results["child"] = ReturnType("success", "done", duration_s=14)
        notify_completion(
            "child", "success", "done", parent_logdir=owner, parent_branch=branch
        )
        assert len(take_queued_completions(owner, branch=branch)) == 1
        await app._update_work_indicator()
        indicator = app.screen_stack[0].query_one("#work-indicator", Static)
        assert "1 running" in str(indicator.render())
        assert "1 unread" in str(indicator.render())
        app.screen._refresh_summary()
        summary = str(app.screen.query_one("#work-overview-content", Static).render())
        assert "decoy" not in summary
        assert "[success] 14s · unread" in summary
        assert child.logdir == agents[0].logdir
        await pilot.press("enter", "escape")
        await pilot.pause()
        assert "0 unread" in str(indicator.render())
        assert take_queued_completions(owner, branch=branch) == []
        await pilot.press("escape")


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_parent_can_finish_while_child_transcript_is_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool
) -> None:
    import asyncio
    import threading
    from collections.abc import Generator, Iterable

    from gptme.tui.app import ChatInput

    started, release = threading.Event(), threading.Event()
    child = _child(tmp_path)
    children = [child]
    manager = _manager(tmp_path)
    monkeypatch.setattr(
        "gptme.tools.subagent.subagent_snapshots", lambda *a, **kw: list(children)
    )

    def step(
        messages: Iterable[Message], **kwargs: object
    ) -> Generator[Message, None, None]:
        started.set()
        assert release.wait(10)
        yield Message("assistant", "Parent finished independently")

    monkeypatch.setattr("gptme.tui.app.step", step)
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    try:
        async with app.run_test() as pilot:
            parent = app.screen
            field = app.query_one("#input", ChatInput)
            field.text = "unsent draft"
            app._start_generation()
            assert await asyncio.to_thread(started.wait, 5)
            app.action_show_work()
            await pilot.pause()
            await pilot.press("enter")
            assert isinstance(app.screen, WorkScreen)
            children[:] = [replace(child, status="success", elapsed_s=23)]
            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause(1.2)
            assert not app.generating
            assert app.manager is manager
            assert field.text == "unsent draft"
            assert "1 unread" in str(
                app.screen_stack[0].query_one("#work-indicator", Static).render()
            )
            await pilot.press("escape", "escape")
            await pilot.pause()
            assert app.screen is parent
            assert field.text == "unsent draft"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_indicator_snapshot_read_is_off_ui_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    ui_thread = threading.current_thread()
    observed: list[threading.Thread] = []

    def snapshots(*args: object, **kwargs: object) -> list[SubagentSnapshot]:
        observed.append(threading.current_thread())
        return []

    monkeypatch.setattr("gptme.tools.subagent.subagent_snapshots", snapshots)
    app = GptmeApp(_manager(tmp_path), workspace=tmp_path)
    async with app.run_test():
        await app._update_work_indicator()
    assert observed
    assert all(thread is not ui_thread for thread in observed)
