"""Empty MALFORMED_* responses: one forced-tool-call retry, then a clear error.

gemini-4-neon via OpenRouter ended a tool-calling step with
``native_finish_reason=MALFORMED_RESPONSE`` and no output, identically on every
replay, so gptme burned 11 backoff retries. ``tool_choice="required"`` (Gemini's
ANY mode, constrained to valid calls) produced the intended calls.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from gptme.llm import EmptyStreamError, llm_openai
from gptme.llm.recovery import is_transient_provider_error
from gptme.message import Message
from gptme.tools.save import tool_save as save_tool

MODEL = "openai/gpt-4o"


def _choice(content=None, finish_reason=None, native=None, tool_calls=None):
    return SimpleNamespace(
        finish_reason=finish_reason,
        model_extra={"native_finish_reason": native} if native else {},
        delta=SimpleNamespace(
            reasoning_content=None,
            reasoning=None,
            content=content,
            tool_calls=tool_calls,
        ),
    )


def _chunk(**kw: Any) -> SimpleNamespace:
    return SimpleNamespace(usage=None, choices=[_choice(**kw)])


MALFORMED = [_chunk(content="", finish_reason="stop", native="MALFORMED_RESPONSE")]
OK = [_chunk(content="done"), _chunk(finish_reason="stop", native="STOP")]


def _client(monkeypatch, *responses: list) -> Mock:
    create = Mock(side_effect=list(responses))
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: False)
    return create


def _drain(gen) -> tuple[str, Any]:
    out = []
    try:
        while True:
            out.append(next(gen))
    except StopIteration as stop:
        return "".join(out), stop.value


def test_malformed_empty_response_retries_with_forced_tool_call(monkeypatch):
    create = _client(monkeypatch, MALFORMED, OK)
    text, metadata = _drain(
        llm_openai.stream([Message("user", "hi")], MODEL, [save_tool])
    )
    assert text == "done"
    assert create.call_count == 2
    assert "tool_choice" not in create.call_args_list[0].kwargs
    assert create.call_args_list[1].kwargs["tool_choice"] == "required"
    assert "native_finish_reason" not in (metadata or {})


def test_malformed_twice_records_native_reason(monkeypatch):
    create = _client(monkeypatch, MALFORMED, MALFORMED)
    text, metadata = _drain(
        llm_openai.stream([Message("user", "hi")], MODEL, [save_tool])
    )
    assert text == ""
    assert create.call_count == 2  # one forced retry, not more
    assert metadata["native_finish_reason"] == "MALFORMED_RESPONSE"


def test_malformed_without_tools_is_not_retried(monkeypatch):
    create = _client(monkeypatch, MALFORMED)
    _, metadata = _drain(llm_openai.stream([Message("user", "hi")], MODEL, None))
    assert create.call_count == 1
    assert metadata["native_finish_reason"] == "MALFORMED_RESPONSE"


def test_malformed_after_output_is_not_retried(monkeypatch):
    partial = [
        _chunk(content="partial"),
        _chunk(finish_reason="stop", native="MALFORMED_RESPONSE"),
    ]
    create = _client(monkeypatch, partial)
    text, _ = _drain(llm_openai.stream([Message("user", "hi")], MODEL, [save_tool]))
    assert text == "partial"
    assert create.call_count == 1


def _tagged(err: BaseException) -> BaseException:
    """Mark as raised by an LLM reply, as ``mark_llm_reply_origin`` does."""
    setattr(err, "_gptme_from_llm_reply", True)  # noqa: B010
    return err


def test_malformed_empty_stream_error_is_not_transient():
    err = _tagged(
        EmptyStreamError(MODEL, {"native_finish_reason": "MALFORMED_RESPONSE"})
    )
    assert "MALFORMED_RESPONSE" in str(err)
    assert not is_transient_provider_error(err)


def test_plain_empty_stream_error_stays_transient():
    err = _tagged(EmptyStreamError(MODEL, None))
    assert isinstance(err, httpx.RemoteProtocolError)
    assert is_transient_provider_error(err)


@pytest.mark.parametrize("native", ["MALFORMED_FUNCTION_CALL", "malformed_response"])
def test_malformed_variants(native):
    assert llm_openai._is_malformed_finish(native)
