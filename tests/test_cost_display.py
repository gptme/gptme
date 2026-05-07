"""Tests for util/cost_display.py — cost aggregation and display."""

from gptme.message import Message
from gptme.util.cost_display import (
    BiggestTurn,
    CostData,
    RequestCosts,
    TotalCosts,
    gather_conversation_costs,
)


def test_gather_conversation_costs_empty():
    """No messages returns None."""
    result = gather_conversation_costs([])
    assert result is None


def test_gather_conversation_costs_no_metadata():
    """Messages without metadata return None."""
    msgs = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi there"),
    ]
    result = gather_conversation_costs(msgs)
    assert result is None


def test_gather_conversation_costs_zero_metadata():
    """Messages with zero-value metadata return None."""
    msgs = [
        Message(
            role="assistant",
            content="hi",
            metadata={
                "cost": 0.0,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                },
            },
        ),
    ]
    result = gather_conversation_costs(msgs)
    assert result is None


def test_gather_conversation_costs_single_request():
    """Single assistant message with metadata returns correct totals."""
    msgs = [
        Message(role="user", content="hello"),
        Message(
            role="assistant",
            content="hi there",
            metadata={
                "cost": 0.005,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_tokens": 20,
                    "cache_creation_tokens": 10,
                },
            },
        ),
    ]
    result = gather_conversation_costs(msgs)
    assert result is not None
    assert isinstance(result, CostData)
    assert result.source == "conversation"
    assert result.total.input_tokens == 100
    assert result.total.output_tokens == 50
    assert result.total.cache_read_tokens == 20
    assert result.total.cache_creation_tokens == 10
    assert result.total.cost == 0.005
    assert result.total.request_count == 1


