"""
RAG (Retrieval-Augmented Generation) tool for context-aware assistance.

The RAG tool provides context-aware assistance by indexing and semantically searching text files.

.. rubric:: Installation

The RAG tool requires the ``gptme-rag`` CLI to be installed::

    pipx install gptme-rag

.. rubric:: Configuration

Configure RAG in your ``gptme.toml``::

    [rag]
    enabled = true
    post_process = false # Whether to post-process the context with an LLM to extract the most relevant information
    post_process_model = "openai/gpt-4o-mini" # Which model to use for post-processing
    post_process_prompt = "" # Optional prompt to use for post-processing (overrides default prompt)
    workspace_only = true # Whether to only search in the workspace directory, or the whole RAG index
    paths = [] # List of paths to include in the RAG index. Has no effect if workspace_only is true.

.. rubric:: Features

1. Manual Search and Indexing

   - Index project documentation with ``rag_index``
   - Search indexed documents with ``rag_search``
   - Check index status with ``rag_status``

2. Automatic Context Enhancement

   - Retrieves semantically similar documents
   - Preserves conversation flow with hidden context messages
"""

import logging
import os
import shutil
import subprocess
import time
from dataclasses import replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from ..config import RagConfig, get_project_config
from ..dirs import get_project_gptme_dir
from ..llm import _chat_complete
from ..message import Message
from .base import ToolSpec, ToolUse
from .cache import BackgroundRefresher, CacheEntry, CacheKey, SmartRAGCache

logger = logging.getLogger(__name__)

# Global cache instance for RAG search results
_cache: SmartRAGCache | None = None

# Global background refresher instance
_refresher: BackgroundRefresher | None = None


def _refresh_callback(key: CacheKey) -> tuple[list[str], list[float]]:
    """Refresh callback for background refresher."""
    try:
        # Reconstruct RAG command from cache key
        cmd = [
            "gptme-rag",
            "search",
            key.query_text,
        ]

        if key.workspace_path and key.workspace_only:
            cmd.append(key.workspace_path)

        cmd.extend(["--format", "full"])

        if key.max_tokens:
            cmd.extend(["--max-tokens", str(key.max_tokens)])
        if key.min_relevance:
            cmd.extend(["--min-relevance", str(key.min_relevance)])

        # Run command and get result
        result = _run_rag_cmd(cmd).stdout

        # Return as single document (raw stdout)
        return ([result], [1.0])

    except Exception as e:
        logger.error(f"Failed to refresh query '{key.query_text[:50]}': {e}")
        raise


def _get_cache() -> SmartRAGCache:
    """Get or create the global cache instance."""
    global _cache
    if _cache is None:
        _cache = SmartRAGCache(
            ttl_seconds=300,  # 5 minutes
            max_memory_bytes=100 * 1024 * 1024,  # 100MB
        )
    return _cache


def _get_refresher() -> BackgroundRefresher:
    """Get or create the global background refresher instance."""
    global _refresher
    if _refresher is None:
        cache = _get_cache()
        _refresher = BackgroundRefresher(
            cache=cache,
            refresh_callback=_refresh_callback,
            refresh_interval_seconds=60,  # Check every minute
            hot_threshold=5,  # Queries with 5+ accesses are hot
        )
        _refresher.start()
        logger.info("Background refresher started")
    return _refresher


instructions = """
Use RAG to index and semantically search through text files such as documentation and code.
"""


def examples(tool_format):
    return f"""
User: Index the current directory
Assistant: Let me index the current directory with RAG.
{ToolUse("ipython", [], "rag_index()").to_output(tool_format)}
System: Indexed 1 paths

User: Search for documentation about functions
Assistant: I'll search for function-related documentation.
{ToolUse("ipython", [], 'rag_search("function documentation")').to_output(tool_format)}
System: ### docs/api.md
Functions are documented using docstrings...

User: Show index status
Assistant: I'll check the current status of the RAG index.
{ToolUse("ipython", [], "rag_status()").to_output(tool_format)}
System: Index contains 42 documents
"""


