"""Tests for _fold_mid_system — folding non-leading system messages for
models/servers (e.g. Qwen3.5 on vLLM) that reject system messages anywhere
except the leading position.

See: https://github.com/gptme/gptme/issues/3779
"""

from unittest.mock import patch

import pytest

from gptme.llm.llm_openai import _fold_mid_system
from gptme.message import Message


def _sys(content: str) -> Message:
    return Message(role="system", content=content)


def _user(content: str) -> Message:
    return Message(role="user", content=content)


def _assistant(content: str) -> Message:
    return Message(role="assistant", content=content)


def _tool_result(content: str, call_id: str = "call-abc") -> Message:
    """A system-role message that is actually a tool result (has call_id)."""
    return Message(role="system", content=content, call_id=call_id)


# ---------------------------------------------------------------------------
# Core folding behaviour
# ---------------------------------------------------------------------------


def test_fold_mid_system_no_system_messages():
    """Conversations without system messages pass through unchanged."""
    msgs = [_user("hello"), _assistant("hi")]
    result = list(_fold_mid_system(msgs))
    assert [(m.role, m.content) for m in result] == [
        ("user", "hello"),
        ("assistant", "hi"),
    ]


def test_fold_mid_system_leading_only():
    """A single leading system message is kept as-is."""
    msgs = [_sys("You are a helpful assistant."), _user("hello")]
    result = list(_fold_mid_system(msgs))
    assert result[0].role == "system"
    assert result[0].content == "You are a helpful assistant."
    assert result[1].role == "user"


def test_fold_mid_system_folds_second_system():
    """A second system message (after a user turn) is folded to user-role."""
    msgs = [
        _sys("Initial system prompt."),
        _user("hello"),
        _sys("Injected context."),
        _assistant("response"),
    ]
    result = list(_fold_mid_system(msgs))
    assert result[0].role == "system", "leading system kept"
    assert result[1].role == "user"
    assert result[2].role == "user", "mid-conversation system folded to user"
    assert "<system>" in result[2].content
    assert "Injected context." in result[2].content
    assert result[3].role == "assistant"


def test_fold_mid_system_multiple_mid_system():
    """All non-leading system messages are folded, not just the second one."""
    msgs = [
        _sys("Initial."),
        _user("turn 1"),
        _sys("Injected 1."),
        _user("turn 2"),
        _sys("Injected 2."),
    ]
    result = list(_fold_mid_system(msgs))
    roles = [m.role for m in result]
    assert roles == ["system", "user", "user", "user", "user"]
    assert "<system>" in result[2].content
    assert "<system>" in result[4].content


def test_fold_mid_system_wrapping_format():
    """Folded messages are wrapped in <system>…</system>."""
    msgs = [_sys("A"), _user("hi"), _sys("B")]
    result = list(_fold_mid_system(msgs))
    folded = result[2]
    assert folded.content == "<system>\nB\n</system>"


def test_fold_mid_system_tool_results_unaffected():
    """System messages with call_id (tool results) are passed through as-is."""
    msgs = [
        _sys("System prompt."),
        _user("run tool"),
        _tool_result("tool output", call_id="call-1"),
    ]
    result = list(_fold_mid_system(msgs))
    # tool result keeps system role and is not wrapped
    tool_msg = result[2]
    assert tool_msg.role == "system"
    assert tool_msg.call_id == "call-1"
    assert "<system>" not in tool_msg.content


def test_fold_mid_system_no_leading_system():
    """When there is no leading system message, the first system encountered
    mid-conversation is still folded (it is non-leading)."""
    msgs = [_user("hello"), _sys("Injected."), _assistant("hi")]
    result = list(_fold_mid_system(msgs))
    assert result[0].role == "user"
    assert result[1].role == "user"
    assert "<system>" in result[1].content
    assert result[2].role == "assistant"


def test_fold_mid_system_consecutive_leading_systems():
    """Back-to-back system messages at the very start: only the first is kept
    as system; subsequent ones are folded (they are non-leading once there is
    already a leading system message)."""
    msgs = [
        _sys("First."),
        _sys("Second."),  # still at front, but not THE leading one
        _user("hello"),
    ]
    result = list(_fold_mid_system(msgs))
    assert result[0].role == "system"
    # second system is folded because leading_system_seen is True
    assert result[1].role == "user"
    assert "<system>" in result[1].content


# ---------------------------------------------------------------------------
# ModelMeta.supports_mid_system flag
# ---------------------------------------------------------------------------


def test_model_meta_default_supports_mid_system():
    """ModelMeta defaults to supports_mid_system=True (no change for existing models)."""
    from gptme.llm.models import get_model

    model = get_model("openai/gpt-4o")
    assert model.supports_mid_system is True


def test_model_meta_supports_mid_system_field():
    """ModelMeta can be instantiated with supports_mid_system=False."""
    from gptme.llm.models import ModelMeta

    m = ModelMeta(
        provider="local",
        model="Qwen/Qwen3.5-0.8B",
        context=32_768,
        supports_mid_system=False,
    )
    assert m.supports_mid_system is False


