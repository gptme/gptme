"""Partial Anthropic usage can contain only cached input tokens."""

import pytest
from anthropic.types import Usage

from gptme.llm.llm_anthropic import _partial_stream_metadata
from gptme.message import Message
from gptme.util.cost_display import gather_conversation_costs, gather_per_step_costs


@pytest.mark.parametrize("cache_read", [False, True])
def test_cache_only_partial_usage_is_displayed(cache_read: bool) -> None:
    usage = Usage(
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=512 if cache_read else 0,
        cache_creation_input_tokens=0 if cache_read else 512,
    )
    metadata = _partial_stream_metadata("claude-sonnet-4-5", usage, use_thinking=False)
    messages = [Message("assistant", "partial response", metadata=metadata)]
    costs = gather_conversation_costs(messages)
    assert costs is not None
    assert costs.total.request_count == 1
    assert costs.last_request is not None
    assert costs.biggest_turn is not None
    assert costs.total.cache_read_tokens == (512 if cache_read else 0)
    assert costs.total.cache_creation_tokens == (0 if cache_read else 512)
    assert costs.last_request.cache_read_tokens == costs.total.cache_read_tokens
    assert costs.last_request.cache_creation_tokens == costs.total.cache_creation_tokens
    steps = gather_per_step_costs(messages)
    assert len(steps) == 1
    assert steps[0].cache_read_tokens == costs.total.cache_read_tokens
    assert steps[0].cache_creation_tokens == costs.total.cache_creation_tokens
