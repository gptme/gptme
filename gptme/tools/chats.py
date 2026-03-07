"""
List, search, and summarize past conversation logs.
"""

from __future__ import annotations

import logging
import re
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..logmanager import ConversationMeta
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
    max_results: int = 5,
    metadata=False,
    include_summary: bool = False,
    since: float | None = None,
) -> list[ConversationMeta]:
    """
    List recent chat conversations and optionally summarize them using an LLM.

    Args:
        max_results: Maximum number of conversations to display.
        include_summary: Whether to include a summary of each conversation.
        since: Only include conversations modified after this Unix timestamp.

    Returns:
        List of matching ConversationMeta objects.
    """
    from ..llm import summarize  # fmt: skip
    from ..logmanager import (  # fmt: skip
        LogManager,
        list_conversations,
    )

    # Fetch more when filtering by time, since many may be excluded
    fetch_limit = max_results * 10 if since else max_results
    conversations = list_conversations(fetch_limit)

    if since:
        conversations = [c for c in conversations if c.modified >= since]
        conversations = conversations[:max_results]

    if not conversations:
        print("No conversations found.")
        return []

    print(f"Recent conversations (showing {len(conversations)}):")
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

    return conversations


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
            lambda m: f"\033[1;31m{m.group(0)}\033[0m",
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


def examples(tool_format):
    return f"""
### Search for a specific topic in past conversations
User: Can you find any mentions of "python" in our past conversations?
Assistant: Certainly! I'll search our past conversations for mentions of "python" using the search_chats function.
{ToolUse("ipython", [], "search_chats('python')").to_output(tool_format)}
"""


tool = ToolSpec(
    name="chats",
    desc="List, search, and summarize past conversation logs",
    examples=examples,
    functions=[list_chats, search_chats, read_chat],
)

__doc__ = tool.get_doc(__doc__)
