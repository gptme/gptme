"""Tests for Anthropic provider-native on-demand compaction."""

from unittest.mock import MagicMock, patch

from gptme.llm.llm_anthropic import _prepare_messages_for_api
from gptme.message import Message
from gptme.tools.autocompact import _resume_via_llm
from gptme.tools.autocompact import native_anthropic as native
from gptme.tools.autocompact.resume import _apply_native_compaction

BLOCK = {
    "type": "compaction",
    "content": "summary of old messages",
    "encrypted_content": "enc-123",
    "signature": "sig-abc",
}


def _mock_client(supported: bool) -> MagicMock:
    client = MagicMock()
    client.beta.models.retrieve.return_value = MagicMock(
        capabilities=MagicMock(
            compaction=MagicMock(
                supported=supported, summarize=MagicMock(supported=supported)
            )
        )
    )
    return client


def test_capability_lookup_supported(monkeypatch):
    client = _mock_client(True)
    monkeypatch.setattr(native, "_client", lambda: client)
    monkeypatch.setattr(native, "_capability_cache", {})
    assert native.anthropic_compaction_supported("claude-test") is True


def test_capability_lookup_unsupported(monkeypatch):
    client = _mock_client(False)
    monkeypatch.setattr(native, "_client", lambda: client)
    monkeypatch.setattr(native, "_capability_cache", {})
    assert native.anthropic_compaction_supported("claude-test") is False


def test_capability_lookup_error_falls_back(monkeypatch):
    client = _mock_client(True)
    client.beta.models.retrieve.side_effect = RuntimeError("api down")
    monkeypatch.setattr(native, "_client", lambda: client)
    monkeypatch.setattr(native, "_capability_cache", {})
    assert native.anthropic_compaction_supported("claude-test") is False


def test_capability_lookup_cached(monkeypatch):
    client = _mock_client(True)
    monkeypatch.setattr(native, "_client", lambda: client)
    monkeypatch.setattr(native, "_capability_cache", {})
    assert native.anthropic_compaction_supported("claude-test") is True
    client.beta.models.retrieve.side_effect = RuntimeError("should not re-call")
    assert native.anthropic_compaction_supported("claude-test") is True


def test_native_compact_returns_block_message(monkeypatch):
    response = MagicMock()
    block = MagicMock(
        type="compaction", content="summary", encrypted_content="enc", signature="sig"
    )
    response.content = [block]
    client = MagicMock()
    client.beta.messages.create.return_value = response
    monkeypatch.setattr(native, "_client", lambda: client)
    monkeypatch.setattr(
        "gptme.llm.llm_anthropic._prepare_messages_for_api",
        lambda msgs, tools, model=None: (
            [{"role": "user", "content": "old"}],
            [],
            None,
        ),
    )

    msgs = [Message("user", "old message")]
    result = native.anthropic_native_compact(msgs, "claude-test")
    assert result is not None
    assert result.role == "assistant"
    block_meta = result.metadata and result.metadata.get("anthropic_compaction_block")
    assert block_meta == {
        "type": "compaction",
        "content": "summary",
        "encrypted_content": "enc",
        "signature": "sig",
    }
    _, kwargs = client.beta.messages.create.call_args
    assert kwargs["betas"] == [native.COMPACT_BETA]
    assert kwargs["compaction"] == {"type": "summarize"}


def test_native_compact_no_signed_block_returns_none(monkeypatch):
    response = MagicMock()
    response.content = [MagicMock(type="text", text="hi")]
    client = MagicMock()
    client.beta.messages.create.return_value = response
    monkeypatch.setattr(native, "_client", lambda: client)
    monkeypatch.setattr(
        "gptme.llm.llm_anthropic._prepare_messages_for_api",
        lambda msgs, tools, model=None: ([], [], None),
    )
    assert (
        native.anthropic_native_compact([Message("user", "x")], "claude-test") is None
    )


def test_native_compact_request_failure_returns_none(monkeypatch):
    client = MagicMock()
    client.beta.messages.create.side_effect = RuntimeError("overloaded")
    monkeypatch.setattr(native, "_client", lambda: client)
    monkeypatch.setattr(
        "gptme.llm.llm_anthropic._prepare_messages_for_api",
        lambda msgs, tools, model=None: ([], [], None),
    )
    assert (
        native.anthropic_native_compact([Message("user", "x")], "claude-test") is None
    )


