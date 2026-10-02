"""Provider usage must trigger compaction even when stored text undercounts it."""

from gptme.message import Message, len_tokens
from gptme.tools.autocompact.decision import should_auto_compact
from gptme.util.context_measurement import input_log_digest


def test_provider_input_triggers_below_text_estimate():
    prefix = [Message("system", "Instructions"), Message("user", "Continue")]
    response = Message(
        "assistant",
        "Working",
        metadata={
            "usage": {
                "input_tokens": 10,
                "cache_read_tokens": 800,
                "cache_creation_tokens": 200,
            },
            "input_log_messages": len(prefix),
            "input_log_digest": input_log_digest(prefix),
        },
    )
    messages = [*prefix, response]
    assert len_tokens(messages, "gpt-4") < 1000
    assert should_auto_compact(messages, limit=1000) == "summarize"


def test_usage_plus_response_and_new_tool_result():
    from gptme.util.context_measurement import measure_context_tokens

    prefix = [Message("user", "Task")]
    response = Message(
        "assistant", "Result", metadata={"usage": {"input_tokens": 1000}}
    )
    from gptme.util.context_measurement import anchor_context_usage

    anchor_context_usage(
        response, len(prefix), input_log_digest(prefix), "openai/gpt-4"
    )
    tail = [response, Message("system", "tool output " * 100)]
    assert measure_context_tokens([*prefix, *tail], "openai/gpt-4") == (
        1000 + len_tokens(tail, "openai/gpt-4")
    )


def test_changed_prefix_invalidates_usage():
    from gptme.util.context_measurement import (
        anchor_context_usage,
        measure_context_tokens,
    )

    prefix = [Message("user", "Original task")]
    response = Message("assistant", "Done", metadata={"usage": {"input_tokens": 10000}})
    anchor_context_usage(response, 1, input_log_digest(prefix), "openai/gpt-4")
    for changed in (
        [Message("user", "New task"), response],
        [response],
        [Message("system", "New prompt"), *prefix, response],
    ):
        assert measure_context_tokens(changed, "openai/gpt-4") == len_tokens(
            changed, "openai/gpt-4"
        )


def test_model_change_and_legacy_usage_fall_back():
    from gptme.util.context_measurement import (
        anchor_context_usage,
        measure_context_tokens,
    )

    prefix = [Message("user", "Task")]
    response = Message("assistant", "Done", metadata={"usage": {"input_tokens": 10000}})
    messages = [*prefix, response]
    assert measure_context_tokens(messages, "openai/gpt-4") == len_tokens(
        messages, "openai/gpt-4"
    )
    anchor_context_usage(
        response, 1, input_log_digest(prefix), "anthropic/claude-sonnet"
    )
    assert measure_context_tokens(messages, "openai/gpt-4") == len_tokens(
        messages, "openai/gpt-4"
    )


def test_qualified_anchor_model_matches_and_provider_switch_invalidates():
    from gptme.util.context_measurement import (
        anchor_context_usage,
        measure_context_tokens,
    )

    prefix = [Message("user", "Task")]
    # The provider reports a bare model name that a sibling provider can share.
    response = Message(
        "assistant",
        "Done",
        metadata={
            "model": "claude-sonnet-4-5",
            "usage": {"input_tokens": 10000},
        },
    )
    anchor_context_usage(
        response, len(prefix), input_log_digest(prefix), "anthropic/claude-sonnet-4-5"
    )
    messages = [*prefix, response]
    assert measure_context_tokens(messages, "anthropic/claude-sonnet-4-5") >= 10000
    # Serving the same bare model name through another provider must not reuse
    # the previous provider's count: the usage semantics differ per provider.
    assert measure_context_tokens(
        messages, "openrouter/anthropic/claude-sonnet-4-5"
    ) == len_tokens(messages, "openrouter/anthropic/claude-sonnet-4-5")
    # A genuinely different model must still invalidate the anchor.
    assert measure_context_tokens(messages, "openai/gpt-4") == len_tokens(
        messages, "openai/gpt-4"
    )


def test_ui_status_is_not_growth():
    from gptme.util.context_measurement import (
        anchor_context_usage,
        measure_context_tokens,
    )

    prefix = [Message("user", "Task")]
    response = Message("assistant", "Done", metadata={"usage": {"input_tokens": 1000}})
    anchor_context_usage(response, 1, input_log_digest(prefix), "openai/gpt-4")
    messages = [*prefix, response]
    measured = measure_context_tokens(messages, "openai/gpt-4")
    messages.append(Message("system", "UI status " * 1000, ui_only=True))
    assert measure_context_tokens(messages, "openai/gpt-4") == measured


