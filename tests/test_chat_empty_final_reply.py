"""An empty reply after the last tool result ends a headless run without `complete`.

gpt-6.1-sol answers "nothing left to do" with an empty message. Without the
`complete` tool, replaying that request repeated the empty answer up to the
recovery budget and then failed the run, although its work was done.
"""

import importlib
from unittest.mock import Mock

import pytest

from gptme import llm
from gptme.llm import EmptyStreamError, recovery
from gptme.logmanager import Log
from gptme.message import Message
from gptme.tools import init_tools

AFTER_TOOL = [
    Message("user", "write pick.json"),
    Message("assistant", "```shell\necho saved > pick.json\n```\n"),
    Message("system", "Ran command: saved"),
]


@pytest.fixture
def offline(monkeypatch):
    chat = importlib.import_module("gptme.chat")
    monkeypatch.setattr(llm, "init_llm", lambda *a: None)
    monkeypatch.setattr(llm, "is_output_quiet", lambda: True)
    monkeypatch.setattr(
        llm, "get_model", lambda *a: Mock(supports_parallel_tool_calls=False)
    )
    monkeypatch.setattr(chat, "prepare_messages", lambda messages, *a, **kw: messages)
    monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)
    monkeypatch.setattr(recovery, "get_max_retries", lambda: 3)
    monkeypatch.setattr(recovery, "backoff_wait", lambda *a: False)
    calls: list[int] = []
    finish: dict = {}

    def stream(messages, model, tools, **kwargs):
        calls.append(len(messages))

        def gen():
            yield from ()
            return {"model": model, **finish}

        return llm._StreamWithMetadata(gen(), model)

    monkeypatch.setattr(llm, "_stream", stream)
    chat._test_finish = finish  # type: ignore[attr-defined]
    return chat, calls


def _run_step(chat, messages, headless: bool):
    token = chat._headless_run.set(headless)
    try:
        return list(chat.step(list(messages), stream=True, model="openai/gpt-4o"))
    finally:
        chat._headless_run.reset(token)


def test_headless_without_complete_ends_after_one_request(offline):
    chat, calls = offline
    init_tools(allowlist=["shell"])

    msgs = _run_step(chat, AFTER_TOOL, headless=True)

    assert len(calls) == 1
    # A hidden stop marker, not an empty assistant message: a resume must
    # return to the prompt instead of generating again.
    assert [m.role for m in msgs] == ["system"]
    assert msgs[0].ui_only and msgs[0].hide
    assert msgs[0].metadata == {"session_stop_outcome": "completed"}
    assert chat._should_prompt_for_input(Log(AFTER_TOOL + msgs))


@pytest.mark.parametrize(
    "finish",
    [
        {"native_finish_reason": "MALFORMED_RESPONSE"},
        {"incomplete": True},
    ],
)
def test_failed_empty_reply_is_still_an_error(offline, finish):
    """A malformed or cut-off response is not the model ending its turn."""
    chat, calls = offline
    init_tools(allowlist=["shell"])
    chat._test_finish.update(finish)

    with pytest.raises(EmptyStreamError):
        _run_step(chat, AFTER_TOOL, headless=True)


def test_new_prompt_after_tool_turn_is_still_an_error(offline):
    """A newer prompt (here followed by a STEP_PRE-style notice) needs an answer."""
    chat, calls = offline
    init_tools(allowlist=["shell"])
    messages = AFTER_TOOL + [
        Message("user", "now summarise it"),
        Message("system", "workspace agent notice"),
    ]

    with pytest.raises(EmptyStreamError):
        _run_step(chat, messages, headless=True)

    assert len(calls) > 1


def test_complete_tool_loaded_keeps_replaying(offline):
    chat, calls = offline
    init_tools(allowlist=["shell", "complete"])

    with pytest.raises(EmptyStreamError):
        _run_step(chat, AFTER_TOOL, headless=True)

    assert len(calls) > 1


def test_interactive_keeps_replaying(offline):
    chat, calls = offline
    init_tools(allowlist=["shell"])

    with pytest.raises(EmptyStreamError):
        _run_step(chat, AFTER_TOOL, headless=False)

    assert len(calls) > 1


