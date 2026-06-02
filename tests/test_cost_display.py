"""Tests for util/cost_display.py — cost aggregation and display."""

from unittest.mock import patch

from gptme.message import Message
from gptme.util.cost_display import (
    BiggestTurn,
    CostData,
    RequestCosts,
    StepCost,
    TotalCosts,
    display_costs,
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


def test_display_costs_uses_explicit_cache_columns():
    """Per-step display labels cache reads/writes separately."""
    total = TotalCosts(
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=200,
        cache_creation_tokens=300,
        cost=0.00001,
        cache_hit_rate=0.4,
        request_count=1,
    )
    conversation = CostData(
        last_request=RequestCosts(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=200,
            cache_creation_tokens=300,
            cost=0.00001,
        ),
        total=total,
        source="conversation",
    )
    per_step = [
        StepCost(
            step_index=1,
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=200,
            cache_creation_tokens=300,
            cost=0.00001,
            model="anthropic/claude-haiku-4-5",
        )
    ]

    with patch("gptme.util.cost_display.console.log") as log:
        display_costs(conversation=conversation, per_step=per_step)

    output = "\n".join(str(call.args[0]) for call in log.call_args_list)
    assert "TotalIn" in output
    assert "Uncached" in output
    assert "CacheR" in output
    assert "CacheW" in output
    assert "Cache hit rate" in output
    assert "<$0.0001" in output
    assert "$0.0000" not in output


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


def test_biggest_turn_set_for_single_request():
    """gather_conversation_costs sets biggest_turn even for a single request.

    Suppression for single-request conversations is handled in display_costs,
    not in gather_conversation_costs.
    """
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
    assert result.biggest_turn is not None
    assert result.biggest_turn.request_index == 1


# --- Tests for gather_per_step_costs ---

from gptme.util.cost_display import gather_per_step_costs


def test_gather_per_step_costs_empty():
    """No messages returns empty list."""
    result = gather_per_step_costs([])
    assert result == []


def test_gather_per_step_costs_no_metadata():
    """Messages without metadata return empty list."""
    msgs = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi there"),
    ]
    result = gather_per_step_costs(msgs)
    assert result == []


