"""TUI lifecycle evidence must describe execution, not queue admission."""

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("textual")

from gptme.constants import DECLINED_CONTENT
from gptme.lessons.index import LessonIndex, clear_cache
from gptme.lessons.skill_commands import (
    register_skill_commands,
    unregister_skill_commands,
)
from gptme.lessons.skill_events import (
    read_skill_events,
    record_skill_phase,
    start_skill_invocation,
)
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools.complete import SessionCompleteException
from gptme.tui.app import GptmeApp


@pytest.mark.asyncio
async def test_tui_real_command_has_one_terminal_event(tmp_path, monkeypatch):
    """Exercise command admission, durable queue, worker dispatch, and teardown."""
    skill = tmp_path / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo\ndescription: demo\n---\nSay hello.")
    monkeypatch.setattr(
        LessonIndex, "_default_dirs", staticmethod(lambda: [skill.parent.parent])
    )
    monkeypatch.setattr("gptme.tui.app.trigger_hook", lambda *a, **kw: [])

    def reply(*args, **kwargs):
        yield Message("assistant", "Hello.")

    monkeypatch.setattr("gptme.tui.app.step", reply)
    clear_cache()
    register_skill_commands()
    manager = LogManager([], logdir=tmp_path / "conversation", lock=False)
    app = GptmeApp(manager, workspace=tmp_path)
    try:
        async with app.run_test() as pilot:
            app._handle_command("/skill:demo")
            await app.workers.wait_for_complete()
            await pilot.pause()
            events = read_skill_events(manager.logdir)
            assert [e.phase for e in events] == ["started", "queued", "completed"]
            assert len({e.invocation_id for e in events}) == 1
            assert events[0].session_id != str(manager.logdir.resolve())
        assert read_skill_events(manager.logdir) == events
    finally:
        unregister_skill_commands()
        clear_cache()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("reply", "completed"),
        ("tools_then_reply", "completed"),
        ("complete_tool", "completed"),
        ("error", "failed"),
        ("interrupt", "abandoned"),
        ("declined", "abandoned"),
        ("limit", "abandoned"),
        ("limit_final", "completed"),
        ("empty", "abandoned"),
    ],
)
def test_tui_terminal_evidence(tmp_path: Path, monkeypatch, mode, expected):
    manager = LogManager([], logdir=tmp_path / "conversation", lock=False)
    invocation_id = start_skill_invocation(
        manager.logdir, "demo", tmp_path / "SKILL.md"
    )
    assert invocation_id
    record_skill_phase(manager.logdir, invocation_id, "queued")
    manager.append(
        Message("user", "run skill", metadata={"skill_invocation_id": invocation_id})
    )
    app = GptmeApp(manager, workspace=tmp_path)
    app._active_skill_invocation_id = invocation_id
    monkeypatch.setattr(app, "call_from_thread", lambda *a, **kw: None)
    monkeypatch.setattr(app, "_restore_terminal", lambda: None)
    monkeypatch.setattr("gptme.tui.app.trigger_hook", lambda *a, **kw: [])
    monkeypatch.delenv("GPTME_MAX_STEPS", raising=False)
    calls = 0

    def reply(*args, **kwargs) -> Iterator[Message]:
        nonlocal calls
        calls += 1
        if mode == "error":
            raise RuntimeError("provider unavailable")
        if mode == "interrupt":
            raise KeyboardInterrupt
        if mode == "complete_tool":
            raise SessionCompleteException
        if mode == "empty":
            return
        if mode == "declined":
            yield Message("system", DECLINED_CONTENT)
        elif mode in ("limit", "tools_then_reply") and calls == 1:
            yield Message("assistant", "```shell\necho 1\n```")
            yield Message("system", "1")
        elif mode == "limit_final":
            # Final response (no runnable tools) exactly at the step boundary.
            yield Message("assistant", "Done.")
        else:
            # The tool-bearing step is not itself completion.
            assert read_skill_events(manager.logdir)[-1].phase == "queued"
            yield Message("assistant", "Done.")

    if mode == "limit":
        monkeypatch.setenv("GPTME_MAX_STEPS", "1")
    elif mode == "limit_final":
        monkeypatch.setenv("GPTME_MAX_STEPS", "1")
    monkeypatch.setattr("gptme.tui.app.step", reply)
    app._generation_body()
    events = read_skill_events(manager.logdir)
    assert [e.phase for e in events] == ["started", "queued", expected]
    assert events[-1].error_type == ("RuntimeError" if mode == "error" else None)
    assert events[-1].duration_seconds is not None
    if mode == "tools_then_reply":
        assert calls == 2


@pytest.mark.asyncio
async def test_tui_interrupted_queue_loses_identity_only_after_abandonment(tmp_path):
    from gptme.tui.app import ChatInput

    manager = LogManager([], logdir=tmp_path / "conversation", lock=False)
    invocation_id = start_skill_invocation(
        manager.logdir, "demo", tmp_path / "SKILL.md"
    )
    assert invocation_id
    record_skill_phase(manager.logdir, invocation_id, "queued")
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test():
        app.prompt_queue.append(
            Message(
                "user", "queued skill", metadata={"skill_invocation_id": invocation_id}
            )
        )
        app._interrupt_event.set()
        await app._generation_done()
        assert [e.phase for e in read_skill_events(manager.logdir)] == [
            "started",
            "queued",
            "abandoned",
        ]
        assert app.query_one("#input", ChatInput).text == "queued skill"
        assert not app.prompt_queue


@pytest.mark.asyncio
async def test_tui_exit_abandons_only_its_run(tmp_path, monkeypatch):
    from gptme.lessons.skill_events import skill_invocation_owner

    manager = LogManager([], logdir=tmp_path / "conversation", lock=False)
    app = GptmeApp(manager, workspace=tmp_path)
    other = start_skill_invocation(
        manager.logdir, "other", tmp_path / "OTHER.md", session_id="other"
    )
    with skill_invocation_owner(manager.logdir, app._skill_session_id):
        ours = start_skill_invocation(manager.logdir, "ours", tmp_path / "OURS.md")
    async with app.run_test():
        pass
    events = read_skill_events(manager.logdir)
    assert [e.phase for e in events if e.invocation_id == ours] == [
        "started",
        "abandoned",
    ]
    assert [e.phase for e in events if e.invocation_id == other] == ["started"]