def test_cli_anchors_stored_log_not_prepared_messages(monkeypatch):
    # `import gptme.chat as chat` binds the re-exported chat() *function*
    # (gptme/__init__ lazy-exports it over the submodule), so resolve the
    # module explicitly.
    import importlib

    chat = importlib.import_module("gptme.chat")
    from gptme.logmanager import Log
    from gptme.util.context_measurement import measure_context_tokens

    stored = [Message("user", "First"), Message("user", "Second")]
    prepared = [Message("user", "First\nSecond")]
    response = Message("assistant", "Done", metadata={"usage": {"input_tokens": 1000}})
    monkeypatch.setattr(chat, "reply", lambda *args, **kwargs: response)
    log = Log(stored)
    result = chat._reply_with_overflow_recovery(
        log=log,
        msgs=prepared,
        model="openai/gpt-4",
        stream=False,
        tools=None,
        workspace=None,
        output_schema=None,
        on_token=None,
        on_thinking=None,
        logdir=None,
    )
    assert result.metadata is not None
    assert result.metadata["input_log_messages"] == len(log.messages)
    assert result.metadata["input_log_digest"] == input_log_digest(log.messages)
    assert measure_context_tokens([*log.messages, result], "openai/gpt-4") >= 1000


def test_cli_resolves_model_once_per_generation(monkeypatch):
    """Reuse one get_model() lookup for reply and usage anchoring.

    A second lookup after generation could retry a failing dynamic model
    catalog (OpenRouter/gptme) and fail the step after a successful reply.
    """
    import importlib

    chat = importlib.import_module("gptme.chat")
    from gptme.llm.models import get_model as real_get_model
    from gptme.logmanager import Log

    stored = [Message("user", "Hello")]
    response = Message("assistant", "Done", metadata={"usage": {"input_tokens": 1000}})
    calls: list[bool] = []
    generated = False

    def tracking_get_model(model):
        calls.append(generated)
        return real_get_model(model)

    def fake_reply(*args, **kwargs):
        nonlocal generated
        generated = True
        return response

    monkeypatch.setattr(chat, "get_model", tracking_get_model)
    monkeypatch.setattr(chat, "reply", fake_reply)
    result = chat._reply_with_overflow_recovery(
        log=Log(stored),
        msgs=stored,
        model="openai/gpt-4",
        stream=False,
        tools=None,
        workspace=None,
        output_schema=None,
        on_token=None,
        on_thinking=None,
        logdir=None,
    )
    assert generated
    assert calls == [False]  # resolved once, before generation
    assert result.metadata is not None
    assert result.metadata["input_log_messages"] == len(stored)


def test_input_digest_tracks_tool_pairs_and_files(tmp_path):
    message = Message(
        "system", "Result", call_id="call_1", files=[tmp_path / "one.txt"]
    )
    original = input_log_digest([message])
    assert input_log_digest([message.replace(call_id="call_2")]) != original
    assert input_log_digest([message.replace(files=[tmp_path / "two.txt"])]) != original


def test_overflow_retry_anchors_compacted_view(tmp_path, monkeypatch):
    import importlib

    import httpx

    from gptme.llm import mark_llm_reply_origin
    from gptme.logmanager import LogManager
    from gptme.util.context_measurement import measure_context_tokens

    chat = importlib.import_module("gptme.chat")
    manager = LogManager(
        [
            Message("system", "Prompt"),
            Message("user", "Task"),
            Message("system", "large output " * 1000),
        ],
        logdir=tmp_path / "conversation",
    )
    error = httpx.HTTPStatusError(
        "maximum context length exceeded",
        request=httpx.Request("POST", "https://example.test"),
        response=httpx.Response(400),
    )
    mark_llm_reply_origin(error)
    response = Message(
        "assistant", "Recovered", metadata={"usage": {"input_tokens": 1000}}
    )
    results = iter([error, response])

    def reply(*args, **kwargs):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(chat, "reply", reply)
    monkeypatch.setattr(chat, "prepare_messages", lambda messages, *a, **kw: messages)
    monkeypatch.setattr(
        "gptme.tools.autocompact.recovery.compact_for_overflow",
        lambda active: active.log.messages[:2],
    )
    result = chat._reply_with_overflow_recovery(
        log=manager.log,
        msgs=manager.log.messages,
        model="openai/gpt-4",
        stream=False,
        tools=None,
        workspace=None,
        output_schema=None,
        on_token=None,
        on_thinking=None,
        logdir=manager.logdir,
    )
    assert manager.current_view == "compacted-001"
    assert result.metadata is not None
    assert result.metadata["input_log_messages"] == 2
    assert result.metadata["input_log_digest"] == input_log_digest(manager.log.messages)
    assert (
        measure_context_tokens([*manager.log.messages, result], "openai/gpt-4") >= 1000
    )


def test_anchor_survives_log_reload(tmp_path):
    from gptme.logmanager import LogManager
    from gptme.util.context_measurement import (
        anchor_context_usage,
        measure_context_tokens,
    )

    manager = LogManager([Message("user", "Task")], logdir=tmp_path / "conversation")
    response = Message("assistant", "Done", metadata={"usage": {"input_tokens": 1000}})
    anchor_context_usage(
        response, 1, input_log_digest(manager.log.messages), "openai/gpt-4"
    )
    manager.append(response)
    loaded = LogManager.load(manager.logdir, lock=False)
    assert measure_context_tokens(loaded.log.messages, "openai/gpt-4") >= 1000