def test_empty_first_reply_is_still_an_error(offline):
    chat, calls = offline
    init_tools(allowlist=["shell"])

    with pytest.raises(EmptyStreamError):
        _run_step(chat, [Message("user", "write pick.json")], headless=True)

    assert len(calls) > 1


def test_flag_is_scoped_to_the_chat_loop():
    chat = importlib.import_module("gptme.chat")
    assert chat._headless_run.get() is False


def _mock_manager(tmp_path, messages):
    manager = Mock(current_branch="main", current_view=None)
    manager.log = Log(list(messages))
    manager.workspace = tmp_path
    manager.logdir = tmp_path
    manager.append.side_effect = lambda msg: setattr(
        manager, "log", manager.log.append(msg)
    )
    return manager


def test_headless_loop_ends_on_marker_but_waits_for_background_job(
    tmp_path, monkeypatch
):
    """Through the real headless loop: the flag is on inside step() and reset
    after, and an empty-reply end still lets a background shell job finish."""
    import shlex
    import sys

    from gptme.hooks import clear_hooks, current_conversation_id
    from gptme.logmanager import LogManager
    from gptme.tools import clear_tools, shell_background
    from gptme.tools.subagent.drain import DRAIN_TIMEOUT_ENV

    chat = importlib.import_module("gptme.chat")
    monkeypatch.setenv("GPTME_USER_CONFIG", str(tmp_path / "no-config.toml"))
    monkeypatch.delenv("GPTME_MAX_STEPS", raising=False)
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "0.1")
    clear_hooks()
    clear_tools()
    init_tools(["subagent"], include_mcp=False)
    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "false")
    manager = LogManager(list(AFTER_TOOL[:-1]), logdir=tmp_path / "conv", lock=False)
    headless: list[bool] = []
    jobs = []

    def step(*_a, **_kw):
        headless.append(chat._headless_run.get())
        if len(headless) == 1:
            command = (
                f"{shlex.quote(sys.executable)} -c "
                "'import time; time.sleep(0.3); print(654321)'"
            )
            jobs.append(shell_background.start_background_job(command))
            yield Message(
                "system",
                "Model ended the turn with an empty reply.",
                ui_only=True,
                hide=True,
                metadata={"session_stop_outcome": "completed"},
            )
        else:
            yield Message("assistant", "done")

    monkeypatch.setattr(chat, "step", step)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_a: False)
    token = current_conversation_id.set(manager.chat_id)
    shell_background.reset_background_jobs()
    try:
        chat._run_chat_loop(
            manager,
            [AFTER_TOOL[-1]],
            False,
            interactive=False,
            logdir=manager.logdir,
        )
    finally:
        current_conversation_id.reset(token)
        shell_background.reset_background_jobs()

    assert headless and all(headless)
    assert chat._headless_run.get() is False
    assert jobs[0].process.poll() == 0, "background job was killed"
    assert any("654321" in m.content for m in manager.log)


def test_turn_ends_once_on_the_empty_reply_marker(monkeypatch, tmp_path):
    """The loop must not regenerate from the previous turn's tool call."""
    chat = importlib.import_module("gptme.chat")
    manager = _mock_manager(tmp_path, AFTER_TOOL)
    steps: list[int] = []

    def step(*args, **kwargs):
        steps.append(1)
        yield Message(
            "system",
            "Model ended the turn with an empty reply.",
            ui_only=True,
            hide=True,
            metadata={"session_stop_outcome": "completed"},
        )

    monkeypatch.setattr(chat, "step", step)
    monkeypatch.setattr(chat, "trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda manager: False)
    monkeypatch.setattr(chat, "maybe_start_auto_naming", lambda *args: None)

    interrupted = chat._process_message_conversation(
        manager, stream=False, tool_format="markdown", model="openai/gpt-4o"
    )

    assert steps == [1]
    assert interrupted is False
    assert manager.log[-1].metadata == {"session_stop_outcome": "completed"}
