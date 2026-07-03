"""
List, search, and summarize past conversation logs.
"""

import json as json_mod
import logging
import statistics
import sys
import textwrap
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from ..logmanager.conversations import _format_duration
from ..message import Message
from .base import ToolFunction, ToolSpec, ToolUse

logger = logging.getLogger(__name__)


def _get_matching_messages(
    log_manager, query: str, system=False
) -> list[tuple[int, Message]]:
    """Get messages matching the query."""
    if not query.strip():
        return []
    matching = []
    for i, msg in enumerate(log_manager.log):
        if msg.role == "system" and not system:
            continue
        if not isinstance(msg.content, str):
            logger.warning(
                f"Skipping message with non-string content: {type(msg.content).__name__}"
            )
            continue
        if query.lower() in msg.content.lower():
            matching.append((i, msg))
    return matching


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
            first, *rest = conv.format(metadata=True).split("\n")
            print(f"{i:2}. {first}")
            for line in rest:
                print(f"     {line}")
        else:
            print(f"{i:2}. {conv.name} ({conv.messages} msgs)")

        # Use the LLM to generate a summary if requested
        if include_summary:
            log_manager = LogManager.load(Path(conv.path), lock=False)
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
    context_lines: int = 1,
    max_matches: int = 1,
) -> None:
    """
    Search past conversation logs for the given query and print a summary of the results.

    Args:
        query (str): The search query.
        max_results (int): Maximum number of conversations to display.
        system (bool): Whether to include system messages in the search.
        context_lines (int): Number of lines to show around each match.
        max_matches (int): Maximum number of matches to show per conversation.
    """
    if not query.strip():
        print("Error: search query cannot be empty.", file=sys.stderr)
        return

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
        matches = result["matching_messages"][:max_matches]
        print(
            f"\n{i}. {conversation.name} ({len(result['matching_messages'])} matches):"
        )
        for j, (msg_idx, msg) in enumerate(matches, 1):
            print(f"\n  Match {j} (message {msg_idx}, {msg.role}):")
            match_str = _format_message_with_context(
                msg.content, query, context_lines=context_lines, max_matches=max_matches
            )
            print(f"     {match_str}")


# ---------------------------------------------------------------------------
# External agent session search (Cursor, Codex)
# ---------------------------------------------------------------------------


def _discover_cursor_sessions(cursor_dir: Path | None = None) -> list[Path]:
    """Return paths to Cursor conversation files under *cursor_dir*.

    Looks for:

    - ``<cursor_dir>/<uuid>/conversation.json`` (standard per-session format)
    - ``<cursor_dir>/../cursor-chat-history.json`` (alternate workspace-storage
      format with ``allConversations`` array)

    Pass *cursor_dir* explicitly in tests.
    """
    if cursor_dir is None:
        cursor_dir = Path.home() / ".cursor" / "conversations"
    if not cursor_dir.exists():
        return []
    results: list[Path] = sorted(cursor_dir.glob("*/conversation.json"))
    # cursor-chat-history.json lives at the .cursor/ root, not under conversations/
    alt = cursor_dir.parent / "cursor-chat-history.json"
    if alt.exists():
        results.append(alt)
    return results


def _search_cursor_session(path: Path, query: str) -> list[dict]:
    """Search a single Cursor ``conversation.json`` for *query*.

    Handles both the standard format (``messages`` list) and the alternate
    workspace-storage format (``allConversations`` / ``bubbles``).
    Returns a list of hit dicts with keys ``role``, ``content``,
    ``session_title``.
    """
    try:
        data = json_mod.loads(path.read_text(encoding="utf-8"))
    except (json_mod.JSONDecodeError, OSError):
        return []

    query_lower = query.lower()
    results: list[dict] = []

    # Standard format: {"title": "...", "messages": [{"role": ..., "content": ...}]}
    for msg in data.get("messages", []):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        if query_lower in content.lower():
            results.append(
                {
                    "role": role,
                    "content": content[:500],
                    "session_title": data.get("title", path.parent.name),
                }
            )

    # Alternate format (cursor-chat-history.json):
    # {"allConversations": [{"composerId": "...", "bubbles": [...]}]}
    if not results:
        for conv in data.get("allConversations", []):
            for bubble in conv.get("bubbles", []):
                btype = bubble.get("type", "")
                if btype == "user":
                    content = bubble.get("text", "")
                    role = "user"
                elif btype == "ai":
                    raw = bubble.get("rawResponse", {})
                    content = raw.get("text", "") if isinstance(raw, dict) else ""
                    role = "assistant"
                else:
                    continue
                if not isinstance(content, str):
                    continue
                if query_lower in content.lower():
                    results.append(
                        {
                            "role": role,
                            "content": content[:500],
                            "session_title": conv.get("composerId", path.parent.name),
                        }
                    )

    return results