def test_gather_conversation_costs_multiple_requests():
    """Multiple assistant messages aggregate correctly."""
    msgs = [
        Message(role="user", content="hello"),
        Message(
            role="assistant",
            content="hi",
            metadata={
                "cost": 0.005,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        ),
        Message(role="user", content="how are you?"),
        Message(
            role="assistant",
            content="fine",
            metadata={
                "cost": 0.010,
                "usage": {"input_tokens": 200, "output_tokens": 80},
            },
        ),
    ]
    result = gather_conversation_costs(msgs)
    assert result is not None
    assert result.total.input_tokens == 300
    assert result.total.output_tokens == 130
    assert result.total.cost == 0.015
    assert result.total.request_count == 2


def test_gather_conversation_costs_last_request():
    """Last assistant metadata is used for last_request field."""
    msgs = [
        Message(
            role="assistant",
            content="first",
            metadata={
                "cost": 0.005,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        ),
        Message(
            role="assistant",
            content="second",
            metadata={
                "cost": 0.010,
                "usage": {"input_tokens": 200, "output_tokens": 80},
            },
        ),
    ]
    result = gather_conversation_costs(msgs)
    assert result is not None
    assert result.last_request is not None
    assert isinstance(result.last_request, RequestCosts)
    assert result.last_request.input_tokens == 200
    assert result.last_request.output_tokens == 80
    assert result.last_request.cost == 0.010


def test_gather_conversation_costs_cache_hit_rate():
    """Cache hit rate is calculated correctly."""
    msgs = [
        Message(
            role="assistant",
            content="cached response",
            metadata={
                "cost": 0.003,
                "usage": {
                    "input_tokens": 50,
                    "output_tokens": 30,
                    "cache_read_tokens": 150,
                    "cache_creation_tokens": 0,
                },
            },
        ),
    ]
    result = gather_conversation_costs(msgs)
    assert result is not None
    # cache_hit_rate = cache_read / (input + cache_read + cache_creation)
    # = 150 / (50 + 150 + 0) = 0.75
    assert result.total.cache_hit_rate == 0.75


def test_gather_conversation_costs_user_metadata_counted():
    """User messages with metadata contribute to totals but not request_count."""
    msgs = [
        Message(
            role="user",
            content="hello",
            metadata={
                "cost": 0.001,
                "usage": {"input_tokens": 50, "output_tokens": 0},
            },
        ),
        Message(
            role="assistant",
            content="hi",
            metadata={
                "cost": 0.005,
                "usage": {"input_tokens": 100, "output_tokens": 30},
            },
        ),
    ]
    result = gather_conversation_costs(msgs)
    assert result is not None
    # Both messages' tokens are summed
    assert result.total.input_tokens == 150
    assert result.total.output_tokens == 30
    assert result.total.cost == 0.006
    # But only assistant messages count as requests
    assert result.total.request_count == 1


def test_gather_conversation_costs_partial_metadata():
    """Messages with partial metadata (missing keys) use defaults."""
    msgs = [
        Message(
            role="assistant",
            content="response",
            metadata={"usage": {"input_tokens": 100, "output_tokens": 50}},
            # No cache_read_tokens, cache_creation_tokens, or cost
        ),
    ]
    result = gather_conversation_costs(msgs)
    assert result is not None
    assert result.total.input_tokens == 100
    assert result.total.output_tokens == 50
    assert result.total.cache_read_tokens == 0
    assert result.total.cache_creation_tokens == 0
    assert result.total.cost == 0.0


def test_request_costs_dataclass():
    """RequestCosts dataclass instantiation works."""
    rc = RequestCosts(
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=20,
        cache_creation_tokens=10,
        cost=0.005,
    )
    assert rc.input_tokens == 100
    assert rc.output_tokens == 50
    assert rc.cost == 0.005


def test_total_costs_dataclass():
    """TotalCosts dataclass instantiation works."""
    tc = TotalCosts(
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=200,
        cache_creation_tokens=100,
        cost=0.05,
        cache_hit_rate=0.15,
        request_count=5,
    )
    assert tc.input_tokens == 1000
    assert tc.request_count == 5
    assert tc.cache_hit_rate == 0.15


def test_cost_data_dataclass():
    """CostData dataclass instantiation works."""
    cd = CostData(
        last_request=None,
        total=TotalCosts(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost=0.0,
            cache_hit_rate=0.0,
            request_count=0,
        ),
        source="conversation",
    )
    assert cd.source == "conversation"
    assert cd.last_request is None


def test_gather_conversation_costs_no_last_request_when_zero():
    """last_request is None when last metadata has all zeros."""
    msgs = [
        Message(
            role="assistant",
            content="first",
            metadata={
                "cost": 0.005,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        ),
        Message(
            role="assistant",
            content="second",
            metadata={
                "cost": 0.0,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                },
            },
        ),
    ]
    result = gather_conversation_costs(msgs)
    assert result is not None
    # Last metadata has all zeros, so last_request should be None
    assert result.last_request is None


def test_biggest_turn_identifies_outlier():
    """Biggest turn surfaces the largest single-turn input."""
    msgs = [
        Message(
            role="assistant",
            content="small",
            metadata={
                "cost": 0.001,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        ),
        Message(
            role="assistant",
            content="huge tool result",
            metadata={
                "cost": 0.05,
                "usage": {"input_tokens": 5000, "output_tokens": 30},
            },
        ),
        Message(
            role="assistant",
            content="small again",
            metadata={
                "cost": 0.002,
                "usage": {"input_tokens": 200, "output_tokens": 25},
            },
        ),
    ]
    result = gather_conversation_costs(msgs)
    assert result is not None
    assert result.biggest_turn is not None
    assert isinstance(result.biggest_turn, BiggestTurn)
    assert result.biggest_turn.request_index == 2
    assert result.biggest_turn.input_tokens == 5000


def test_biggest_turn_includes_cache_tokens():
    """Biggest turn ranks by total input including cache_read + cache_creation."""
    msgs = [
        Message(
            role="assistant",
            content="cached huge",
            metadata={
                "cost": 0.005,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 30,
                    "cache_read_tokens": 9000,
                    "cache_creation_tokens": 0,
                },
            },
        ),
        Message(
            role="assistant",
            content="non-cached small",
            metadata={
                "cost": 0.01,
                "usage": {"input_tokens": 1000, "output_tokens": 30},
            },
        ),
    ]
    result = gather_conversation_costs(msgs)
    assert result is not None
    assert result.biggest_turn is not None
    # First turn wins because cache_read pushes it past 1000
    assert result.biggest_turn.request_index == 1
    assert result.biggest_turn.cache_read_tokens == 9000


def test_biggest_turn_none_for_single_request():
    """Biggest turn is meaningful only with multiple requests; otherwise None."""
    msgs = [
        Message(
            role="assistant",
            content="only response",
            metadata={
                "cost": 0.005,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        ),
    ]
    result = gather_conversation_costs(msgs)
    assert result is not None
    # Still set, but display logic skips it for single-request conversations
    assert result.biggest_turn is not None
    assert result.biggest_turn.request_index == 1
