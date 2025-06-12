"""
List, search, and summarize past conversation logs.
"""

import logging
import re
import textwrap
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

        # highlighted = f"{prefix}{context_prefix}\033[1m{context[match_start:match_end]}\033[0m{context_suffix}{suffix}"
        highlighted = f"{prefix}{context}{suffix}"
        highlighted = re.sub(
            query,
            lambda m: f"\033[1;31m{m.group(0)!r}\033[0m",  # not sure why !r is needed to avoid linting error
            highlighted,
            flags=re.DOTALL,
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