def _discover_codex_sessions(codex_dir: Path | None = None) -> list[Path]:
    """Return paths to Codex CLI session files under *codex_dir*.

    Looks for ``<codex_dir>/<session-id>.jsonl``.  Pass *codex_dir*
    explicitly in tests.
    """
    if codex_dir is None:
        codex_dir = Path.home() / ".codex" / "sessions"
    if not codex_dir.exists():
        return []
    return sorted(codex_dir.glob("*.jsonl"))


def _search_codex_session(path: Path, query: str) -> list[dict]:
    """Search a Codex CLI ``.jsonl`` session file for *query*.

    Each line is a JSON record.  Only ``{"type": "message", ...}`` records
    are searched; tool-call records are skipped.  Returns a list of hit
    dicts with keys ``role``, ``content``, ``session_title``.
    """
    query_lower = query.lower()
    results: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json_mod.loads(line)
            except json_mod.JSONDecodeError:
                continue
            if record.get("type") != "message":
                continue
            role = record.get("role", "unknown")
            content = record.get("content", "")
            if not isinstance(content, str):
                continue
            if query_lower in content.lower():
                results.append(
                    {
                        "role": role,
                        "content": content[:500],
                        "session_title": path.stem,
                    }
                )
    except OSError:
        pass
    return results


def search_external_chats(
    query: str,
    max_results: int = 5,
    include_cursor: bool = True,
    include_codex: bool = True,
    cursor_dir: Path | None = None,
    codex_dir: Path | None = None,
) -> None:
    """Search external agent sessions (Cursor, Codex) for *query*.

    Prints matching snippets grouped by source agent, with a ``[Cursor]``
    or ``[Codex]`` label so results are clearly distinguished from gptme
    native results.

    Args:
        query: The search query (case-insensitive substring match).
        max_results: Maximum number of sessions to display.
        include_cursor: Whether to search Cursor sessions.
        include_codex: Whether to search Codex sessions.
        cursor_dir: Override the default Cursor conversations directory.
        codex_dir: Override the default Codex sessions directory.
    """
    if not query.strip():
        print("Error: search query cannot be empty.", file=sys.stderr)
        return

    all_results: list[dict] = []

    if include_cursor:
        for session_path in _discover_cursor_sessions(cursor_dir):
            if len(all_results) >= max_results:
                break
            matches = _search_cursor_session(session_path, query)
            if matches:
                all_results.append(
                    {"agent": "Cursor", "path": session_path, "matches": matches}
                )

    if include_codex:
        for session_path in _discover_codex_sessions(codex_dir):
            if len(all_results) >= max_results:
                break
            matches = _search_codex_session(session_path, query)
            if matches:
                all_results.append(
                    {"agent": "Codex", "path": session_path, "matches": matches}
                )

    if not all_results:
        return

    total_matches = sum(len(r["matches"]) for r in all_results)
    shown = all_results[:max_results]
    print(
        f"\nExternal agent results for '{query}' "
        f"({len(all_results)} sessions, {total_matches} matches):"
    )
    for i, result in enumerate(shown, 1):
        agent = result["agent"]
        matches = result["matches"]
        title = matches[0]["session_title"] if matches else result["path"].stem
        print(f"\n{i}. [{agent}] {title} ({len(matches)} match(es)):")
        for match in matches[:1]:
            snippet = match["content"][:200].replace("\n", " ")
            print(f"   ({match['role']}): {snippet}…")


