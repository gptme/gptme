"""Cross-process peer delivery through the real idle timer and Textual workers."""

import asyncio
import contextvars
import json
import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets.text_area import Selection

from gptme.config import ChatConfig, get_config
from gptme.hooks import clear_hooks
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.prompt_queue import get_prompt_queue_path, queue_prompt
from gptme.tui.app import ChatInput, GptmeApp, UrlConfirmScreen


@pytest.fixture(autouse=True)
def isolated_hooks() -> Iterator[None]:
    clear_hooks()
    yield
    clear_hooks()


async def produce(logdir: Path, records: list[dict]) -> int:
    """Use the public queue API in a foreign interpreter, not a notification bus."""
    root = Path(__file__).resolve().parents[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import json, os, sys; from pathlib import Path; "
        "from gptme.prompt_queue import queue_prompt; "
        "[(queue_prompt(Path(sys.argv[1]), peer_sender_pid=os.getpid(), **record)) "
        "for record in json.loads(sys.argv[2])]",
        str(logdir),
        json.dumps(records),
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    return process.pid


def peer(content: str, *, steer: bool = False, branch: str = "main") -> dict:
    return {
        "content": content,
        "steer": steer,
        "branch": branch,
        "peer_sender": "sibling",
        "peer_message_id": f"id-{content}",
        "peer_sender_logdir": "/tmp/sibling",
    }


async def wait_for_turn(app: GptmeApp, seen: list, count: int) -> None:
    async with asyncio.timeout(8):
        while len(seen) < count or app.generating:
            await asyncio.sleep(0.02)


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_external_peer_burst_preserves_draft_and_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool
) -> None:
    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    sentinel = contextvars.ContextVar("peer-test", default="outside")
    token = sentinel.set("parent")
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    sentinel.reset(token)
    seen: list[list[Message]] = []

    def model(log: list[Message], **kwargs: object) -> Iterator[Message]:
        assert sentinel.get() == "parent"
        assert app.manager is manager
        seen.append(list(log))
        yield Message("assistant", "done")

    monkeypatch.setattr("gptme.tui.app.step", model)
    async with app.run_test() as pilot:
        await pilot.pause()
        field = app.query_one("#input", ChatInput)
        field._set_text("unfinished\ndraft selection")
        field.selection = Selection((0, 2), (1, 5))
        selection, focus = field.selection, app.focused
        pid = await produce(manager.logdir, [peer("/exit"), peer("steer", steer=True)])
        await wait_for_turn(app, seen, 1)
        await pilot.pause(0.7)
        assert len(seen) == 1
        delivered = [
            m for m in manager.log if m.metadata and m.metadata.get("peer_sender")
        ]
        assert len(delivered) == 2
        assert all(m in seen[0] for m in delivered)
        for msg in delivered:
            assert msg.metadata is not None
            assert msg.metadata["peer_sender"] == "sibling"
            assert msg.metadata["peer_sender_pid"] == pid
            assert msg.metadata["peer_message_id"].startswith("id-")
            assert msg.metadata["peer_sender_logdir"] == "/tmp/sibling"
        assert not app._quitting  # /exit is peer text, never a local command
        assert app.manager is manager
        assert field.text == "unfinished\ndraft selection"
        assert field.selection == selection
        assert app.focused is focus
        assert not get_prompt_queue_path(manager.logdir).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize(
    "guard",
    [
        "modal",
        "startup",
        "user-queue",
        "interrupt",
        "disabled",
        "quitting",
        "restart",
        "command",
    ],
)
async def test_idle_guards_leave_queue_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool, guard: str
) -> None:
    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    config = get_config()
    config.chat = ChatConfig(watch_autowake=guard != "disabled")
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    seen: list[int] = []

    def model(*args: object, **kwargs: object) -> Iterator[Message]:
        seen.append(1)
        yield Message("assistant", "done")

    monkeypatch.setattr("gptme.tui.app.step", model)
    async with app.run_test() as pilot:
        await pilot.pause()
        if guard == "modal":
            app.push_screen(UrlConfirmScreen(["https://example.com"]))
        elif guard == "command":
            # Commands reserve this same busy flag while owning _chat_ctx.
            app.generating = True
            app._set_state("running /compact…")
        elif guard == "startup":
            app._initial_prompts = ["local startup"]
        elif guard == "user-queue":
            app._enqueue_prompt("local queued prompt")
        elif guard == "interrupt":
            app.action_interrupt()
        elif guard == "quitting":
            app._quitting = True
        elif guard == "restart":
            app._web_restart_pending = 1
        await produce(manager.logdir, [peer("waiting"), peer("steering", steer=True)])
        path = get_prompt_queue_path(manager.logdir)
        before = path.read_bytes()
        await pilot.pause(0.7)
        assert path.read_bytes() == before
        assert not seen
        assert not manager.log.messages
        if guard == "modal":
            app.screen.dismiss(None)
        app.generating = False
        app._initial_prompts.clear()
        app.prompt_queue.clear()
        app._quitting = False
        app._web_restart_pending = None
        assert config.chat is not None
        config.chat.watch_autowake = True
        if guard == "interrupt":
            # Only an explicit new local turn lifts an interrupt, not a tick.
            await pilot.press("n", "e", "w", "enter")
            await wait_for_turn(app, seen, 1)
            await wait_for_turn(app, seen, 2)
        else:
            await wait_for_turn(app, seen, 1)
        assert len(seen) == (2 if guard == "interrupt" else 1)
        assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_busy_worker_defers_without_entering_parent_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool
) -> None:
    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    entered, release = threading.Event(), threading.Event()
    seen: list[int] = []

    def model(*args: object, **kwargs: object) -> Iterator[Message]:
        seen.append(1)
        if len(seen) == 1:
            entered.set()
            assert release.wait(8)
        yield Message("assistant", "done")

    monkeypatch.setattr("gptme.tui.app.step", model)
    async with app.run_test() as pilot:
        try:
            app._start_generation()
            assert await asyncio.to_thread(entered.wait, 5)
            await produce(
                manager.logdir,
                [peer("after busy"), peer("after busy steer", steer=True)],
            )
            path = get_prompt_queue_path(manager.logdir)
            before = path.read_bytes()
            await pilot.pause(0.7)
            assert path.read_bytes() == before
            assert seen == [1]
            release.set()
            await wait_for_turn(app, seen, 2)
            await pilot.pause(0.7)
            assert len(seen) == 2
            assert not path.exists()
        finally:
            release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_branch_isolation_and_nonpeer_queue_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool
) -> None:
    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    manager.branch("other")
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    seen: list[str] = []

    def model(*args: object, **kwargs: object) -> Iterator[Message]:
        seen.append(manager.current_branch)
        yield Message("assistant", "done")

    monkeypatch.setattr("gptme.tui.app.step", model)
    async with app.run_test() as pilot:
        queue_prompt(manager.logdir, "/exit")
        queue_prompt(
            manager.logdir, "local skill", skill_invocation_id="skill", branch="other"
        )
        await produce(
            manager.logdir,
            [peer("main only"), peer("other only", branch="other", steer=True)],
        )
        await wait_for_turn(app, seen, 1)
        assert seen == ["other"]
        assert not any("main only" in m.content for m in manager.log)
        manager.branch("main")
        await wait_for_turn(app, seen, 2)
        await pilot.pause(0.7)
        assert seen == ["other", "main"]
        assert not any("other only" in m.content for m in manager.log)
        remaining = [
            json.loads(line)
            for line in get_prompt_queue_path(manager.logdir).read_text().splitlines()
        ]
        assert [r["content"] for r in remaining] == ["/exit", "local skill"]
        assert not app._quitting


@pytest.mark.asyncio
async def test_command_drain_respects_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = LogManager([], logdir=tmp_path / "parent", lock=False)
    manager.branch("other")
    queue_prompt(manager.logdir, "main skill", skill_invocation_id="main")
    queue_prompt(
        manager.logdir, "other skill", branch="other", skill_invocation_id="other"
    )
    app = GptmeApp(manager, workspace=tmp_path)
    seen: list[int] = []

    def model(*args: object, **kwargs: object) -> Iterator[Message]:
        seen.append(1)
        yield Message("assistant", "done")

    monkeypatch.setattr("gptme.tui.app.step", model)
    async with app.run_test():
        app._drain_command_queued_prompts()
        await wait_for_turn(app, seen, 1)
        assert any(m.content == "other skill" for m in manager.log)
        assert not any(m.content == "main skill" for m in manager.log)
        assert "main skill" in get_prompt_queue_path(manager.logdir).read_text()