def test_replay_block_is_first_message_and_dropped_from_rest():
    block_msg = Message(
        "assistant",
        "[compacted]",
        metadata={"anthropic_compaction_block": dict(BLOCK)},
    )
    messages = [
        Message("system", "System prompt"),
        block_msg,
        Message("user", "recent question"),
    ]
    messages_dicts, _, _ = _prepare_messages_for_api(messages, None)
    assert messages_dicts[0]["role"] == "user"
    assert messages_dicts[0]["content"][0] == BLOCK
    # The block placeholder message must not be duplicated in the replay
    flat = str(messages_dicts)
    assert flat.count("sig-abc") == 1
    assert any("recent question" in str(m["content"]) for m in messages_dicts[1:])


def test_no_block_leaves_messages_unchanged():
    messages = [Message("system", "System prompt"), Message("user", "hi")]
    messages_dicts, _, _ = _prepare_messages_for_api(messages, None)
    assert all(
        not isinstance(part, dict) or part.get("type") != "compaction"
        for m in messages_dicts
        for part in (m["content"] if isinstance(m["content"], list) else [])
    )


def _conversation_msgs():
    return [
        Message("system", "System prompt"),
        Message("user", "Old question 1"),
        Message("assistant", "Old answer 1"),
        Message("user", "Old question 2"),
        Message("assistant", "Old answer 2"),
        Message("user", "Old question 3"),
        Message("assistant", "Old answer 3"),
        Message("user", "recent question"),
        Message("assistant", "recent answer"),
    ]


def test_apply_native_compaction_applies_view(monkeypatch):
    manager = MagicMock()
    manager.workspace = None
    manager.logdir = None
    manager.current_branch = "main"

    block_msg = Message(
        "assistant",
        "[compacted]",
        metadata={"anthropic_compaction_block": dict(BLOCK)},
    )
    monkeypatch.setattr(native, "anthropic_compaction_supported", lambda m: True)
    monkeypatch.setattr(native, "anthropic_native_compact", lambda *a, **k: block_msg)

    model_meta = MagicMock()
    model_meta.full = "anthropic/claude-test"
    model_meta.model = "claude-test"

    msgs = _conversation_msgs()
    from gptme.logmanager import prepare_messages

    prepared = prepare_messages(msgs)
    results = list(
        _apply_native_compaction(
            manager,
            prepared,
            use_view_branch=False,
            compact_instructions=None,
            keep_recent_tokens=1,
            keep_head=0,
            model_meta=model_meta,
            llm_unlocked=None,
        )
    )
    assert results[-1].role == "system"
    assert "Native compaction completed" in results[-1].content
    manager.write.assert_called_once()
    new_messages = manager.log.messages
    roles = [m.role for m in new_messages]
    assert roles[0] == "system"  # leading system block preserved
    assert any(
        m.metadata and m.metadata.get("anthropic_compaction_block")
        for m in new_messages
    )
    assert new_messages[-1] is next(
        m
        for m in new_messages
        if m.metadata and m.metadata.get("anthropic_compaction_block")
    )
    # Tail (kept verbatim) comes after the block, prefix does not reappear.
    assert not any(m.content == "Old answer 1" for m in new_messages)


def test_apply_native_compaction_unsupported_falls_back(monkeypatch):
    manager = MagicMock()
    manager.workspace = None
    manager.logdir = None
    manager.current_branch = "main"

    monkeypatch.setattr(native, "anthropic_compaction_supported", lambda m: False)
    model_meta = MagicMock()
    model_meta.full = "anthropic/claude-test"
    model_meta.model = "claude-test"

    from gptme.logmanager import prepare_messages

    prepared = prepare_messages(_conversation_msgs())
    applied = list(
        _apply_native_compaction(
            manager,
            prepared,
            use_view_branch=False,
            compact_instructions=None,
            keep_recent_tokens=1,
            keep_head=0,
            model_meta=model_meta,
            llm_unlocked=None,
        )
    )
    assert applied == []
    manager.write.assert_not_called()