def _format_message_with_context(
    content: str | object, query: str, context_lines: int = 1, max_matches: int = 1
) -> str:
    """Format a message with context around matching query parts.

    Args:
        content: The message content to search in
        query: The search query
        context_lines: Number of lines to show before and after match
        max_matches: Maximum number of matches to show

    Returns:
        Formatted string with highlighted matches and context
    """
    if not isinstance(content, str):
        return "[non-string content]"
    query_lower = query.lower()

    # Split content into lines for line-based context
    lines = content.split("\n")
    lines_lower = [line.lower() for line in lines]

    # Find all lines containing the query
    line_indices = []
    for line_idx, line_lower in enumerate(lines_lower):
        start = 0
        while True:
            idx = line_lower.find(query_lower, start)
            if idx == -1:
                break
            line_indices.append((line_idx, idx, idx + len(query_lower)))
            start = idx + len(query_lower)

    if not line_indices:
        return content[:100] + "..." if len(content) > 100 else content

    # Gather the matches to render (up to max_matches)
    visible = line_indices[:max_matches]

    # Merge overlapping context windows so nearby matches share a single block.
    # Each window is [context_start, context_end) in line space.
    windows: list[tuple[int, int, list[tuple[int, int, int]]]] = []
    for match_idx, start_pos, end_pos in visible:
        cs = max(0, match_idx - context_lines)
        ce = min(len(lines), match_idx + context_lines + 1)
        if windows and cs <= windows[-1][1]:
            # Overlaps with the previous window — extend it
            prev_cs, _, prev_matches = windows[-1]
            windows[-1] = (
                prev_cs,
                max(ce, windows[-1][1]),
                prev_matches + [(match_idx, start_pos, end_pos)],
            )
        else:
            windows.append((cs, ce, [(match_idx, start_pos, end_pos)]))

    # Build a highlight map: line_idx -> list of (start, end) char positions
    def _highlight_line(line: str, highlights: list[tuple[int, int]]) -> str:
        if not highlights:
            return line
        result_parts = []
        prev = 0
        for hs, he in sorted(highlights):
            result_parts.append(line[prev:hs])
            match_text = line[hs:he]
            if sys.stdout.isatty():
                result_parts.append(f"\033[1;31m{match_text}\033[0m")
            else:
                result_parts.append(f"**{match_text}**")
            prev = he
        result_parts.append(line[prev:])
        return "".join(result_parts)

    formatted_matches = []
    for cs, ce, block_matches in windows:
        highlight_map: dict[int, list[tuple[int, int]]] = {}
        for match_idx, start_pos, end_pos in block_matches:
            highlight_map.setdefault(match_idx, []).append((start_pos, end_pos))

        formatted_lines = []
        for i in range(cs, ce):
            line = lines[i]
            formatted_line = _highlight_line(line, highlight_map.get(i, []))
            formatted_lines.append(f"{i + 1:4d}| {formatted_line}")

        context_text = "\n     ".join(formatted_lines)
        if cs > 0:
            context_text = "...\n     " + context_text
        if ce < len(lines):
            context_text = context_text + "\n     ..."
        formatted_matches.append(context_text)

    result = "\n".join(formatted_matches)
    if len(line_indices) > max_matches:
        result += f"\n(+{len(line_indices) - max_matches} more matches)"

    return result


def read_chat(
    id: str,
    max_results: int = 5,
    incl_system: bool = False,
    context_messages: int = 0,
    start_message: int | None = None,
) -> None:
    """
    Read a specific conversation log.

    Args:
        id (str): The id of the conversation to read.
        max_results (int): Maximum number of messages to display.
        incl_system (bool): Whether to include system messages.
        context_messages (int): Number of messages to show before start_message.
        start_message (int | None): Start from this message number (1-indexed), if specified.
    """
    from ..logmanager import LogManager, get_conversation_by_id  # fmt: skip

    # Look up by id across ALL conversations. Using list_conversations() here
    # would only scan the 20 most recent, so any older conversation was reported
    # as "not found" even though it exists.
    conv = get_conversation_by_id(id)
    if conv is None:
        print(f"Conversation '{id}' not found.")
        return

    log_path = Path(conv.path)
    logmanager = LogManager.load(log_path, lock=False)
    print(f"Reading conversation: {conv.name} ({conv.id})")
    messages = [msg for msg in logmanager.log if msg.role != "system" or incl_system]
    start_idx = 0
    if start_message is not None:
        start_idx = max(0, start_message - 1 - context_messages)
        messages = messages[start_idx:]
    for i, msg in enumerate(messages[:max_results]):
        print(f"{start_idx + i + 1}. {msg.format(max_length=100)}")


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


