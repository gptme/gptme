"""
List, search, and summarize past conversation logs.
"""

import json as json_mod
import logging
import re
import statistics
import textwrap
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from ..message import Message
from .base import ToolSpec, ToolUse

logger = logging.getLogger(__name__)


def _get_matching_messages(
    log_manager, query: str, system=False
) -> list[tuple[int, Message]]:
    """Get messages matching the query."""
    return [
        (i, msg)
        for i, msg in enumerate(log_manager.log)
        if query.lower() in msg.content.lower()
        if msg.role != "system" or system
    ]


def list_chats(
    max_results: int = 5, metadata=False, include_summary: bool = False
) -> None:
    """
    List recent chat conversations and optionally summarize them using an LLM.

    Args:
        max_results (int): Maximum number of conversations to display.
        include_summary (bool): Whether to include a summary of each conversation.
            If True, uses an LLM to generate a comprehensive summary.
            If False, uses a simple strategy showing snippets of the first and last messages.
    """
    from ..llm import summarize  # fmt: skip
    from ..logmanager import LogManager, list_conversations  # fmt: skip

    conversations = list_conversations(max_results)
    if not conversations:
        print("No conversations found.")
        return

    print(f"Recent conversations (showing up to {max_results}):")
    for i, conv in enumerate(conversations, 1):
        if metadata:
            print()  # Add a newline between conversations
        print(f"{i:2}. {textwrap.indent(conv.format(metadata=True), '    ')[4:]}")

        log_path = Path(conv.path)
        log_manager = LogManager.load(log_path, lock=False)

        # Use the LLM to generate a summary if requested
        if include_summary:
            summary = summarize(log_manager.log.messages)
            print(
                f"\n    Summary:\n{textwrap.indent(summary.content, '    > ', predicate=lambda _: True)}"
            )
            print()


def search_chats(
    query: str,
    max_results: int = 5,
    system=False,
    sort: Literal["date", "count"] = "date",
) -> None:
    """
    Search past conversation logs for the given query and print a summary of the results.

    Args:
        query (str): The search query.
        max_results (int): Maximum number of conversations to display.
        system (bool): Whether to include system messages in the search.
    """
    from ..logmanager import LogManager, list_conversations  # fmt: skip

    results: list[dict] = []
    for conv in list_conversations(10 * max_results):
        log_path = Path(conv.path)
        log_manager = LogManager.load(log_path, lock=False)

        matching_messages = _get_matching_messages(log_manager, query, system)

        if matching_messages:
            results.append(
                {
                    "conversation": conv,
                    "log_manager": log_manager,
                    "matching_messages": matching_messages,
                }
            )

    if not results:
        print(f"No results found for query: '{query}'")
        return

    # Sort results by the number of matching messages, in descending order
    if sort == "count":
        print("Sorting by number of matching messages")
        results.sort(key=lambda x: len(x["matching_messages"]), reverse=True)

    print(
        f"Search results for '{query}' ({len(results)} conversations, showing {min(max_results, len(results))}):"
    )
    for i, result in enumerate(results[:max_results], 1):
        conversation = result["conversation"]
        matches = result["matching_messages"][:1]
        match_strs = [
            _format_message_with_context(msg.content, query) for _, msg in matches
        ]
        print(
            f"{i}. {conversation.name} ({len(result['matching_messages'])}): {match_strs[0]}"
        )


