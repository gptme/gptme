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

    for msg in messages:
        if msg.metadata:
            if msg.role == "assistant":
                last_metadata = msg.metadata
                request_count += 1

            total_input += msg.metadata.get("input_tokens", 0)
            total_output += msg.metadata.get("output_tokens", 0)
            total_cache_read += msg.metadata.get("cache_read_tokens", 0)
            total_cache_created += msg.metadata.get("cache_creation_tokens", 0)
            total_cost += msg.metadata.get("cost", 0.0)

    # Check if we have any actual data
    has_data = (
        total_input > 0 or total_output > 0 or total_cache_read > 0 or total_cost > 0
    )

    if not has_data:
        return None

    # Last request from metadata
    last_request = None
    if last_metadata and (
        last_metadata.get("input_tokens", 0) > 0
        or last_metadata.get("output_tokens", 0) > 0
        or last_metadata.get("cost", 0) > 0
    ):
        last_request = RequestCosts(
            input_tokens=last_metadata.get("input_tokens", 0),
            output_tokens=last_metadata.get("output_tokens", 0),
            cache_read_tokens=last_metadata.get("cache_read_tokens", 0),
            cache_creation_tokens=last_metadata.get("cache_creation_tokens", 0),
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

    return CostData(last_request=last_request, total=total, source="conversation")


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
        console.log("[bold]Last Request:[/bold]")
        console.log(
            f"  Tokens:  {last_req.input_tokens:,} in / {last_req.output_tokens:,} out"
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


def _display_total(total: TotalCosts) -> None:
    """Helper to display total costs."""
    console.log(f"  Tokens:  {total.input_tokens:,} in / {total.output_tokens:,} out")
    console.log(
        f"  Cache:   {total.cache_read_tokens:,} read / {total.cache_creation_tokens:,} created"
    )
    console.log(f"  Hit rate: {total.cache_hit_rate * 100:.1f}%")
    console.log(f"  Cost:    ${total.cost:.4f}")
    console.log(f"  Requests: {total.request_count}")
