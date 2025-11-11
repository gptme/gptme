"""Enhanced file selection using context selector."""

import logging
from datetime import datetime
from pathlib import Path

from ..message import Message
from .base import ContextSelector
from .file_config import FileSelectorConfig
from .file_integration import FileItem
from .hybrid import HybridSelector

logger = logging.getLogger(__name__)


async def select_relevant_files(
    msgs: list[Message],
    workspace: Path | None,
    query: str | None = None,
    max_files: int = 10,
    use_selector: bool = False,
    config: FileSelectorConfig | None = None,
) -> list[Path]:
    """Select most relevant files from messages using context selector.

    Args:
        msgs: Conversation messages to analyze
        workspace: Workspace path for resolving relative paths
        query: Optional query for semantic selection (uses last message if None)
        max_files: Maximum number of files to return
        use_selector: Whether to use context selector (vs simple sorting)
        config: Configuration for file selection

    Returns:
        List of most relevant file paths, ordered by relevance
    """
    # Import here to avoid circular dependency
    from ..util.context import get_mentioned_files

    # Get files with mention counts (existing logic)
    files = get_mentioned_files(msgs, workspace)

    if not files or not use_selector:
        # Fallback: return top N by mention count + recency (existing behavior)
        return files[:max_files]

    # Convert to FileItems with metadata
    now = datetime.now().timestamp()
    file_items = []
    for f in files:
        try:
            mtime = f.stat().st_mtime if f.exists() else 0
            # Count mentions (already done in get_mentioned_files, but we need count)
            mention_count = sum(1 for msg in msgs if f in msg.files)
            file_items.append(FileItem(f, mention_count, mtime))
        except OSError:
            logger.debug(f"Skipping file {f}: stat failed")
            continue

    if not file_items:
        return []

    # Apply metadata boosts before selection
    config = config or FileSelectorConfig()
    scored_items = []
    for item in file_items:
        # Calculate boost factors
        mention_boost = config.get_mention_boost(item.mention_count)
        hours_since_modified = (
            (now - item.mtime) / 3600 if item.mtime > 0 else float("inf")
        )
        recency_boost = config.get_recency_boost(hours_since_modified)
        file_type = item.metadata["file_type"]
        type_weight = config.get_file_type_weight(file_type)

        # Composite score
        base_score = 1.0
        final_score = base_score * mention_boost * recency_boost * type_weight
        scored_items.append((item, final_score))

    # Sort by score (for rule-based, this is the final ranking)
    scored_items.sort(key=lambda x: x[1], reverse=True)

    if config.strategy == "rule":
        # Rule-based: use scored ranking directly
        return [item.path for item, _ in scored_items[:max_files]]

    # For LLM/hybrid: use context selector for semantic refinement
    selector: ContextSelector = HybridSelector(config)

    # Use last user message as query if not provided
    if query is None:
        user_msgs = [msg for msg in msgs if msg.role == "user"]
        query = user_msgs[-1].content if user_msgs else ""

    # Select using context selector (async)
    try:
        selected = await selector.select(
            query=query,
            candidates=[item for item, _ in scored_items],
            max_results=max_files,
        )
        # Assert type for mypy (we know these are FileItems)
        assert all(isinstance(item, FileItem) for item in selected)
        return [item.path for item in selected]  # type: ignore[attr-defined]
    except Exception as e:
        logger.error(f"Context selector failed: {e}, falling back to scored ranking")
        return [item.path for item, _ in scored_items[:max_files]]