def test_resume_via_llm_uses_native_path_when_available(monkeypatch):
    manager = MagicMock()
    manager.workspace = None
    manager.logdir = None
    manager.current_branch = "main"

    block_msg = Message(
        "assistant",
        "[compacted]",
        metadata={"anthropic_compaction_block": dict(BLOCK)},
    )
    monkeypatch.setattr(native, "anthropic_compaction_supported", lambda m: True)
    monkeypatch.setattr(native, "anthropic_native_compact", lambda *a, **k: block_msg)

    model_meta = MagicMock()
    model_meta.full = "anthropic/claude-test"
    model_meta.model = "claude-test"
    model_meta.context = 200_000
    model_meta.max_output = 8192

    with patch(
        "gptme.tools.autocompact.resume.get_default_model", return_value=model_meta
    ):
        results = list(
            _resume_via_llm(
                manager,
                _conversation_msgs(),
                use_view_branch=False,
                keep_recent_tokens=1,
            )
        )

    assert any("Native compaction completed" in r.content for r in results)
    # Generic summarize path must not have run: no "LLM-powered resume completed"
    assert not any("LLM-powered resume completed" in r.content for r in results)


def test_resume_via_llm_falls_back_to_generic_when_native_fails(monkeypatch):
    manager = MagicMock()
    manager.workspace = None
    manager.logdir = None
    manager.current_branch = "main"

    monkeypatch.setattr(native, "anthropic_compaction_supported", lambda m: True)
    monkeypatch.setattr(native, "anthropic_native_compact", lambda *a, **k: None)

    model_meta = MagicMock()
    model_meta.full = "anthropic/claude-test"
    model_meta.model = "claude-test"
    model_meta.context = 200_000
    model_meta.max_output = 8192

    resume_content = "# Resume\n## Summary\nWe discussed testing.\n"
    with patch("gptme.tools.autocompact.resume.llm") as mock_llm:
        mock_response = MagicMock()
        mock_response.content = resume_content
        mock_llm.reply.return_value = mock_response
        with patch(
            "gptme.tools.autocompact.resume.get_default_model",
            return_value=model_meta,
        ):
            results = list(
                _resume_via_llm(manager, _conversation_msgs(), use_view_branch=False)
            )

    assert any("LLM-powered resume completed" in r.content for r in results)


def test_block_round_trips_through_serialization():
    msg = Message(
        "assistant", "[compacted]", metadata={"anthropic_compaction_block": dict(BLOCK)}
    )
    import json

    # to_dict feeds the JSONL log; the signed block must survive losslessly.
    data = json.loads(json.dumps(msg.to_dict()))
    assert data["metadata"]["anthropic_compaction_block"] == BLOCK


def test_native_compact_conversion_failure_returns_none(monkeypatch):
    """A converter ValueError (e.g. missing leading system message) must fall
    back to the generic checkpoint instead of raising out of the caller."""
    client = MagicMock()
    monkeypatch.setattr(native, "_client", lambda: client)
    # Real conversion path: no leading system message -> _prepare_messages_for_api
    # raises ValueError. Must not propagate.
    msgs = [Message("user", "old message"), Message("assistant", "old answer")]
    assert native.anthropic_native_compact(msgs, "claude-test") is None
    client.beta.messages.create.assert_not_called()


def test_apply_native_compaction_supplies_system_head(monkeypatch):
    """The native converter receives the leading system block so the real
    _prepare_messages_for_api conversion succeeds (provider request is sent)."""
    manager = MagicMock()
    manager.workspace = None
    manager.logdir = None
    manager.current_branch = "main"

    response = MagicMock()
    block = MagicMock(
        type="compaction", content="summary", encrypted_content="enc", signature="sig"
    )
    response.content = [block]
    client = MagicMock()
    client.beta.messages.create.return_value = response
    monkeypatch.setattr(native, "_client", lambda: client)
    monkeypatch.setattr(native, "_capability_cache", {})
    monkeypatch.setattr(native, "anthropic_compaction_supported", lambda m: True)

    model_meta = MagicMock()
    model_meta.full = "anthropic/claude-test"
    model_meta.model = "claude-test"

    from gptme.logmanager import prepare_messages

    prepared = prepare_messages(_conversation_msgs())
    # sanity: real conversion of the system-first list must not raise
    results = list(
        _apply_native_compaction(
            manager,
            prepared,
            use_view_branch=False,
            compact_instructions=None,
            keep_recent_tokens=1,
            keep_head=0,
            model_meta=model_meta,
            llm_unlocked=None,
        )
    )
    assert any("Native compaction completed" in r.content for r in results)
    _, kwargs = client.beta.messages.create.call_args
    assert kwargs["betas"] == [native.COMPACT_BETA]
    # the summarized request must carry the system prompt separately
    assert kwargs["system"], "system head must be extracted into the system param"
    assert kwargs["messages"], "summarized body must be non-empty"