def test_gather_per_step_costs_single_step():
    """Single assistant message with metadata returns one StepCost."""
    msgs = [
        Message(role="user", content="hello"),
        Message(
            role="assistant",
            content="hi",
            metadata={
                "model": "openrouter/deepseek/deepseek-v4-flash@deepseek",
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
    result = gather_per_step_costs(msgs)
    assert len(result) == 1
    assert result[0].step_index == 1
    assert result[0].input_tokens == 100
    assert result[0].output_tokens == 50
    assert result[0].cache_read_tokens == 20
    assert result[0].cache_creation_tokens == 10
    assert result[0].cost == 0.005
    assert result[0].model is not None


def test_gather_per_step_costs_multiple_steps():
    """Multiple assistant messages return multiple StepCosts with correct indices."""
    msgs = [
        Message(role="user", content="hello"),
        Message(
            role="assistant",
            content="first",
            metadata={
                "model": "anthropic/claude-sonnet-4-5",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        ),
        Message(role="user", content="follow-up"),
        Message(
            role="assistant",
            content="second",
            metadata={
                "model": "openrouter/deepseek/deepseek-v4-flash@deepseek",
                "usage": {"input_tokens": 200, "output_tokens": 80},
            },
        ),
    ]
    result = gather_per_step_costs(msgs)
    assert len(result) == 2
    assert result[0].step_index == 1
    assert result[0].input_tokens == 100
    assert result[1].step_index == 2
    assert result[1].input_tokens == 200


def test_gather_per_step_costs_cache_arithmetic():
    """Cache column (read+creation) + input + output == total."""
    msgs = [
        Message(
            role="assistant",
            content="response",
            metadata={
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_tokens": 200,
                    "cache_creation_tokens": 300,
                },
            },
        ),
    ]
    result = gather_per_step_costs(msgs)
    assert len(result) == 1
    s = result[0]
    # cache column = read + creation; total = input + output + cache_read + cache_creation
    assert s.cache_read_tokens + s.cache_creation_tokens == 500
    assert (
        s.input_tokens + s.output_tokens + s.cache_read_tokens + s.cache_creation_tokens
        == 650
    )


def test_gather_per_step_costs_skips_zero_input():
    """Messages with zero input AND zero output are skipped."""
    msgs = [
        Message(
            role="assistant",
            content="empty",
            metadata={
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                },
            },
        ),
        Message(
            role="assistant",
            content="real",
            metadata={
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        ),
    ]
    result = gather_per_step_costs(msgs)
    assert len(result) == 1
    assert result[0].input_tokens == 100


def test_gather_per_step_costs_skips_user_metadata():
    """Only assistant messages are counted; user messages with metadata are ignored."""
    msgs = [
        Message(
            role="user",
            content="system context loaded",
            metadata={
                "model": "openrouter/deepseek/deepseek-v4-flash@deepseek",
                "usage": {"input_tokens": 25000, "output_tokens": 0},
            },
        ),
        Message(
            role="assistant",
            content="working on it",
            metadata={
                "model": "openrouter/deepseek/deepseek-v4-flash@deepseek",
                "usage": {"input_tokens": 25000, "output_tokens": 100},
            },
        ),
    ]
    result = gather_per_step_costs(msgs)
    # Only the assistant message is a step; user metadata is ignored
    assert len(result) == 1
    assert result[0].step_index == 1
    assert result[0].input_tokens == 25000
    assert result[0].output_tokens == 100


# --- Tests for request_count consistency with per_step ---


def test_request_count_ignores_zero_token_assistant_messages():
    """Assistant messages with empty usage metadata do not inflate request_count.

    Matches gather_per_step_costs behaviour: only messages with real token data
    are counted.  Before the fix, empty-metadata assistant messages (e.g. from
    a prior session that ran before token tracking was added) caused
    request_count to be larger than the number of Per-Step Breakdown rows.
    """
    msgs = [
        # Old message from a session that did not record tokens
        Message(
            role="assistant",
            content="old response",
            metadata={"cost": 0.0, "usage": {}},
        ),
        # Old message likewise empty
        Message(
            role="assistant",
            content="also old",
            metadata={"model": "anthropic/claude-haiku-4-5"},
        ),
        # Current session request — has real token data
        Message(
            role="assistant",
            content="current response",
            metadata={
                "cost": 0.007,
                "usage": {
                    "input_tokens": 33_982,
                    "output_tokens": 203,
                    "cache_read_tokens": 31_738,
                    "cache_creation_tokens": 2_234,
                },
            },
        ),
    ]
    conv = gather_conversation_costs(msgs)
    per_step = gather_per_step_costs(msgs)

    assert conv is not None
    # Only the one message with real token data should count
    assert conv.total.request_count == 1
    # Per-step count must match
    assert len(per_step) == conv.total.request_count


def test_request_count_matches_per_step_count():
    """request_count from gather_conversation_costs equals len(gather_per_step_costs)."""
    msgs = [
        Message(
            role="assistant",
            content="step 1",
            metadata={"usage": {"input_tokens": 100, "output_tokens": 50}},
        ),
        Message(
            role="assistant",
            content="step 2",
            metadata={"usage": {"input_tokens": 200, "output_tokens": 80}},
        ),
        # Zero-token noise message
        Message(
            role="assistant",
            content="noise",
            metadata={"usage": {}},
        ),
    ]
    conv = gather_conversation_costs(msgs)
    per_step = gather_per_step_costs(msgs)

    assert conv is not None
    assert conv.total.request_count == len(per_step) == 2


# --- Tests for display_costs deduplication ---


def _make_total(requests: int, tokens: int = 1000) -> TotalCosts:
    return TotalCosts(
        input_tokens=tokens,
        output_tokens=100,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost=0.01,
        cache_hit_rate=0.0,
        request_count=requests,
    )


def test_display_costs_single_total_when_no_prior_history():
    """When session and conversation have the same request count, only one 'Total'
    block is shown (no redundant Session Total / Conversation Total split)."""
    session = CostData(last_request=None, total=_make_total(3), source="session")
    conversation = CostData(
        last_request=None, total=_make_total(3), source="conversation"
    )

    with patch("gptme.util.cost_display.console.log") as log:
        display_costs(session=session, conversation=conversation)

    output = "\n".join(str(call.args[0]) for call in log.call_args_list)
    # No session/conversation split when request counts match
    assert "Session Total" not in output
    assert "Conversation Total" not in output
    assert "Total" in output


def test_display_costs_shows_split_when_prior_history_exists():
    """When conversation has more requests than session, both blocks are shown."""
    session = CostData(last_request=None, total=_make_total(2), source="session")
    conversation = CostData(
        last_request=None, total=_make_total(5), source="conversation"
    )

    with patch("gptme.util.cost_display.console.log") as log:
        display_costs(session=session, conversation=conversation)

    output = "\n".join(str(call.args[0]) for call in log.call_args_list)
    assert "Session Total" in output
    assert "Conversation Total" in output


def test_display_costs_only_conversation_when_no_session():
    """With no session data, Conversation Total is shown without a split label."""
    conversation = CostData(
        last_request=None, total=_make_total(3), source="conversation"
    )

    with patch("gptme.util.cost_display.console.log") as log:
        display_costs(session=None, conversation=conversation)

    output = "\n".join(str(call.args[0]) for call in log.call_args_list)
    assert "Session Total" not in output
    # Shows a total block (either "Total" or "Conversation Total")
    assert "Total" in output