def _normalize_timestamp(ts: datetime) -> datetime:
    """Normalize datetimes to timezone-aware UTC."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _conversation_detail(conversation_id: str) -> dict:
    """Collect detailed stats for a single conversation."""
    from ..logmanager import LogManager, get_conversation_by_id  # fmt: skip

    conv = get_conversation_by_id(conversation_id)
    if conv is None:
        raise ValueError(f"Conversation '{conversation_id}' not found.")

    log_manager = LogManager.load(Path(conv.path), lock=False)
    messages = list(log_manager.log)

    role_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    timestamps: list[datetime] = []

    for msg in messages:
        role_counts[msg.role] += 1
        timestamps.append(_normalize_timestamp(msg.timestamp))
        if msg.role != "assistant":
            continue
        tool_uses = list(
            ToolUse.iter_from_content(msg.content, tool_format_override="tool")
        )
        if not tool_uses and "<tool-use>" in msg.content:
            tool_uses = list(
                ToolUse.iter_from_content(msg.content, tool_format_override="xml")
            )
        if not tool_uses and "```" in msg.content:
            tool_uses = list(
                ToolUse.iter_from_content(msg.content, tool_format_override="markdown")
            )
        for tool_use in tool_uses:
            tool_counts[tool_use.tool] += 1

    started = (
        timestamps[0]
        if timestamps
        else datetime.fromtimestamp(conv.created, tz=timezone.utc)
    )
    ended = (
        timestamps[-1]
        if timestamps
        else datetime.fromtimestamp(conv.modified, tz=timezone.utc)
    )
    duration_seconds = max(0.0, (ended - started).total_seconds())
    total_tokens = conv.total_input_tokens + conv.total_output_tokens

    data = {
        "id": conv.id,
        "name": conv.name,
        "workspace": conv.workspace,
        "agent_name": conv.agent_name,
        "model": conv.model,
        "started": started.isoformat(),
        "ended": ended.isoformat(),
        "duration_seconds": int(duration_seconds),
        "messages": {
            "total": len(messages),
            "by_role": dict(sorted(role_counts.items())),
        },
        "tool_calls": {
            "total": sum(tool_counts.values()),
            "by_tool": dict(tool_counts.most_common()),
        },
        "usage": {
            "input_tokens": conv.total_input_tokens,
            "output_tokens": conv.total_output_tokens,
            "cache_read_tokens": conv.total_cache_read_tokens,
            "total_tokens": total_tokens,
            "cost": round(conv.total_cost, 4),
        },
        "last_message": (
            {
                "role": conv.last_message_role,
                "preview": conv.last_message_preview,
            }
            if conv.last_message_preview
            else None
        ),
    }
    return data


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


def conversation_stats(
    since: str | None = None,
    as_json: bool = False,
    conversation_id: str | None = None,
) -> None:
    """Show statistics about conversation history.

    Args:
        since: Only include conversations since this date (YYYY-MM-DD or Nd).
        as_json: Output as JSON instead of formatted text.
        conversation_id: Optional conversation ID to inspect in detail.
    """
    if conversation_id is not None:
        conv_data = _conversation_detail(conversation_id)

        if as_json:
            print(json_mod.dumps(conv_data, indent=2))
            return

        print(f"Conversation Stats: {conv_data['name']}")
        print("=" * 40)
        print(f"  ID:                   {conv_data['id']}")
        if conv_data["agent_name"]:
            print(f"  Agent:                {conv_data['agent_name']}")
        if conv_data["model"]:
            print(f"  Model:                {conv_data['model']}")
        print(f"  Messages:             {conv_data['messages']['total']}")
        role_breakdown = ", ".join(
            f"{role}={count}"
            for role, count in conv_data["messages"]["by_role"].items()
        )
        print(f"  By role:              {role_breakdown}")
        print(f"  Tool calls:           {conv_data['tool_calls']['total']}")
        if conv_data["tool_calls"]["by_tool"]:
            tool_breakdown = ", ".join(
                f"{tool}={count}"
                for tool, count in conv_data["tool_calls"]["by_tool"].items()
            )
            print(f"  By tool:              {tool_breakdown}")
        print(f"  Started:              {conv_data['started']}")
        print(f"  Ended:                {conv_data['ended']}")
        print(
            f"  Duration:             {_format_duration(conv_data['duration_seconds'])}"
        )
        print(f"  Workspace:            {conv_data['workspace']}")

        usage = conv_data["usage"]
        if usage["total_tokens"] or usage["cost"]:
            cache_pct = (
                usage["cache_read_tokens"] / usage["input_tokens"] * 100
                if usage["input_tokens"]
                else 0
            )
            print("\nToken Usage & Cost")
            print(
                f"  Input tokens:         {_format_tokens(usage['input_tokens']):>8s}  ({cache_pct:.0f}% cached)"
            )
            print(
                f"  Output tokens:        {_format_tokens(usage['output_tokens']):>8s}"
            )
            print(
                f"  Total tokens:         {_format_tokens(usage['total_tokens']):>8s}"
            )
            print(f"  Total cost:           ${usage['cost']:>8.4f}")

        if conv_data["last_message"]:
            last = conv_data["last_message"]
            print("\nLast Message")
            print(f"  {last['role']}: {last['preview']}")
        return

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


instructions = """
### When to use chats

Use chats when the user asks about or wants to reference a past conversation:
- "remember when we discussed X?" → search_chats('X')
- "find our earlier chat about Y" → search_chats('Y')
- "what did we say about Z last week?" → search_chats('Z')
- Listing recent sessions to give the user an overview → list_chats()
- Reading a specific prior conversation by ID → read_chat(id)

Do **not** use chats for:
- The current conversation — its content is already in the context window.
- Searching files or code — use the shell or read tool instead.
- Web or documentation search — use the browser tool.
""".strip()

tool = ToolSpec(
    name="chats",
    desc="List, search, and summarize past conversation logs",
    instructions=instructions,
    instructions_format={
        # Compact description for OpenAI tool format (full docstrings exceed 1024 chars)
        "tool": "Access past conversations: list recent chats with list_chats(), "
        "search chat history by content with search_chats(), "
        "or read a specific conversation by ID with read_chat().",
    },
    examples=examples,
    functions=[
        ToolFunction.from_callable(f) for f in [list_chats, search_chats, read_chat]
    ],
)

__doc__ = tool.get_doc(__doc__)
