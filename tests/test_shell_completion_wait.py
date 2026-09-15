"""Completion waiting uses reader events and respects session ownership/control."""

from collections.abc import Generator
from pathlib import Path
from threading import Condition, Event, Thread
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gptme.hooks import StopPropagation, current_conversation_id
from gptme.message import Message
from gptme.tools import shell_background as bg


@pytest.fixture(autouse=True)
def clean_jobs(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("GPTME_WATCH_IDLE_MAX", "3")
    bg.reset_background_jobs()
    yield
    bg.reset_background_jobs()


def start_owned(command: str, owner: str = "owner") -> bg.BackgroundJob:
    token = current_conversation_id.set(owner)
    try:
        return bg.start_background_job(command)
    finally:
        current_conversation_id.reset(token)


def test_step_drain_is_nonblocking_and_exactly_once() -> None:
    job = start_owned("printf final-output; exit 7")
    assert job._reader_thread is not None
    job._reader_thread.join(timeout=3)
    manager = SimpleNamespace(chat_id="owner")
    messages = list(bg.background_job_completion_hook(manager))
    assert len(messages) == 1
    assert "exit code 7" in messages[0].content
    assert "final-output" in messages[0].content
    assert list(bg.background_job_completion_hook(manager)) == []
    assert bg._get_background_job("owner", job.id) is job


@pytest.mark.parametrize(
    ("interactive", "no_confirm", "queued"),
    [(True, False, []), (True, True, []), (False, False, [Message("user", "next")])],
)
def test_interactive_or_queued_input_never_waits(
    interactive: bool, no_confirm: bool, queued: list[Message]
) -> None:
    start_owned("sleep 30")
    with patch("threading.Condition.wait", side_effect=AssertionError("must not wait")):
        assert (
            list(
                bg.background_job_wait_hook(
                    SimpleNamespace(chat_id="owner"), interactive, queued, no_confirm
                )
            )
            == []
        )


def test_foreign_running_job_does_not_hold_conversation_open() -> None:
    start_owned("sleep 30", "foreign")
    with patch("threading.Condition.wait", side_effect=AssertionError("must not wait")):
        assert (
            list(
                bg.background_job_wait_hook(SimpleNamespace(chat_id="owner"), False, [])
            )
            == []
        )


def test_timeout_reports_once_but_preserves_late_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GPTME_WATCH_IDLE_MAX", "0")
    job = start_owned("sleep 30")
    manager = SimpleNamespace(chat_id="owner")
    results = list(bg.background_job_wait_hook(manager, False, []))
    assert isinstance(results[0], Message)
    assert "timed out" in results[0].content
    assert isinstance(results[1], StopPropagation)
    assert job.is_running()
    assert list(bg.background_job_wait_hook(manager, False, [])) == []
    job.kill()
    late = list(bg.background_job_wait_hook(manager, False, []))
    assert isinstance(late[0], Message)
    assert "finished" in late[0].content


def test_reset_wakes_waiter_and_drops_late_callback() -> None:
    job = start_owned("sleep 30")
    entered = Event()
    results: list[Message | StopPropagation] = []
    condition = bg._job_conditions.setdefault("owner", Condition(bg._job_lock))
    original_wait = condition.wait

    def waiting(timeout: float | None = None) -> bool:
        entered.set()
        return original_wait(timeout)

    with patch.object(condition, "wait", waiting):
        waiter = Thread(
            target=lambda: results.extend(
                bg.background_job_wait_hook(SimpleNamespace(chat_id="owner"), False, [])
            )
        )
        waiter.start()
        assert entered.wait(timeout=3)
        bg.reset_background_jobs("owner", all_conversations=False)
        waiter.join(timeout=3)
    assert not waiter.is_alive()
    assert results == []
    assert not job.is_running()
    assert "owner" not in bg._job_conditions
    assert list(bg._completion_queue.queue) == []


@pytest.mark.parametrize("steer", [False, True])
def test_external_prompt_releases_wait_with_input(tmp_path: Path, steer: bool) -> None:
    from gptme.prompt_queue import queue_prompt

    start_owned("sleep 30")
    queue_prompt(tmp_path, "steer-now", steer=steer)
    results = list(
        bg.background_job_wait_hook(
            SimpleNamespace(chat_id="owner", logdir=tmp_path), False, []
        )
    )
    assert len(results) == 2
    assert isinstance(results[0], Message)
    assert results[0].role == "user"
    assert results[0].content == "steer-now"
    assert isinstance(results[1], StopPropagation)
    assert not (tmp_path / "prompt-queue.jsonl").exists()


def test_malformed_prompt_does_not_end_background_wait(tmp_path: Path) -> None:
    start_owned("sleep 0.1; printf FINISHED")
    queue = tmp_path / "prompt-queue.jsonl"
    queue.write_text('{"content":')
    results = list(
        bg.background_job_wait_hook(
            SimpleNamespace(chat_id="owner", logdir=tmp_path), False, []
        )
    )
    assert isinstance(results[0], Message)
    assert "FINISHED" in results[0].content
    assert queue.exists()


def test_control_file_reenters_step_checkpoint(tmp_path: Path) -> None:
    start_owned("sleep 30")
    (tmp_path / "control.jsonl").write_text('{"op":"cancel"}\n')
    results = list(
        bg.background_job_wait_hook(
            SimpleNamespace(chat_id="owner", logdir=tmp_path), False, []
        )
    )
    assert isinstance(results[0], Message)
    assert "pending session input" in results[0].content
    assert isinstance(results[1], StopPropagation)
    assert (tmp_path / "control.jsonl").exists()


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("recorded", [False, True])
def test_subagent_budget_or_cancel_ends_wait(
    tmp_path: Path, cancelled: bool, recorded: bool
) -> None:
    from gptme.tools.complete import SessionCompleteException
    from gptme.tools.subagent import types

    start_owned("sleep 30")
    cancel = Event()
    if cancelled:
        cancel.set()
    child = SimpleNamespace(
        agent_id="waiting-child",
        logdir=tmp_path,
        cancel_event=cancel,
        max_time=None if cancelled else 0,
        started_at=0,
    )
    earlier = types.ReturnType("cancelled", "Earlier terminal result")
    results: dict[str, types.ReturnType] = {child.agent_id: earlier} if recorded else {}
    with (
        patch.object(types, "_subagents", [child]),
        patch.object(types, "_subagent_results", results),
        pytest.raises(SessionCompleteException),
    ):
        list(
            bg.background_job_wait_hook(
                SimpleNamespace(chat_id="owner", logdir=tmp_path), False, []
            )
        )
    if recorded:
        assert results[child.agent_id] is earlier
    else:
        assert results[child.agent_id].status == (
            "cancelled" if cancelled else "timeout"
        )


def test_keyboard_interrupt_restores_interrupt_state() -> None:
    from gptme.util.interrupt import _interruptible_var

    start_owned("sleep 30")
    with (
        patch("threading.Condition.wait", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        list(bg.background_job_wait_hook(SimpleNamespace(chat_id="owner"), False, []))
    assert not _interruptible_var.get()


def test_pending_subagent_notification_yields_to_its_hook(tmp_path: Path) -> None:
    from queue import Queue

    from gptme.tools.subagent import types

    start_owned("sleep 30")
    completions: Queue[tuple[str, str, str]] = Queue()
    completions.put(("child", "success", "result"))
    with patch.object(types, "_completion_queue", completions):
        assert (
            list(
                bg.background_job_wait_hook(
                    SimpleNamespace(chat_id="owner", logdir=tmp_path), False, []
                )
            )
            == []
        )
    assert completions.get_nowait() == ("child", "success", "result")
