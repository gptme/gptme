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


def test_provider_measured_over_budget_with_big_stale_tool_output_picks_rule_based():
    from gptme.llm.models import get_default_model, get_model
    from gptme.tools.autocompact import auto_compact_log
    from gptme.tools.autocompact.decision import estimate_compaction_savings
    from gptme.util.context_measurement import measure_context_tokens, prefix_digests

    model = get_default_model() or get_model("gpt-4")
    big = "\n".join(
        f"entry_{i} size={i * 13} checksum={i * 7919 % 10007}" for i in range(1500)
    )
    log = [
        Message("system", "You are a helpful assistant."),
        Message("user", "list things"),
        Message("assistant", "Running a listing."),
        Message("system", big, call_id="call_1"),
        Message("user", "now something else"),
        Message("assistant", "Sure, done."),
        Message("user", "and another thing"),
    ]
    anchor_idx = 5
    digests = prefix_digests(log)
    stored = len_tokens(log, model.model)
    # provider-reported input includes tool schemas etc. (~20k overhead)
    log[anchor_idx] = log[anchor_idx].replace(
        metadata={
            "usage": {"input_tokens": stored + 20_000},
            "input_log_messages": anchor_idx,
            "input_log_digest": digests[anchor_idx],
            "input_log_model": model.full,
        }
    )
    measured = measure_context_tokens(log, model.full)
    assert measured > stored + 19_000
    budget = stored + 5_000  # measured is over budget; stored text is under it
    assert measured >= budget > stored

    _, savings, _ = estimate_compaction_savings(log, limit=budget)
    assert savings > 0, "expected positive savings for reclaimable stale tool output"

    action = should_auto_compact(log, limit=budget)
    out = [m for m in auto_compact_log(log, limit=budget) if isinstance(m, Message)]
    trimmed = len_tokens(out, model.model)
    assert action == "rule_based", (
        f"got {action!r}: estimate saw 0 savings because it re-gated on stored-text tokens"
    )
    overhead = measured - stored
    assert trimmed < stored
    assert trimmed + overhead < budget


def test_provider_measured_over_budget_non_reclaimable_overhead_picks_summarize():
    """Even with a big stale tool output, summarize when overhead alone exceeds budget."""
    from gptme.llm.models import get_default_model, get_model
    from gptme.tools.autocompact.decision import (
        MIN_SAVINGS_RATIO,
        estimate_compaction_savings,
    )
    from gptme.util.context_measurement import measure_context_tokens, prefix_digests

    model = get_default_model() or get_model("gpt-4")
    big = "\n".join(
        f"entry_{i} size={i * 13} checksum={i * 7919 % 10007}" for i in range(1500)
    )
    log = [
        Message("system", "You are a helpful assistant."),
        Message("user", "list things"),
        Message("assistant", "Running a listing."),
        Message("system", big, call_id="call_1"),
        Message("user", "now something else"),
        Message("assistant", "Sure, done."),
        Message("user", "and another thing"),
    ]
    anchor_idx = 5
    digests = prefix_digests(log)
    stored = len_tokens(log, model.model)
    # Overhead (~30k) alone exceeds the ~23k budget; trimming stored text cannot fit.
    log[anchor_idx] = log[anchor_idx].replace(
        metadata={
            "usage": {"input_tokens": stored + 30_000},
            "input_log_messages": anchor_idx,
            "input_log_digest": digests[anchor_idx],
            "input_log_model": model.full,
        }
    )
    measured = measure_context_tokens(log, model.full)
    budget = stored + 5_000
    assert measured >= budget > stored

    total, savings, _ = estimate_compaction_savings(log, limit=budget)
    assert savings / total >= MIN_SAVINGS_RATIO
    assert measured - savings >= budget
    assert should_auto_compact(log, limit=budget) == "summarize"


def test_provider_measured_over_budget_insufficient_trim_picks_summarize():
    """When trimming frees >10% of stored text but still leaves measured >= budget, summarize."""
    from gptme.llm.models import get_default_model, get_model
    from gptme.tools.autocompact.decision import (
        MIN_SAVINGS_RATIO,
        estimate_compaction_savings,
    )
    from gptme.util.context_measurement import measure_context_tokens, prefix_digests

    model = get_default_model() or get_model("gpt-4")
    stale = "\n".join(f"item_{i} val={i * 7}" for i in range(400))
    # Non-trimmable user turns dominate stored text; overhead alone is below budget,
    # so summarization can satisfy the budget even though trimming cannot.
    non_trimmable = [
        Message("user", f"turn {i}: " + "alpha beta gamma delta " * 80)
        for i in range(12)
    ]
    log = [
        Message("system", "You are a helpful assistant."),
        Message("user", "list things"),
        Message("assistant", "Running a listing."),
        Message("system", stale, call_id="call_1"),
        *non_trimmable,
        Message("assistant", "Sure, done."),
        Message("user", "and another thing"),
    ]
    anchor_idx = len(log) - 2
    digests = prefix_digests(log)
    stored = len_tokens(log, model.model)
    overhead = 8_000
    log[anchor_idx] = log[anchor_idx].replace(
        metadata={
            "usage": {"input_tokens": stored + overhead},
            "input_log_messages": anchor_idx,
            "input_log_digest": digests[anchor_idx],
            "input_log_model": model.full,
        }
    )
    measured = measure_context_tokens(log, model.full)
    budget = stored + 4_000
    assert overhead < budget
    assert measured >= budget > stored

    total, savings, _ = estimate_compaction_savings(log, limit=budget)
    assert savings / total >= MIN_SAVINGS_RATIO
    assert measured - savings >= budget
    assert should_auto_compact(log, limit=budget) == "summarize"


def test_provider_measured_over_budget_phase2_non_stale_tool_output_trims():
    """Phase 2 truncation also respects provider overhead when Phase 0 does not prune."""
    from gptme.llm.models import get_default_model, get_model
    from gptme.tools.autocompact import TRIM_TARGET_RATIO, auto_compact_log
    from gptme.tools.autocompact.decision import estimate_compaction_savings
    from gptme.util.context_measurement import measure_context_tokens, prefix_digests

    model = get_default_model() or get_model("gpt-4")
    big = "\n".join(
        f"entry_{i} size={i * 13} checksum={i * 7919 % 10007}" for i in range(1500)
    )
    # Recent non-call_id system output (after assistant without tool call):
    # Phase 0 skips it, Phase 2 truncates it.
    log = [
        Message("system", "You are a helpful assistant."),
        Message("user", "list things"),
        Message("assistant", "Sure, done."),
        Message("system", big),
    ]
    anchor_idx = 2
    digests = prefix_digests(log)
    stored = len_tokens(log, model.model)
    log[anchor_idx] = log[anchor_idx].replace(
        metadata={
            "usage": {
                "input_tokens": len_tokens(log[:anchor_idx], model.model) + 20_000
            },
            "input_log_messages": anchor_idx,
            "input_log_digest": digests[anchor_idx],
            "input_log_model": model.full,
        }
    )
    measured = measure_context_tokens(log, model.full)
    budget = stored + 5_000
    assert measured >= budget > stored

    _, savings, _ = estimate_compaction_savings(log, limit=budget)
    assert savings > 0
    assert should_auto_compact(log, limit=budget) == "rule_based"

    out = [
        m
        for m in auto_compact_log(log, limit=budget, target_ratio=TRIM_TARGET_RATIO)
        if isinstance(m, Message)
    ]
    trimmed = len_tokens(out, model.model)
    overhead = measured - stored
    assert trimmed < stored
    assert trimmed + overhead < budget
