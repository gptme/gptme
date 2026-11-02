"""Retained logs must never be displayed as actual provider input."""

from typing import cast

import pytest

from gptme.message import Message, MessageMetadata, len_tokens
from gptme.util.context_measurement import (
    format_context_status,
    last_provider_input_tokens,
)


def test_oversized_log_with_small_request_usage() -> None:
    response = Message(
        "assistant",
        "Working",
        metadata={
            "input_log_model": "openai-subscription/gpt-4",
            "usage": {
                "input_tokens": 100,
                "cache_read_tokens": 700,
                "cache_creation_tokens": 200,
            },
        },
    )
    log = [response, Message("system", "CI output line\n" * 10000)]
    retained = len_tokens(log, "gpt-4")
    assert retained > 10000
    last_input = last_provider_input_tokens(log, "openai-subscription/gpt-4")
    assert last_input == 1000
    text = format_context_status(retained, 10000, last_input)
    assert "last input 1k/10k (10%)" in text
    assert f"log ~{retained // 1000}k" in text
    assert "remaining" not in text
    assert last_provider_input_tokens(log, "openai/gpt-4") is None


@pytest.mark.parametrize("count", [-1, True, "100"])
def test_invalid_provider_usage_is_unknown(count: object) -> None:
    log = [
        Message(
            "assistant",
            "Hello",
            # Deliberately malformed persisted data exercises runtime validation.
            metadata=cast(
                MessageMetadata,
                {
                    "input_log_model": "openai/gpt-4",
                    "usage": {"input_tokens": count},
                },
            ),
        )
    ]
    assert last_provider_input_tokens(log, "openai/gpt-4") is None


def test_unknown_usage_does_not_present_log_as_request() -> None:
    text = format_context_status(1900000, 1050000, None)
    assert "log ~1900k" in text
    assert "request usage unknown" in text
    assert "%" not in text


def test_provider_percentage_is_not_clamped() -> None:
    assert "(185%)" in format_context_status(1900, 1000, 1850)