def test_get_model_infers_qwen35_does_not_support_mid_system():
    """Resolving a local Qwen3.5 id sets supports_mid_system=False without mocks.

    This is the activation path for vLLM/local endpoints (gptme/gptme#3779):
    no static registry entry exists, so inference has to fire on the fallback.
    """
    from gptme.llm.models import get_model, infer_supports_mid_system

    assert infer_supports_mid_system("Qwen/Qwen3.5-0.8B") is False
    assert infer_supports_mid_system("qwen3_5-4b") is False
    assert infer_supports_mid_system("qwen/qwen3-32b") is True
    assert infer_supports_mid_system("llama-3") is True

    model = get_model("local/Qwen/Qwen3.5-0.8B")
    assert model.provider == "local"
    assert model.supports_mid_system is False

    # Earlier Qwen3 family is unaffected
    qwen3 = get_model("local/Qwen/Qwen3-8B")
    assert qwen3.supports_mid_system is True


# ---------------------------------------------------------------------------
# Integration: _prepare_messages_for_api respects the flag and env var
# ---------------------------------------------------------------------------


def _make_qwen_model_meta():
    """Return a ModelMeta that mimics a Qwen3.5 local model."""
    from gptme.llm.models import ModelMeta

    return ModelMeta(
        provider="local",
        model="Qwen/Qwen3.5-0.8B",
        context=32_768,
        supports_mid_system=False,
    )


def _msgs_with_mid_system() -> list[Message]:
    return [
        _sys("System prompt."),
        _user("hello"),
        _sys("Injected context."),
        _user("follow-up"),
    ]


@pytest.mark.parametrize("env_value", ["1"])
def test_prepare_messages_folds_via_env_var(env_value, monkeypatch):
    """GPTME_FOLD_SYSTEM_MESSAGES=1 triggers folding regardless of ModelMeta."""
    from gptme.llm.llm_openai import _prepare_messages_for_api
    from gptme.llm.models import ModelMeta

    # a plain model that normally supports mid-system
    model_meta = ModelMeta(
        provider="local",
        model="some-local-model",
        context=32_768,
        supports_mid_system=True,
    )

    monkeypatch.setenv("GPTME_FOLD_SYSTEM_MESSAGES", env_value)
    with patch("gptme.llm.models.get_model", return_value=model_meta):
        result_dicts, _ = _prepare_messages_for_api(
            _msgs_with_mid_system(), "local/some-local-model", tools=None
        )

    roles = [d["role"] for d in result_dicts]
    # The third message (originally system, non-leading) should now be user
    assert roles[2] == "user", f"expected user at index 2, got {roles}"


def test_prepare_messages_folds_via_model_meta_flag():
    """ModelMeta.supports_mid_system=False triggers folding."""
    from gptme.llm.llm_openai import _prepare_messages_for_api

    model_meta = _make_qwen_model_meta()

    with patch("gptme.llm.models.get_model", return_value=model_meta):
        result_dicts, _ = _prepare_messages_for_api(
            _msgs_with_mid_system(), "local/Qwen/Qwen3.5-0.8B", tools=None
        )

    roles = [d["role"] for d in result_dicts]
    assert roles[2] == "user", f"expected user at index 2, got {roles}"


def test_prepare_messages_folds_via_real_qwen35_resolution(monkeypatch):
    """Unmocked get_model('local/Qwen/Qwen3.5-0.8B') activates folding."""
    from gptme.llm.llm_openai import _prepare_messages_for_api

    monkeypatch.delenv("GPTME_FOLD_SYSTEM_MESSAGES", raising=False)
    result_dicts, _ = _prepare_messages_for_api(
        _msgs_with_mid_system(), "local/Qwen/Qwen3.5-0.8B", tools=None
    )

    roles = [d["role"] for d in result_dicts]
    assert roles[0] == "system", "leading system kept"
    assert roles[2] == "user", f"expected folded user at index 2, got {roles}"


def test_openai_compat_listing_infers_qwen35_flag():
    """Dynamic OpenAI-compat listing stamps supports_mid_system from the model id."""
    from gptme.llm.llm_openai import _openai_compatible_model_to_modelmeta

    qwen = _openai_compatible_model_to_modelmeta(
        {"id": "Qwen/Qwen3.5-0.8B", "context_length": 32768}, "local"
    )
    assert qwen.supports_mid_system is False

    llama = _openai_compatible_model_to_modelmeta({"id": "llama-3"}, "local")
    assert llama.supports_mid_system is True


def test_prepare_messages_no_fold_when_supported(monkeypatch):
    """When supports_mid_system=True (default) and env var unset, no folding."""
    from gptme.llm.llm_openai import _prepare_messages_for_api
    from gptme.llm.models import ModelMeta

    model_meta = ModelMeta(
        provider="local",
        model="some-local-model",
        context=32_768,
        supports_mid_system=True,
    )

    monkeypatch.delenv("GPTME_FOLD_SYSTEM_MESSAGES", raising=False)
    with patch("gptme.llm.models.get_model", return_value=model_meta):
        result_dicts, _ = _prepare_messages_for_api(
            _msgs_with_mid_system(), "local/some-local-model", tools=None
        )

    roles = [d["role"] for d in result_dicts]
    assert roles[2] == "system", f"expected system at index 2, got {roles}"
