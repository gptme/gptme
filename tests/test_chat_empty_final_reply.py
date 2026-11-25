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
from gptme.message import Message, MessageMetadata
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


def test_checkpoint_tool_steps_still_owe_the_checkpoint(offline):
    """Autocompaction asked for a checkpoint; the model saved state with tools
    but has not written the checkpoint, so an empty reply stays an error."""
    chat, calls = offline
    init_tools(allowlist=["shell"])
    messages = AFTER_TOOL + [
        Message(
            "user",
            "Write a checkpoint of your progress.",
            metadata={"compaction_checkpoint_view": "main"},
        ),
        Message("assistant", "```shell\necho state > notes.md\n```\n"),
        Message("system", "Ran command: state"),
    ]

    with pytest.raises(EmptyStreamError):
        _run_step(chat, messages, headless=True)

    assert len(calls) > 1


def test_multi_step_tool_turn_ends_on_empty_reply(offline):
    chat, calls = offline
    init_tools(allowlist=["shell"])
    messages = AFTER_TOOL + [
        Message("assistant", "```shell\ncat pick.json\n```\n"),
        Message("system", "Ran command: saved"),
    ]

    msgs = _run_step(chat, messages, headless=True)

    assert len(calls) == 1
    assert msgs[0].metadata == {"session_stop_outcome": "completed"}


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


@pytest.mark.parametrize("interactive", [False, True])
def test_flag_is_scoped_to_the_chat_loop(monkeypatch, interactive):
    """The loop sets the flag for its body and resets it even when it raises."""
    chat = importlib.import_module("gptme.chat")
    seen: list[bool] = []

    def inner(*_a, **_kw):
        seen.append(chat._headless_run.get())
        raise RuntimeError("loop failed")

    monkeypatch.setattr(chat, "_run_chat_loop_inner", inner)
    token = chat._headless_run.set(interactive)  # the opposite of the loop's value
    try:
        with pytest.raises(RuntimeError):
            chat._run_chat_loop(Mock(), [], False, interactive=interactive)
        assert seen == [not interactive]
        assert chat._headless_run.get() is interactive
    finally:
        chat._headless_run.reset(token)


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

    from gptme.hooks import current_conversation_id, get_registry, set_registry
    from gptme.hooks.registry import HookRegistry
    from gptme.logmanager import LogManager
    from gptme.tools import clear_tools, shell_background
    from gptme.tools.subagent.drain import DRAIN_TIMEOUT_ENV

    chat = importlib.import_module("gptme.chat")
    monkeypatch.setenv("GPTME_USER_CONFIG", str(tmp_path / "no-config.toml"))
    monkeypatch.delenv("GPTME_MAX_STEPS", raising=False)
    monkeypatch.setenv(DRAIN_TIMEOUT_ENV, "0.1")
    previous_registry = get_registry()
    set_registry(HookRegistry())
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
        set_registry(previous_registry)

    assert headless and all(headless)
    assert chat._headless_run.get() is False
    assert jobs[0].process.poll() == 0, "background job was killed"
    # The completion notice repeats the command; the output is its own line.
    assert any("654321" in m.content.splitlines() for m in manager.log)


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


def _drain(gen):
    out = []
    try:
        while True:
            out.append(next(gen))
    except StopIteration as stop:
        return "".join(out), stop.value


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
def test_chat_completions_cutoff_is_not_a_normal_finish(monkeypatch, finish_reason):
    from types import SimpleNamespace

    from gptme.llm import llm_openai

    chunk = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                model_extra={},
                delta=SimpleNamespace(
                    reasoning_content=None, reasoning=None, content="", tool_calls=None
                ),
            )
        ],
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=Mock(return_value=[chunk]))
        )
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: False)

    monkeypatch.setattr(llm_openai, "_resolve_reasoning_effort", lambda *a: "high")

    text, metadata = _drain(
        llm_openai.stream([Message("user", "hi")], "openai/gpt-4o", None)
    )

    assert text == ""
    assert not EmptyStreamError("openai/gpt-4o", metadata).normal_finish
    # The request metadata fallback still runs for a cut-off without usage.
    assert metadata.get("reasoning_effort") == "high"


def test_chat_completions_stop_is_a_normal_finish(monkeypatch):
    from types import SimpleNamespace

    from gptme.llm import llm_openai

    chunk = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                model_extra={},
                delta=SimpleNamespace(
                    reasoning_content=None, reasoning=None, content="", tool_calls=None
                ),
            )
        ],
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=Mock(return_value=[chunk]))
        )
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: False)

    _, metadata = _drain(
        llm_openai.stream([Message("user", "hi")], "openai/gpt-4o", None)
    )

    assert EmptyStreamError("openai/gpt-4o", metadata).normal_finish


@pytest.mark.parametrize(
    ("terminal", "normal"),
    [
        (
            {
                "type": "response.incomplete",
                "response": {"incomplete_details": {"reason": "max_output_tokens"}},
            },
            False,
        ),
        ({"type": "response.completed", "response": {}}, True),
    ],
    ids=["incomplete", "completed"],
)
def test_subscription_incomplete_is_not_a_normal_finish(monkeypatch, terminal, normal):
    import json

    from gptme.llm import llm_openai_subscription as subscription

    class _Response:
        status_code = 200

        def iter_lines(self):
            yield f"data: {json.dumps(terminal)}".encode()

        def close(self):
            pass

    monkeypatch.setattr(
        subscription,
        "get_auth",
        lambda: subscription.SubscriptionAuth(
            access_token="test-token",
            refresh_token=None,
            account_id="test-account",
            expires_at=9_999_999_999.0,
        ),
    )
    monkeypatch.setenv("GPTME_SUBSCRIPTION_STREAM_RETRIES", "0")
    monkeypatch.setattr(
        subscription._http_session, "post", Mock(return_value=_Response())
    )

    text, metadata = _drain(subscription.stream([Message("user", "hello")], "gpt-5.4"))

    assert text == ""
    assert EmptyStreamError("gpt-5.4", metadata).normal_finish is normal


@pytest.mark.parametrize(
    ("usage", "normal"),
    [
        # gpt-5.6 zero-chunk glitch (tests/test_llm_empty_stream.py): billed
        # visible output that never streamed must be replayed, not end the turn.
        ({"output_tokens": 166}, False),
        ({"output_tokens": 800, "reasoning_tokens": 200}, False),
        # Reasoning-only output with no message is the model saying nothing.
        ({"output_tokens": 300, "reasoning_tokens": 300}, True),
        ({"output_tokens": 0}, True),
        ({"input_tokens": 50}, True),
        (None, True),
    ],
    ids=["unsplit", "visible", "reasoning-only", "zero", "no-output", "no-usage"],
)
def test_billed_visible_output_is_not_a_normal_finish(usage, normal):
    metadata: MessageMetadata = (
        {"model": "gpt-5.6"} if usage is None else {"usage": usage}
    )
    assert EmptyStreamError("gpt-5.6", metadata).normal_finish is normal
