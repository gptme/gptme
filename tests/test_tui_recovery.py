"""TUI retry boundaries discard previews, not committed conversation state."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("textual")

from rich.console import Console
from textual.widgets import Static

from gptme.lessons.skill_events import (
    read_skill_events,
    record_skill_phase,
    start_skill_invocation,
)
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tui.app import AssistantMessage, GptmeApp, StreamingMessage


def retry_notice() -> Any:
    # Match the shared recovery API without depending on constructor defaults.
    return SimpleNamespace(
        attempt=2,
        max_attempts=3,
        error=RuntimeError("provider unavailable"),
        delay=0.1,
        partial_output=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_retry_resets_only_uncommitted_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline: bool
) -> None:
    committed = [
        Message("user", "Check the file"),
        Message("assistant", "```shell\necho kept\n```"),
        Message("system", "```stdout\nkept result\n```"),
    ]
    manager = LogManager(committed, logdir=tmp_path / "conversation", lock=False)
    app = GptmeApp(manager, workspace=tmp_path, inline=inline)
    console = Console(record=True, width=120)
    notices: list[str] = []
    show_info = app._show_info

    def capture_info(text: str, error: bool = False) -> None:
        notices.append(text)
        show_info(text, error)

    monkeypatch.setattr(app, "_show_info", capture_info)
    monkeypatch.setattr(app, "_print_above", lambda *items: console.print(*items))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._enqueue_prompt("steer the next step")
        queued = Message("user", "confirmed queued prompt")
        app.prompt_queue.append(queued)
        app._confirmed_urls[id(queued)] = []
        queued_widgets = list(app._queued_widgets)
        committed_widgets = list(app.query(AssistantMessage))
        app._begin_stream()
        app._on_stream_token("<think>failed reasoning")
        app._set_stream_thinking(True)
        failed_widget = app._stream_widget
        # Leave a final chunk pending as a StreamPending event would do.
        with app._stream_lock:
            app._stream_pending.append(" failed tail")
            app._stream_flush_scheduled = True

        app._on_retry(retry_notice())
        # An already-posted flush must not resurrect the failed attempt.
        app._flush_stream()
        await pilot.pause()
        assert not app._stream_pending
        assert not app._stream_flush_scheduled
        assert app._stream_text == ""
        assert manager.log.messages == committed
        assert app.prompt_queue == ["steer the next step", queued]
        assert app._confirmed_urls == {id(queued): []}
        assert app._queued_widgets == queued_widgets
        assert list(app.query(AssistantMessage)) == committed_widgets
        assert notices and "retry" in notices[-1].lower()
        assert "2/3" in notices[-1]
        assert "RuntimeError" in notices[-1]
        assert "provider unavailable" not in notices[-1]
        if inline:
            assert "failed preview" in console.export_text().lower()
            assert "Generating" in str(app.query_one("#live", Static).render())
        else:
            assert failed_widget not in list(app.query(StreamingMessage))
            assert app._stream_widget is not None
            assert app._stream_widget._buffer == ""
            assert "Thinking" not in str(app._stream_widget._body.render())
        app._on_stream_token("Fresh answer")
        app._on_step_message(Message("assistant", "Fresh answer"))
        await app.wait_rendered()
        await pilot.pause()
        if not inline:
            assert app.query(AssistantMessage).last().content == "Fresh answer"


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["success", "exhaustion", "interrupt"])
async def test_generation_binds_retry_notifications_and_preserves_terminal_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    manager = LogManager([], logdir=tmp_path / "conversation", lock=False)
    invocation_id = start_skill_invocation(
        manager.logdir, "demo", tmp_path / "SKILL.md"
    )
    assert invocation_id
    record_skill_phase(manager.logdir, invocation_id, "queued")
    manager.append(Message("user", "run skill"))
    app = GptmeApp(manager, workspace=tmp_path)
    app._active_skill_invocation_id = invocation_id
    callbacks: list[Any] = []
    observed: list[Any] = []
    monkeypatch.delenv("GPTME_MAX_STEPS", raising=False)
    monkeypatch.setattr("gptme.tui.app.trigger_hook", lambda *a, **kw: [])
    monkeypatch.setattr(app, "_restore_terminal", lambda: None)

    @contextmanager
    def notifications(callback: Any) -> Iterator[None]:
        callbacks.append(callback)
        try:
            yield
        finally:
            callbacks.pop()

    # A deterministic provider boundary: no sleep, network, or core retry policy.
    monkeypatch.setattr(
        "gptme.tui.app.recovery_notifications", notifications, raising=False
    )
    original_retry = getattr(app, "_on_retry", None)

    def on_retry(notice: Any) -> None:
        observed.append(notice)
        assert original_retry is not None
        original_retry(notice)
        assert app._stream_widget is not None
        assert app._stream_widget._buffer == ""
        assert not app._stream_pending
        assert app.prompt_queue == ["/help"]

    monkeypatch.setattr(app, "_on_retry", on_retry, raising=False)
    tool_call = Message("assistant", "Already executed tool")
    tool_result = Message("system", "Already committed result")

    def fake_step(*args: Any, **kwargs: Any) -> Iterator[Message]:
        assert len(callbacks) == 1, "Recovery must be bound during step iteration"
        yield tool_call
        yield tool_result
        kwargs["on_token"]("failed partial response")
        kwargs["on_thinking"](True)
        callbacks[-1](retry_notice())
        assert manager.log.messages[-2:] == [tool_call, tool_result]
        if ending == "exhaustion":
            raise RuntimeError("retry budget exhausted")
        if ending == "interrupt":
            raise KeyboardInterrupt
        kwargs["on_token"]("Recovered answer")
        yield Message("assistant", "Recovered answer")

    monkeypatch.setattr("gptme.tui.app.step", fake_step)
    async with app.run_test() as pilot:
        app._enqueue_prompt("/help")
        # Run exactly the worker body, avoiding queue dispatch after the turn.
        await asyncio.to_thread(app._generation_body)
        await app.wait_rendered()
        await pilot.pause()
        assert len(observed) == 1
        assert not callbacks, "The recovery callback must not leak past step()"
        assert app.prompt_queue == ["/help"]
        assert tool_call in manager.log.messages
        assert tool_result in manager.log.messages
        events = read_skill_events(manager.logdir)
        expected = {
            "success": "completed",
            "exhaustion": "failed",
            "interrupt": "abandoned",
        }
        assert events[-1].phase == expected[ending]
        if ending != "success":
            assert not any(event.phase == "completed" for event in events)
        if ending == "exhaustion":
            assert events[-1].error_type == "RuntimeError"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_backoff", [False, True])
async def test_real_recovery_exhaustion_or_backoff_cancel_never_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_backoff: bool
) -> None:
    import requests

    from gptme.llm import recovery

    manager = LogManager([], logdir=tmp_path / "conversation", lock=False)
    invocation_id = start_skill_invocation(
        manager.logdir, "demo", tmp_path / "SKILL.md"
    )
    assert invocation_id
    record_skill_phase(manager.logdir, invocation_id, "queued")
    app = GptmeApp(manager, workspace=tmp_path)
    app._active_skill_invocation_id = invocation_id
    monkeypatch.setattr("gptme.tui.app.trigger_hook", lambda *a, **kw: [])
    monkeypatch.setattr(app, "_restore_terminal", lambda: None)
    monkeypatch.setattr(recovery, "get_max_retries", lambda: 2)
    monkeypatch.setattr(recovery, "retry_delay_for_error", lambda *a: 0.0)
    attempts = 0
    notices: list[Any] = []
    on_retry = app._on_retry

    def capture_retry(notice: Any) -> None:
        notices.append(notice)
        on_retry(notice)

    def backoff(*args: Any) -> bool:
        if cancel_backoff:
            # action_interrupt injects this into the step worker during backoff.
            raise KeyboardInterrupt
        return False

    monkeypatch.setattr(app, "_on_retry", capture_retry)
    monkeypatch.setattr(recovery, "backoff_wait", backoff)

    def fake_step(*args: Any, **kwargs: Any) -> Iterator[Message]:
        def generate() -> Message:
            nonlocal attempts
            attempts += 1
            kwargs["on_token"](f"failed attempt {attempts}")
            error = requests.Timeout("transient timeout")
            error._gptme_from_llm_reply = True  # type: ignore[attr-defined]
            error._gptme_llm_reply_output_emitted = True  # type: ignore[attr-defined]
            raise error

        yield recovery.recover_generation(generate)

    monkeypatch.setattr("gptme.tui.app.step", fake_step)
    async with app.run_test():
        await asyncio.to_thread(app._generation_body)
        assert attempts == (1 if cancel_backoff else 2)
        assert len(notices) == 1
        assert not any(msg.role == "assistant" for msg in manager.log.messages)
        if cancel_backoff:
            from gptme.constants import INTERRUPT_CONTENT

            assert manager.log.messages[-1].content == INTERRUPT_CONTENT
        else:
            assert not manager.log.messages
        terminal = read_skill_events(manager.logdir)[-1]
        assert terminal.phase == ("abandoned" if cancel_backoff else "failed")
        assert terminal.error_type == (None if cancel_backoff else "Timeout")