def _format_message_with_context(
    content: str, query: str, context_size: int = 50, max_matches: int = 1
) -> str:
    """Format a message with context around matching query parts.

    Args:
        content: The message content to search in
        query: The search query
        context_size: Number of characters to show before and after match
        max_matches: Maximum number of matches to show

    Returns:
        Formatted string with highlighted matches and context
    """
    content_lower = content.lower()
    query_lower = query.lower()

    # Find all occurrences of the query
    matches = []
    start = 0
    while True:
        idx = content_lower.find(query_lower, start)
        if idx == -1:
            break
        matches.append(idx)
        start = idx + len(query_lower)

    if not matches:
        return content[:100] + "..." if len(content) > 100 else content

    # Format matches with context
    formatted_matches = []
    for match_idx in matches[:max_matches]:
        # Extract context window
        context_start = max(0, match_idx - context_size)
        context_end = min(len(content), match_idx + len(query) + context_size)
        context = content[context_start:context_end]

        # Add ellipsis if truncated
        prefix = "..." if context_start > 0 else ""
        suffix = "..." if context_end < len(content) else ""

        # Highlight the match
        match_start = match_idx - context_start
        match_end = match_start + len(query)

        # Only show line context
        context_prefix = context[:match_start].rsplit("\n", 1)[-1]
        context_suffix = context[match_end:].split("\n", 1)[0]
        context = f"{context_prefix}{context[match_start:match_end]}{context_suffix}"

        highlighted = f"{prefix}{context}{suffix}"
        highlighted = re.sub(
            re.escape(query),
            lambda m: "\033[1;31m" + str(m.group()) + "\033[0m",
            highlighted,
            flags=re.IGNORECASE,
        )
        formatted_matches.append(highlighted)

    result = " ".join(formatted_matches)
    if len(matches) > max_matches:
        result += f" (+{len(matches) - max_matches})"

    return result


def read_chat(id: str, max_results: int = 5, incl_system=False) -> None:
    """
    Read a specific conversation log.

    Args:
        id (str): The id of the conversation to read.
        max_results (int): Maximum number of messages to display.
        incl_system (bool): Whether to include system messages.
    """
    from ..logmanager import LogManager, list_conversations  # fmt: skip

    for conv in list_conversations():
        if conv.id == id:
            log_path = Path(conv.path)
            logmanager = LogManager.load(log_path)
            print(f"Reading conversation: {conv.name} ({conv.id})")
            i = 0
            for msg in logmanager.log:
                if msg.role != "system" or incl_system:
                    print(f"{i}. {msg.format(max_length=100)}")
                    i += 1
                if i >= max_results:
                    break
            break
    else:
        print(f"Conversation '{id}' not found.")


def find_empty_conversations(
    max_messages: int = 1,
    include_test: bool = False,
) -> list[dict]:
    """Find conversations with few or no messages.

    Scans all conversations and returns those with at most `max_messages` messages.
    Useful for cleaning up abandoned or empty conversation logs.

    Args:
        max_messages: Maximum message count to consider "empty" (default: 1, system-only).
        include_test: Whether to include test/eval conversations.

    Returns:
        List of dicts with conversation metadata and disk size.
    """
    from ..logmanager import get_conversations, get_user_conversations  # fmt: skip

    conversation_iter = (
        get_conversations() if include_test else get_user_conversations()
    )

    results = []
    for conv in conversation_iter:
        # Skip conversations with branch history — conv.messages only counts the main
        # branch, so a conversation with few main-branch messages but significant branch
        # history could be incorrectly flagged as empty.
        if conv.messages <= max_messages and conv.branches <= 1:
            # Calculate disk usage for the conversation directory
            conv_dir = Path(conv.path).parent
            try:
                size_bytes = sum(
                    f.stat().st_size for f in conv_dir.rglob("*") if f.is_file()
                )
            except OSError:
                size_bytes = 0

            results.append(
                {
                    "conversation": conv,
                    "size_bytes": size_bytes,
                }
            )

    return results


