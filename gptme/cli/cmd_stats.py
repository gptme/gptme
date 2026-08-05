"""CLI command for global LLM usage and cost analytics.

Aggregates usage across gptme's own stored conversation logs.

See also: ``gptme-sessions cost`` (gptme-contrib) for cross-backend analytics
(Claude Code, OpenRouter, Bedrock, etc.) and ``gptme-usage`` for pricing tables.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from ..logmanager.conversations import get_conversations


@dataclass
class ModelStats:
    """Statistics aggregated for a specific model."""

    model: str
    sessions: int = 0
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Total tokens across input and output."""
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        """Convert ModelStats to a dictionary."""
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d


@dataclass
class StatsSummary:
    """Aggregated global usage and cost statistics."""

    total_sessions: int = 0
    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    by_model: list[ModelStats] = field(default_factory=list)
    days: int | None = None

    @property
    def total_tokens(self) -> int:
        """Total tokens across all conversations."""
        return self.total_input_tokens + self.total_output_tokens

    @property
    def avg_cost_per_session(self) -> float:
        """Average cost per session in USD."""
        if self.total_sessions == 0:
            return 0.0
        return self.total_cost / self.total_sessions

    def to_dict(self) -> dict[str, Any]:
        """Convert StatsSummary to a dictionary for JSON serialization.

        Note: ``cache_read`` is a *subset* of ``input`` (cache reads are counted
        as input tokens), so the fields do not sum to ``total``.
        """
        return {
            "total_sessions": self.total_sessions,
            "total_cost": round(self.total_cost, 4),
            "total_tokens": {
                "total": self.total_tokens,
                "input": self.total_input_tokens,
                "output": self.total_output_tokens,
                "cache_read": self.total_cache_read_tokens,
            },
            "avg_cost_per_session": round(self.avg_cost_per_session, 4),
            "days": self.days,
            "by_model": [m.to_dict() for m in self.by_model],
        }


def gather_global_stats(
    days: int | None = None, include_test: bool = False
) -> StatsSummary:
    """Gather aggregated usage and cost statistics across stored conversation logs.

    Args:
        days: Optional number of past days to restrict statistics to (based on modification date).
        include_test: If True, include test/eval conversations in the statistics.

    Returns:
        StatsSummary containing total sessions, cost, token counts, and per-model breakdown.
    """
    cutoff_timestamp: float | None = None
    if days is not None:
        cutoff_timestamp = time.time() - (days * 86400)

    summary = StatsSummary(days=days)
    model_map: dict[str, ModelStats] = {}

    for conv in get_conversations(detail=True, include_test=include_test):
        if cutoff_timestamp is not None and conv.modified < cutoff_timestamp:
            continue

        summary.total_sessions += 1
        summary.total_cost += conv.total_cost
        summary.total_input_tokens += conv.total_input_tokens
        summary.total_output_tokens += conv.total_output_tokens
        summary.total_cache_read_tokens += conv.total_cache_read_tokens

        if conv.models_usage:
            for m_name, mu in conv.models_usage.items():
                if m_name not in model_map:
                    model_map[m_name] = ModelStats(model=m_name)
                ms = model_map[m_name]
                ms.sessions += 1
                ms.cost += mu.get("cost", 0.0)
                ms.input_tokens += mu.get("input_tokens", 0)
                ms.output_tokens += mu.get("output_tokens", 0)
                ms.cache_read_tokens += mu.get("cache_read_tokens", 0)
        else:
            model_name = conv.model or "unknown"
            if model_name not in model_map:
                model_map[model_name] = ModelStats(model=model_name)

            ms = model_map[model_name]
            ms.sessions += 1
            ms.cost += conv.total_cost
            ms.input_tokens += conv.total_input_tokens
            ms.output_tokens += conv.total_output_tokens
            ms.cache_read_tokens += conv.total_cache_read_tokens

    # Sort models by cost descending, then total tokens descending
    summary.by_model = sorted(
        model_map.values(), key=lambda m: (m.cost, m.total_tokens), reverse=True
    )

    return summary


def _format_tokens(count: int) -> str:
    """Format token count into a human-readable string."""
    return f"{count:,}"


def _format_cost(cost: float) -> str:
    """Format USD cost."""
    if cost == 0:
        return "$0.0000"
    if 0 < cost < 0.0001:
        return "<$0.0001"
    return f"${cost:.4f}"


def display_stats(summary: StatsSummary, console: Console | None = None) -> None:
    """Display aggregated statistics in a Rich formatted layout.

    Args:
        summary: Aggregated stats to render.
        console: Optional Rich Console instance to print to.
    """
    if console is None:
        console = Console()

    time_period = (
        f"Last {summary.days} days" if summary.days is not None else "All-time"
    )

    summary_table = Table(
        title=f"gptme Usage & Cost Analytics ({time_period})",
        show_header=False,
        box=None,
        padding=(0, 1),
    )
    summary_table.add_column("Property", style="bold cyan")
    summary_table.add_column("Value", style="white")

    summary_table.add_row("Total Sessions", str(summary.total_sessions))
    summary_table.add_row("Total Cost", _format_cost(summary.total_cost))
    summary_table.add_row(
        "Total Tokens",
        f"{_format_tokens(summary.total_tokens)} "
        f"([dim]in: {_format_tokens(summary.total_input_tokens)} "
        f"(incl. {_format_tokens(summary.total_cache_read_tokens)} cached) / "
        f"out: {_format_tokens(summary.total_output_tokens)}[/dim])",
    )
    summary_table.add_row(
        "Avg Cost/Session", _format_cost(summary.avg_cost_per_session)
    )

    console.print()
    console.print(summary_table)
    console.print()

    if not summary.by_model:
        console.print("[dim]No conversation logs found.[/dim]")
        return

    table = Table(
        title="Breakdown by Model", title_style="bold yellow", show_lines=True
    )
    table.add_column("Model", style="cyan")
    table.add_column("Chats", justify="right", style="green")
    table.add_column("Tokens", justify="right", style="white")
    table.add_column("Cost (USD)", justify="right", style="bold magenta")

    for ms in summary.by_model:
        table.add_row(
            ms.model,
            str(ms.sessions),
            _format_tokens(ms.total_tokens),
            _format_cost(ms.cost),
        )

    console.print(table)
    console.print()


@click.command("stats")
@click.option(
    "-d",
    "--days",
    type=click.IntRange(min=1),
    default=None,
    help="Filter statistics to conversations modified in the last N days.",
)
@click.option(
    "--include-test",
    is_flag=True,
    default=False,
    help="Include test/eval conversations in statistics.",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output statistics as JSON.",
)
def stats(days: int | None, include_test: bool, output_json: bool) -> None:
    """Show global LLM usage and cost statistics across all conversation logs.

    Cache-read tokens are included in the input-token count, not added on top.

    See also: ``gptme-sessions cost`` (gptme-contrib) for cross-backend
    analytics (Claude Code, OpenRouter, Bedrock, etc.) and ``gptme-usage`` for
    pricing tables.
    """
    summary = gather_global_stats(days=days, include_test=include_test)

    if output_json:
        click.echo(json.dumps(summary.to_dict(), indent=2))
    else:
        display_stats(summary)


if __name__ == "__main__":
    stats()
