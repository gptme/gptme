"""
/rag command — query the RAG store mid-session and inject results as context.
"""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING

from .base import CommandContext, command

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..message import Message

logger = logging.getLogger(__name__)

# Imported at module level so tests can patch gptme.commands.rag.rag_search
from ..tools.rag import rag_search


@command("rag", auto_undo=False)
def cmd_rag(ctx: CommandContext) -> Generator[Message, None, None]:
    """Search the RAG index and inject the top results into the conversation.

    Usage: /rag <query>

    Requires gptme-rag to be installed and a populated index.
    """
    from ..message import Message  # fmt: skip

    query = ctx.full_args.strip()
    if not query:
        print("Usage: /rag <query>")
        return

    if not shutil.which("gptme-rag"):
        print("gptme-rag is not installed. Install with: pipx install gptme-rag")
        return

    try:
        results = rag_search(query, top_k=3)
    except Exception as e:
        print(f"RAG search failed: {e}")
        return

    if not results:
        print("No relevant results found in the RAG index.")
        return

    content = f"Relevant context from RAG search for '{query}':\n\n{results}"
    yield Message("system", content, hide=False)
