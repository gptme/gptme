"""Enhanced file selection using context selector."""

import logging
import subprocess
from datetime import datetime
from pathlib import Path

from ...message import Message
from .base import ContextSelector
from .file_config import FileSelectorConfig
from .file_integration import FileItem
from .hybrid import HybridSelector

logger = logging.getLogger(__name__)


def get_workspace_files(workspace: Path) -> list[Path]:
    """Get all tracked files in the workspace."""
    files: list[Path] = []
    # Try git first
    try:
        p = subprocess.run(
            ["git", "ls-files"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )
        files = [workspace / f for f in p.stdout.splitlines()]
        # Filter existing files
        files = [f for f in files if f.exists()]
    except (OSError, subprocess.CalledProcessError):
        # Fallback to glob if not a git repo or git fails
        # We exclude hidden files/dirs
        files = [
            f
            for f in workspace.rglob("*")
            if f.is_file() and not any(p.startswith(".") for p in f.parts)
        ]
    return files


def get_git_status_files(workspace: Path) -> dict[Path, str]:
    """Get files with their git status.

    Returns:
        Dict mapping file paths to their status:
        - 'staged': Files staged for commit
        - 'modified': Files with unstaged changes
        - 'untracked': New files not yet tracked
    """
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )

        status_map = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                continue

            # Format: "XY filename" where X=staged, Y=unstaged
            status_code = line[:2]
            filepath = line[3:].strip()
            path = (workspace / filepath).resolve()

            # Determine status priority
            if status_code[0] in ("M", "A", "D", "R"):
                status_map[path] = "staged"
            elif status_code[1] == "M":
                status_map[path] = "modified"
            elif status_code == "??":
                status_map[path] = "untracked"

        return status_map
    except (OSError, subprocess.CalledProcessError):
        return {}


def select_relevant_files(
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
    from ...util.context import get_mentioned_files

    # Get files with mention counts (existing logic)
    mentioned_files = get_mentioned_files(msgs, workspace)

    # Load config if not provided
    if config is None:
        if workspace:
            from dataclasses import asdict

            from ...config import get_project_config

            if pc := get_project_config(workspace):
                # Create FileSelectorConfig from project's unified context config
                config = FileSelectorConfig(**asdict(pc.context.selector))

    config = config or FileSelectorConfig()

    if not use_selector or not config.enabled:
        # Fallback: return top N by mention count + recency (existing behavior)
        return mentioned_files[:max_files]

    # Gather all candidate files
    # 1. Mentioned files
    candidates = {f: 0 for f in mentioned_files}

    # 2. Workspace files (if available)
    if workspace:
        for f in get_workspace_files(workspace):
            if f not in candidates:
                candidates[f] = 0

    # Convert to FileItems with metadata
    now = datetime.now().timestamp()
    file_items = []

    # Pre-calculate counts for mentioned files only (optimization)
    # For non-mentioned workspace files, count is 0
    # Instead of re-iterating msgs for every file, we trust get_mentioned_files ordering/existence specific for mentioned ones
    # But we need actual counts for boosting?
    # Let's do a quick pass to count mentions if we really need them for scoring

    # Optimization: build mention map locally
    # TODO: get_mentioned_files should probably return counts
    mention_counts: dict[Path, int] = {}
    for msg in msgs:
        for f in msg.files:
            path = (workspace / f).resolve() if workspace else f.resolve()
            mention_counts[path] = mention_counts.get(path, 0) + 1

    for f in candidates.keys():
        try:
            mtime = f.stat().st_mtime if f.exists() else 0
            count = mention_counts.get(f, 0)
            file_items.append(FileItem(f, count, mtime))
        except OSError:
            logger.debug(f"Skipping file {f}: stat failed")
            continue

    if not file_items:
        return []

    # Get git status for additional boosting
    git_status_map = get_git_status_files(workspace) if workspace else {}

    # Apply metadata boosts before selection
    scored_items = []

    # Extract query terms for name matching
    query_terms = set(query.lower().split()) if query else set()
    num_query_terms = max(len(query_terms), 1)  # Avoid division by zero

    for item in file_items:
        # Calculate boost factors
        hours_since_modified = (
            (now - item.mtime) / 3600 if item.mtime > 0 else float("inf")
        )
        recency_boost = config.get_recency_boost(hours_since_modified)
        file_type = item.metadata["file_type"]
        type_weight = config.get_file_type_weight(file_type)

        # Git status boost - STRONG signal for active work
        git_boost = 1.0
        if item.path in git_status_map:
            status = git_status_map[item.path]
            if status == "staged":
                git_boost = 3.0  # Staged files: very relevant
            elif status == "modified":
                git_boost = 2.5  # Modified files: actively working on
            elif status == "untracked":
                git_boost = 1.8  # New files: potentially relevant

        # Name matching boost - normalized by query length
        name_boost = 1.0
        stem = item.path.stem.lower()
        matched_terms = 0.0  # Use float to allow fractional partial matches

        # Count exact matches
        if stem in query_terms:
            matched_terms += 1

        # Count partial matches (only for longer terms)
        for term in query_terms:
            if len(term) > 3 and term in stem:
                matched_terms += 0.5  # Partial match worth less

        if matched_terms > 0:
            # Normalize boost by total query terms to prevent single-term domination
            # Max boost of 3x, but divided by query length
            name_boost = 1.0 + (2.0 * matched_terms / num_query_terms)

        # Additive base score (so zero-mention files aren't crushed)
        base_score = 1.0 + (item.mention_count * 0.3)

        # Multiplicative boosts
        final_score = base_score * recency_boost * type_weight * name_boost * git_boost
        scored_items.append((item, final_score))

    # Sort by score (for rule-based, this is the final ranking)
    scored_items.sort(key=lambda x: x[1], reverse=True)

    if config.strategy == "rule":
        # Rule-based: use scored ranking directly
        return [item.path for item, _ in scored_items[:max_files]]

    # For LLM/hybrid: use context selector for semantic refinement
    selector: ContextSelector = HybridSelector(config)

    # Build query from recent conversation context
    # This provides continuity across turns by considering multiple recent messages
    if query is None:
        # Extract terms from recent messages with decay weighting
        term_weights: dict[str, float] = {}
        recent_msgs = list(reversed(msgs))[:5]  # Last 5 messages (user + assistant)

        for i, msg in enumerate(recent_msgs):
            if msg.role not in ("user", "assistant") or not msg.content.strip():
                continue

            content = msg.content.lower()

            # Skip very short messages
            if len(content) < 10:
                continue

            # Extract meaningful terms (filter stopwords, short words)
            # Weight decays: most recent = 1.0, then 0.5, 0.33, 0.25, 0.2
            weight = 1.0 / (i + 1)

            # Simple tokenization
            words = content.split()
            for word in words:
                # Filter stopwords and short words
                if len(word) > 3 and word not in {
                    "this",
                    "that",
                    "with",
                    "from",
                    "have",
                    "were",
                    "been",
                    "what",
                    "when",
                    "where",
                    "which",
                    "their",
                    "there",
                    "does",
                    "about",
                    "would",
                    "could",
                    "should",
                    "these",
                    "those",
                }:
                    # Accumulate weights for repeated terms across messages
                    term_weights[word] = term_weights.get(word, 0) + weight

        # Build query from top weighted terms (preserves multi-message context)
        sorted_terms = sorted(term_weights.items(), key=lambda x: x[1], reverse=True)
        top_terms = [term for term, _ in sorted_terms[:20]]  # Top 20 terms
        query = " ".join(top_terms) if top_terms else ""

    # Select using context selector (sync)
    try:
        selected = selector.select(
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
