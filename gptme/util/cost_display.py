"""Cost display utilities for unified cost reporting.

Provides functions to gather costs from multiple sources (CostTracker, metadata, approximation)
and display them in a consistent format.
"""

from dataclasses import dataclass

from ..message import Message
from . import console
from .cost_tracker import CostTracker


@dataclass
class RequestCosts:
    """Cost data for a single request."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost: float


@dataclass
class BiggestTurn:
    """Largest single-turn input observed in a conversation.

    Helps identify when a single tool result blows up the next turn's input.
    Indexed by assistant-message position (1-based) in the conversation.
    """

    request_index: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost: float


@dataclass
class TotalCosts:
    """Aggregated cost data."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost: float
    cache_hit_rate: float
    request_count: int


@dataclass
class CostData:
    """Complete cost information from a single source."""

    last_request: RequestCosts | None
    total: TotalCosts
    source: str  # "session" | "conversation" | "approximation"
    biggest_turn: BiggestTurn | None = None


def gather_session_costs() -> CostData | None:
    """Get costs from CostTracker (current session only).

    Returns:
        CostData if session tracking is active, None otherwise
    """
    costs = CostTracker.get_session_costs()
    if not costs or not costs.entries:
        return None

    # Last request
    last = costs.entries[-1]
    last_request = RequestCosts(
        input_tokens=last.input_tokens,
        output_tokens=last.output_tokens,
        cache_read_tokens=last.cache_read_tokens,
        cache_creation_tokens=last.cache_creation_tokens,
        cost=last.cost,
    )

    # Session totals
    total = TotalCosts(
        input_tokens=costs.total_input_tokens,
        output_tokens=costs.total_output_tokens,
        cache_read_tokens=costs.total_cache_read_tokens,
        cache_creation_tokens=costs.total_cache_creation_tokens,
        cost=costs.total_cost,
        cache_hit_rate=costs.cache_hit_rate,
        request_count=costs.request_count,
    )

    return CostData(last_request=last_request, total=total, source="session")


def gather_conversation_costs(messages: list[Message]) -> CostData | None:
    """Get costs from message metadata (entire conversation).

    Returns:
        CostData if metadata is available, None otherwise
    """
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_created = 0
    total_cost = 0.0
    request_count = 0
    last_metadata = None
    biggest_turn: BiggestTurn | None = None

    for msg in messages:
        if msg.metadata:
            usage = msg.metadata.get("usage", {})
            msg_input = usage.get("input_tokens", 0)
            msg_output = usage.get("output_tokens", 0)
            msg_cache_read = usage.get("cache_read_tokens", 0)
            msg_cache_created = usage.get("cache_creation_tokens", 0)
            msg_cost = msg.metadata.get("cost", 0.0)

            if msg.role == "assistant":
                last_metadata = msg.metadata
                request_count += 1

                turn_input_total = msg_input + msg_cache_read + msg_cache_created
                if biggest_turn is None or turn_input_total > (
                    biggest_turn.input_tokens
                    + biggest_turn.cache_read_tokens
                    + biggest_turn.cache_creation_tokens
                ):
                    biggest_turn = BiggestTurn(
                        request_index=request_count,
                        input_tokens=msg_input,
                        output_tokens=msg_output,
                        cache_read_tokens=msg_cache_read,
                        cache_creation_tokens=msg_cache_created,
                        cost=msg_cost,
                    )

            total_input += msg_input
            total_output += msg_output
            total_cache_read += msg_cache_read
            total_cache_created += msg_cache_created
            total_cost += msg_cost

    # Check if we have any actual data
    has_data = (
        total_input > 0 or total_output > 0 or total_cache_read > 0 or total_cost > 0
    )

    if not has_data:
        return None

    # Last request from metadata
    last_request = None
    if last_metadata:
        last_usage = last_metadata.get("usage", {})
        if (
            last_usage.get("input_tokens", 0) > 0
            or last_usage.get("output_tokens", 0) > 0
            or last_metadata.get("cost", 0) > 0
        ):
            last_request = RequestCosts(
                input_tokens=last_usage.get("input_tokens", 0),
                output_tokens=last_usage.get("output_tokens", 0),
                cache_read_tokens=last_usage.get("cache_read_tokens", 0),
                cache_creation_tokens=last_usage.get("cache_creation_tokens", 0),
                cost=last_metadata.get("cost", 0.0),
            )

    # Calculate cache hit rate
    total_input_with_cache = total_input + total_cache_read + total_cache_created
    cache_hit_rate = (
        (total_cache_read / total_input_with_cache)
        if total_input_with_cache > 0
        else 0.0
    )

    total = TotalCosts(
        input_tokens=total_input,
        output_tokens=total_output,
        cache_read_tokens=total_cache_read,
        cache_creation_tokens=total_cache_created,
        cost=total_cost,
        cache_hit_rate=cache_hit_rate,
        request_count=request_count,
    )

    # Suppress biggest_turn when the winning turn had zero total input tokens
    # (degenerate case; outlier-vs-average filtering happens in display_costs).
    if (
        biggest_turn is not None
        and request_count >= 2
        and (
            biggest_turn.input_tokens
            + biggest_turn.cache_read_tokens
            + biggest_turn.cache_creation_tokens
        )
        == 0
    ):
        biggest_turn = None

    return CostData(
        last_request=last_request,
        total=total,
        source="conversation",
        biggest_turn=biggest_turn,
    )