DEFAULT_POST_PROCESS_PROMPT = """
You are an intelligent knowledge retrieval assistant designed to analyze context chunks and extract relevant information based on user queries. Your primary goal is to provide accurate and helpful information while adhering to specific guidelines.

You will be provided with a user query inside <user_query> tags and a list of potentially relevant context chunks inside <chunks> tags.

When a user submits a query, follow these steps:

1. Analyze the user's query carefully to identify key concepts and requirements.

2. Search through the provided context chunks for relevant information.

3. If you find relevant information:
   a. Extract the most pertinent parts.
   b. Summarize the relevant context inside <context_summary> tags.
   c. Output the exact relevant context chunks, including the complete <chunks path="...">...</chunks> tags.

4. If you cannot find any relevant information, respond with exactly: "No relevant context found".

Important guidelines:
- Do not make assumptions beyond the available data.
- Maintain objectivity in source selection.
- When returning context chunks, include the entire content of the <chunks> tag. Do not modify or truncate it in any way.
- Ensure that you're providing complete information from the chunks, not partial or summarized versions within the tags.
- When no relevant context is found, do not return anything other than exactly "No relevant context found".
- Do not output anything else than the <context_summary> and <chunks> tags.

Please provide your response, starting with the summary and followed by the relevant chunks (if any).
"""


@lru_cache
def _has_gptme_rag() -> bool:
    """Check if gptme-rag is available in PATH."""
    return shutil.which("gptme-rag") is not None


def _get_workspace_mtime(workspace_path: str) -> float:
    """Get workspace modification time for freshness tracking."""
    path = Path(workspace_path)
    if not path.exists():
        return 0.0
    # Sample approach: check workspace root mtime
    # More sophisticated: walk directory tree
    return os.path.getmtime(path)


def _get_index_mtime(workspace_path: str) -> float:
    """Get RAG index modification time."""
    # Check ChromaDB index directory
    index_path = Path(workspace_path) / ".cache" / "chroma"
    if not index_path.exists():
        return 0.0
    return os.path.getmtime(index_path)


