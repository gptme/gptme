"""
RAG search: /rag <query>

Query the indexed document store mid-session and inject the top-N results into
the conversation as a user message, so the assistant can reference past
conversations or project docs without restarting gptme.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..message import Message

from .base import CommandContext, command


@command("rag")
def cmd_rag(ctx: CommandContext) -> Generator[Message, None, None]:
    """Search the RAG index and inject results into the conversation."""
    from ..message import Message
    from ..tools.rag import _has_gptme_rag, rag_search

    query = ctx.full_args.strip()
    if not query:
        print("Usage: /rag <query>")
        print("Search the RAG index and inject results into the conversation.")
        return

    if not _has_gptme_rag():
        print("gptme-rag is not installed. Install it with: pipx install gptme-rag")
        return

    try:
        results = rag_search(query, top_k=3)
    except RuntimeError as e:
        print(f"RAG search failed: {e}")
        return

    if not results or results.strip() == "No results found.":
        print(f"No relevant past conversations or documents found for: {query!r}")
        return

    snippet = f"Relevant context from past conversations and documents (query: {query!r}):\n\n{results}"
    yield Message("user", snippet)