def display_costs(
    session: CostData | None = None, conversation: CostData | None = None
) -> None:
    """Display costs in unified format.

    Shows both session and conversation totals if both available,
    otherwise shows whichever is available.

    Args:
        session: Costs from current session (CostTracker)
        conversation: Costs from conversation history (metadata)
    """
    if not session and not conversation:
        console.log(
            "[yellow]No cost data available. Use /tokens for approximation.[/yellow]"
        )
        return

    # Show last request (prefer session, fall back to conversation)
    last_req = (session.last_request if session else None) or (
        conversation.last_request if conversation else None
    )

    if last_req:
        last_req_total_in = (
            last_req.input_tokens
            + last_req.cache_read_tokens
            + last_req.cache_creation_tokens
        )
        console.log("[bold]Last Request:[/bold]")
        console.log(
            f"  Tokens:  {last_req_total_in:,} in / {last_req.output_tokens:,} out"
        )
        console.log(
            f"  Cache:   {last_req.cache_read_tokens:,} read / {last_req.cache_creation_tokens:,} created"
        )
        console.log(f"  Cost:    ${last_req.cost:.4f}")
        console.log("")

    # Show session total if available
    if session:
        console.log("[bold]Session Total:[/bold] (current session)")
        _display_total(session.total)
        console.log("")

    # Show conversation total if available and different from session
    if conversation and (
        not session or conversation.total.request_count > session.total.request_count
    ):
        console.log("[bold]Conversation Total:[/bold] (all messages)")
        _display_total(conversation.total)

    # Highlight the largest single-turn input (helps catch context spikes,
    # e.g. when a tool result blows up the next turn's input).
    biggest = (
        conversation.biggest_turn
        if conversation and conversation.biggest_turn is not None
        else None
    )
    if biggest is not None and conversation and conversation.total.request_count >= 2:
        biggest_total_in = (
            biggest.input_tokens
            + biggest.cache_read_tokens
            + biggest.cache_creation_tokens
        )
        avg_input_per_request = (
            (
                conversation.total.input_tokens
                + conversation.total.cache_read_tokens
                + conversation.total.cache_creation_tokens
            )
            / conversation.total.request_count
            if conversation.total.request_count
            else 0
        )
        # Only flag if peak is at least 1.5x the average — otherwise it's noise
        if avg_input_per_request and biggest_total_in >= 1.5 * avg_input_per_request:
            ratio = biggest_total_in / avg_input_per_request
            console.log("")
            console.log(
                "[bold]Biggest Turn:[/bold] "
                f"request #{biggest.request_index} — "
                f"{biggest_total_in:,} in ({ratio:.1f}x avg)"
            )


def _display_total(total: TotalCosts) -> None:
    """Helper to display total costs."""
    total_in = (
        total.input_tokens + total.cache_read_tokens + total.cache_creation_tokens
    )
    console.log(f"  Tokens:  {total_in:,} in / {total.output_tokens:,} out")
    console.log(
        f"  Cache:   {total.cache_read_tokens:,} read / {total.cache_creation_tokens:,} created"
    )
    console.log(f"  Hit rate: {total.cache_hit_rate * 100:.1f}%")
    console.log(f"  Cost:    ${total.cost:.4f}")
    console.log(f"  Requests: {total.request_count}")