def _run_rag_cmd(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a gptme-rag command and handle errors."""
    start = time.monotonic()
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"gptme-rag command failed: {e.stderr}")
        raise RuntimeError(f"gptme-rag command failed: {e.stderr}") from e
    finally:
        cmd_str = " ".join(cmd)
        logger.info(
            f"Ran RAG: `{cmd_str[:100] if len(cmd_str) > 100 else cmd_str}` in {time.monotonic() - start:.2f}s"
        )


def rag_index(*paths: str, glob: str | None = None) -> str:
    """Index documents in specified paths."""
    paths = paths or (".",)
    cmd = ["gptme-rag", "index"]
    cmd.extend(paths)
    if glob:
        cmd.extend(["--glob", glob])

    result = _run_rag_cmd(cmd)
    return result.stdout.strip()


def rag_search(query: str, return_full: bool = False) -> str:
    """Search indexed documents."""
    cmd = ["gptme-rag", "search", query]
    if return_full:
        # shows full context of the search results
        cmd.extend(["--raw"])

    result = _run_rag_cmd(cmd)
    return result.stdout.strip()


def rag_status() -> str:
    """Show index status."""
    cmd = ["gptme-rag", "status"]
    result = _run_rag_cmd(cmd)
    return result.stdout.strip()


def init() -> ToolSpec:
    """Initialize the RAG tool."""
    # Check if gptme-rag CLI is available
    if not _has_gptme_rag():
        logger.debug("gptme-rag CLI not found in PATH")
        return replace(tool, available=False)

    # Check project configuration
    project_dir = get_project_gptme_dir()
    if project_dir and (config := get_project_config(project_dir)):
        enabled = config.rag.enabled
        if not enabled:
            logger.debug("RAG not enabled in the project configuration")
            return replace(tool, available=False)
    else:
        logger.debug("Project configuration not found, not enabling")
        return replace(tool, available=False)

    return tool


def get_rag_context(
    query: str,
    rag_config: RagConfig,
    workspace: Path | None = None,
) -> Message:
    """Get relevant context chunks from RAG for the user query."""

    should_post_process = (
        rag_config.post_process and rag_config.post_process_model is not None
    )

    cmd = [
        "gptme-rag",
        "search",
        query,
    ]
    if workspace and rag_config.workspace_only:
        cmd.append(workspace.as_posix())
    elif rag_config.paths:
        cmd.extend(rag_config.paths)
    if not should_post_process:
        cmd.append("--score")
    cmd.extend(["--format", "full"])

    if rag_config.max_tokens:
        cmd.extend(["--max-tokens", str(rag_config.max_tokens)])
    if rag_config.min_relevance:
        cmd.extend(["--min-relevance", str(rag_config.min_relevance)])

    # Try cache first
    cache = _get_cache()
    # Ensure background refresher is running
    _get_refresher()
    workspace_path = workspace.as_posix() if workspace else "."
    cache_key = CacheKey.from_search(
        query=query,
        workspace_path=workspace_path,
        workspace_only=rag_config.workspace_only,
        max_tokens=rag_config.max_tokens or 3000,
        min_relevance=rag_config.min_relevance or 0.0,
        embedding_model="modernbert",  # Current default
        index_version="v1",
    )

    cache_entry = cache.get(cache_key)
    if cache_entry is not None:
        logger.info(f"RAG cache hit for query: {query[:50]}...")
        # Reconstruct stdout from cached data
        rag_result = "\n".join(cache_entry.document_ids)
    else:
        logger.info(f"RAG cache miss for query: {query[:50]}...")
        start_time = time.monotonic()
        rag_result = _run_rag_cmd(cmd).stdout
        search_time_ms = (time.monotonic() - start_time) * 1000

        # Store in cache
        entry = CacheEntry(
            document_ids=[rag_result],  # Store raw stdout as single document
            relevance_scores=[1.0],  # Placeholder score
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=1,
            workspace_mtime=_get_workspace_mtime(workspace_path),
            index_mtime=_get_index_mtime(workspace_path),
            embedding_time_ms=search_time_ms,
            result_count=1,
        )
        cache.put(cache_key, entry)

    # Post-process the context with an LLM (if enabled)
    if should_post_process:
        post_process_msgs = [
            Message(
                role="system",
                content=rag_config.post_process_prompt or DEFAULT_POST_PROCESS_PROMPT,
            ),
            Message(role="system", content=rag_result),
            Message(
                role="user",
                content=f"<user_query>\n{query}\n</user_query>",
            ),
        ]
        start = time.monotonic()
        rag_result = _chat_complete(
            messages=post_process_msgs,
            model=rag_config.post_process_model,  # type: ignore
            tools=[],
        )
        logger.info(f"Ran RAG post-process in {time.monotonic() - start:.2f}s")

    # Create the context message
    msg = Message(
        role="system",
        content=f"Relevant context retrieved using `gptme-rag search`:\n\n{rag_result}",
        hide=True,
    )
    return msg


def rag_enhance_messages(
    messages: list[Message], workspace: Path | None = None
) -> list[Message]:
    """Enhance messages with context from RAG."""
    if not _has_gptme_rag():
        return messages

    # Load config
    config = get_project_config(Path.cwd())
    rag_config = config.rag if config and config.rag else RagConfig()

    if not rag_config.enabled:
        return messages

    last_msg = messages[-1] if messages else None
    if last_msg and last_msg.role == "user":
        try:
            # Get context using gptme-rag CLI
            msg = get_rag_context(last_msg.content, rag_config, workspace)

            # Append context message right before the last user message
            messages.insert(-1, msg)
        except Exception as e:
            logger.warning(f"Error getting context: {e}")

    return messages


tool = ToolSpec(
    name="rag",
    desc="RAG (Retrieval-Augmented Generation) for context-aware assistance",
    instructions=instructions,
    examples=examples,
    functions=[rag_index, rag_search, rag_status],
    available=_has_gptme_rag,
    init=init,
)

__doc__ = tool.get_doc(__doc__)
