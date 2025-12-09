"""Cost tracking for LLM requests.

Provides session-level cost aggregation with context-safe storage,
enabling cost awareness during sessions and eval framework integration.

See Issue #935 for design context.
"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class CostEntry:
    """Single cost entry from an LLM request."""

    timestamp: float
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost: float


@dataclass
class CostSummary:
    """Typed cost summary for eval results and quick status checks.

    Provides structured access to aggregated cost metrics.
    """

    session_id: str
    total_cost: float
    total_input_tokens: int
    total_output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cache_hit_rate: float
    request_count: int

    @classmethod
    def from_dict(cls, data: dict) -> "CostSummary":
        """Create CostSummary from dictionary."""
        return cls(
            session_id=data.get("session_id", ""),
            total_cost=data.get("total_cost", 0.0),
            total_input_tokens=data.get("total_input_tokens", 0),
            total_output_tokens=data.get("total_output_tokens", 0),
            cache_read_tokens=data.get("cache_read_tokens", 0),
            cache_creation_tokens=data.get("cache_creation_tokens", 0),
            cache_hit_rate=data.get("cache_hit_rate", 0.0),
            request_count=data.get("request_count", 0),
        )

    def to_dict(self) -> dict:
        """Convert CostSummary to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "total_cost": self.total_cost,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_hit_rate": self.cache_hit_rate,
            "request_count": self.request_count,
        }


@dataclass
class SessionCosts:
    """Aggregated costs for a session."""

    session_id: str
    entries: list[CostEntry] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        """Total cost across all entries."""
        return sum(e.cost for e in self.entries)

    @property
    def total_input_tokens(self) -> int:
        """Total input tokens across all entries."""
        return sum(e.input_tokens for e in self.entries)

    @property
    def total_output_tokens(self) -> int:
        """Total output tokens across all entries."""
        return sum(e.output_tokens for e in self.entries)

    @property
    def total_cache_read_tokens(self) -> int:
        """Total cache read tokens across all entries."""
        return sum(e.cache_read_tokens for e in self.entries)

    @property
    def total_cache_creation_tokens(self) -> int:
        """Total cache creation tokens across all entries."""
        return sum(e.cache_creation_tokens for e in self.entries)

    @property
    def cache_hit_rate(self) -> float:
        """Cache hit rate as fraction of total input tokens.

        Calculation: cache_read_tokens / (input_tokens + cache_read_tokens + cache_creation_tokens)

        This measures cache efficiency across ALL input tokens:
        - input_tokens: tokens not requested for caching (e.g., single-turn hook context)
        - cache_read_tokens: tokens served from cache (hits)
        - cache_creation_tokens: tokens written to cache (misses, first-time processing)

        The denominator includes input_tokens because some content is intentionally
        not cached (like single-turn context from hooks that won't be sent again).
        This gives a more accurate picture of overall cache efficiency rather than
        just cache-specific metrics.

        Returns 0.0 if no input tokens have been processed.
        """
        total_input = (
            self.total_input_tokens
            + self.total_cache_read_tokens
            + self.total_cache_creation_tokens
        )
        if total_input == 0:
            return 0.0
        return self.total_cache_read_tokens / total_input

    @property
    def request_count(self) -> int:
        """Number of requests in this session."""
        return len(self.entries)


class CostTracker:
    """Track costs across a session with context-safe storage.

    Uses ContextVar for thread-safe, context-local storage suitable
    for concurrent sessions.

    Usage:
        # At session start
        CostTracker.start_session("my-session-id")

        # After each LLM request (called from telemetry)
        CostTracker.record(CostEntry(...))

        # Get current costs (for hooks or evals)
        summary = CostTracker.get_summary()
        costs = CostTracker.get_session_costs()
    """

    _session_costs_var: ContextVar[SessionCosts | None] = ContextVar(
        "session_costs", default=None
    )

    @classmethod
    def start_session(cls, session_id: str) -> None:
        """Initialize cost tracking for a session.

        Args:
            session_id: Unique identifier for the session (typically logdir path).
        """
        cls._session_costs_var.set(SessionCosts(session_id=session_id))

    @classmethod
    def record(cls, entry: CostEntry) -> None:
        """Record a cost entry.

        Safe to call even if session hasn't been started (will be a no-op).

        Args:
            entry: The cost entry to record.
        """
        costs = cls._session_costs_var.get()
        if costs:
            costs.entries.append(entry)

    @classmethod
    def get_session_costs(cls) -> SessionCosts | None:
        """Get current session costs.

        Returns:
            SessionCosts object if session is active, None otherwise.
            Used by evals to access detailed cost data.
        """
        return cls._session_costs_var.get()

    @classmethod
    def get_summary(cls) -> CostSummary | None:
        """Get cost summary for current session.

        Returns:
            CostSummary with aggregated cost metrics, None if no session.
            Used by cost_awareness hook and for quick status checks.
        """
        costs = cls._session_costs_var.get()
        if not costs:
            return None
        return CostSummary(
            session_id=costs.session_id,
            total_cost=costs.total_cost,
            total_input_tokens=costs.total_input_tokens,
            total_output_tokens=costs.total_output_tokens,
            cache_read_tokens=costs.total_cache_read_tokens,
            cache_creation_tokens=costs.total_cache_creation_tokens,
            cache_hit_rate=costs.cache_hit_rate,
            request_count=costs.request_count,
        )

    @classmethod
    def reset(cls) -> None:
        """Reset cost tracking (for testing)."""
        cls._session_costs_var.set(None)