def _format_tokens(n: int) -> str:
    """Format token count with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _parse_since(since: str | None) -> float | None:
    """Parse a --since argument into a timestamp.

    Supports:
        - YYYY-MM-DD: specific date
        - Nd: N days ago (e.g. 7d, 30d)

    Returns None if since is None.
    """
    if since is None:
        return None

    # Try "Nd" format (days ago)
    if since.endswith("d") and since[:-1].isdigit():
        days = int(since[:-1])
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        return cutoff.timestamp()

    # Try YYYY-MM-DD format
    try:
        dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        pass

    raise ValueError(
        f"Invalid --since format: '{since}'. Use YYYY-MM-DD or Nd (e.g. 7d, 30d)."
    )


def conversation_stats(since: str | None = None, as_json: bool = False) -> None:
    """Show statistics about conversation history.

    Args:
        since: Only include conversations since this date (YYYY-MM-DD or Nd).
        as_json: Output as JSON instead of formatted text.
    """
    from ..logmanager import get_user_conversations  # fmt: skip

    since_ts = _parse_since(since)

    # Collect stats
    total_conversations = 0
    total_messages = 0
    oldest_ts: float | None = None
    newest_ts: float | None = None
    daily_counts: Counter[str] = Counter()
    agent_counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    model_cost: dict[str, float] = {}
    model_input_tokens: Counter[str] = Counter()
    model_output_tokens: Counter[str] = Counter()
    daily_cost: dict[str, float] = {}
    total_cost = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_tokens = 0
    messages_list: list[int] = []

    for conv in get_user_conversations():
        # Conversations are sorted newest-first by modification time.
        # If filtering by --since, stop once we pass the cutoff.
        if since_ts and conv.modified < since_ts:
            break

        total_conversations += 1
        total_messages += conv.messages
        messages_list.append(conv.messages)

        # Track date range.
        # When filtering by --since (which uses conv.modified), also use modified for
        # display so the "Oldest" date and histogram align with the filtered window.
        ts_for_display = conv.modified if since_ts else conv.created
        if oldest_ts is None or ts_for_display < oldest_ts:
            oldest_ts = ts_for_display
        if newest_ts is None or conv.modified > newest_ts:
            newest_ts = conv.modified

        # Daily activity
        day = datetime.fromtimestamp(ts_for_display, tz=timezone.utc).strftime(
            "%Y-%m-%d"
        )
        daily_counts[day] += 1

        # Agent breakdown
        agent = conv.agent_name or "interactive"
        agent_counts[agent] += 1

        # Model breakdown and cost/token tracking
        model = conv.model or "unknown"
        model_counts[model] += 1
        model_cost[model] = model_cost.get(model, 0.0) + conv.total_cost
        model_input_tokens[model] += conv.total_input_tokens
        model_output_tokens[model] += conv.total_output_tokens
        total_cost += conv.total_cost
        total_input_tokens += conv.total_input_tokens
        total_output_tokens += conv.total_output_tokens
        total_cache_read_tokens += conv.total_cache_read_tokens
        daily_cost[day] = daily_cost.get(day, 0.0) + conv.total_cost

    if total_conversations == 0:
        if since:
            print(f"No conversations found since {since}.")
        else:
            print("No conversations found.")
        return

    # Compute derived stats
    avg_messages = total_messages / total_conversations if total_conversations else 0
    median_messages = statistics.median(messages_list) if messages_list else 0

    # Recent activity (last 7 and 30 days)
    now = datetime.now(tz=timezone.utc)

    # Histogram window: match the --since window (or default to 14 days).
    # Cap at 365 to avoid thousands of output lines for old --since dates.
    if since_ts:
        hist_days = min(
            365,
            max(1, (now - datetime.fromtimestamp(since_ts, tz=timezone.utc)).days),
        )
    else:
        hist_days = 14

    last_7d = sum(
        count
        for day, count in daily_counts.items()
        if (now - datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days
        < 7
    )
    last_30d = sum(
        count
        for day, count in daily_counts.items()
        if (now - datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days
        < 30
    )

    oldest_dt = (
        datetime.fromtimestamp(oldest_ts, tz=timezone.utc) if oldest_ts else None
    )
    newest_dt = (
        datetime.fromtimestamp(newest_ts, tz=timezone.utc) if newest_ts else None
    )

    if as_json:
        data: dict = {
            "total_conversations": total_conversations,
            "total_messages": total_messages,
            "avg_messages_per_conversation": round(avg_messages, 1),
            "median_messages_per_conversation": median_messages,
            "oldest": oldest_dt.isoformat() if oldest_dt else None,
            "newest": newest_dt.isoformat() if newest_dt else None,
            "total_cost": round(total_cost, 4),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cache_read_tokens": total_cache_read_tokens,
            "by_agent": dict(agent_counts.most_common()),
            "by_model": {
                m: {
                    "conversations": model_counts[m],
                    "cost": round(model_cost[m], 4),
                    "input_tokens": model_input_tokens[m],
                    "output_tokens": model_output_tokens[m],
                }
                for m in sorted(
                    model_counts, key=lambda m: model_counts.get(m, 0), reverse=True
                )
            },
            "by_day": {
                (now - timedelta(days=i)).strftime("%Y-%m-%d"): daily_counts.get(
                    (now - timedelta(days=i)).strftime("%Y-%m-%d"), 0
                )
                for i in range(hist_days)
            },
        }
        if since:
            data["since"] = since
        else:
            # Only include last_7d/last_30d when not filtering — otherwise they'd
            # equal total_conversations and be misleading.
            data["conversations_last_7d"] = last_7d
            data["conversations_last_30d"] = last_30d
        print(json_mod.dumps(data, indent=2))
        return

    # Formatted output
    since_label = f" (since {since})" if since else ""
    print(f"Conversation Statistics{since_label}")
    print("=" * 40)
    print(f"  Total conversations:  {total_conversations:,}")
    print(f"  Total messages:       {total_messages:,}")
    print(f"  Avg messages/conv:    {avg_messages:.1f}")
    print(f"  Median messages/conv: {median_messages}")
    if oldest_dt:
        print(f"  Oldest:               {oldest_dt:%Y-%m-%d %H:%M}")
    if newest_dt:
        print(f"  Newest:               {newest_dt:%Y-%m-%d %H:%M}")

    # Only show last-7d/last-30d when not filtering; when --since is set the
    # data is already scoped and these values would just repeat the total.
    if not since:
        print("\nRecent Activity")
        print(f"  Last 7 days:   {last_7d} conversations")
        print(f"  Last 30 days:  {last_30d} conversations")

    if agent_counts:
        print("\nBy Agent")
        for agent, count in agent_counts.most_common(10):
            pct = count / total_conversations * 100
            print(f"  {agent:20s}  {count:5,} ({pct:5.1f}%)")

    # Model breakdown with cost and tokens
    known_models = {m: c for m, c in model_counts.items() if m != "unknown"}
    if known_models:
        print("\nBy Model")
        top_models = sorted(
            known_models, key=lambda m: known_models.get(m, 0), reverse=True
        )[:15]
        for model in top_models:
            count = known_models[model]
            cost = model_cost[model]
            in_tok = model_input_tokens[model]
            out_tok = model_output_tokens[model]
            tok_str = _format_tokens(in_tok + out_tok)
            cost_str = f"${cost:.2f}" if cost >= 0.01 else f"${cost:.4f}"
            print(f"  {model:40s}  {count:4,} convs  {tok_str:>8s} tok  {cost_str:>8s}")

    # Token and cost summary
    if total_input_tokens or total_cost:
        cache_pct = (
            total_cache_read_tokens / total_input_tokens * 100
            if total_input_tokens
            else 0
        )
        print("\nToken Usage & Cost")
        print(
            f"  Input tokens:   {_format_tokens(total_input_tokens):>10s}  ({cache_pct:.0f}% cached)"
        )
        print(f"  Output tokens:  {_format_tokens(total_output_tokens):>10s}")
        print(
            f"  Total tokens:   {_format_tokens(total_input_tokens + total_output_tokens):>10s}"
        )
        print(f"  Total cost:     ${total_cost:>9.2f}")

    # Show daily breakdown for the histogram window
    print(f"\nDaily Activity (last {hist_days} days)")
    for i in range(hist_days):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        count = daily_counts.get(day, 0)
        bar = "#" * min(count, 50)
        weekday = (now - timedelta(days=i)).strftime("%a")
        print(f"  {day} ({weekday}): {count:3d} {bar}")


def examples(tool_format):
    return f"""
### Search for a specific topic in past conversations
User: Can you find any mentions of "python" in our past conversations?
Assistant: Certainly! I'll search our past conversations for mentions of "python" using the search_chats function.
{ToolUse("chats", [], "search_chats('python')").to_output(tool_format)}
"""


tool = ToolSpec(
    name="chats",
    desc="List, search, and summarize past conversation logs",
    instructions_format={
        # Compact description for OpenAI tool format (full docstrings exceed 1024 chars)
        "tool": "Access past conversations: list recent chats with list_chats(), "
        "search chat history by content with search_chats(), "
        "or read a specific conversation by ID with read_chat().",
    },
    examples=examples,
    functions=[list_chats, search_chats, read_chat],
)

__doc__ = tool.get_doc(__doc__)
