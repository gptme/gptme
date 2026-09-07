"""Trajectory-to-environment pipeline for fine-tuning dataset construction.

Converts gptme session trajectories into ``TaskEnvironment`` records that can
be used as training environments for fine-tuning LLMs on gptme-specific tasks.

The core insight (from the issue) is that gptme sessions embed session IDs in
commit messages::

    chore(journal): session c876 — refactor done
    fix(tool): handle edge case (a1b2)

This lets us recover the exact git state before/after each session without
replaying the trajectory — cheaper than general file-op replay.

Usage::

    from gptme.dataset import extract_environments

    envs = list(extract_environments(repo_path="/path/to/repo", limit=500))
    for env in envs:
        print(env.to_jsonl())

See ``gptme-util dataset`` for the CLI interface.

References:
    - Terminal-Universe paper: https://arxiv.org/abs/2609.04148
    - Issue: gptme/gptme#3718
"""

import json
import logging
import re
import subprocess
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..dirs import get_logs_dir
from ..logmanager.conversations import _is_test_conversation_id

logger = logging.getLogger(__name__)

# Legacy commit subjects identify a session as either ``(<id>)`` or
# ``session <id>``. Newer commits may use an exact ``Git-Session-Id`` trailer.
_SESSION_MARKER_TEMPLATE = r"(?:\({id}\)|\bsession[ :]+{id}(?=$|\s|[—–-]))"


@dataclass
class TaskEnvironment:
    """A single training environment derived from a gptme session.

    Each instance encodes a reproducible task: given ``entry_commit`` as the
    workspace state and ``task_description`` as the user prompt, a model
    should produce the changes reflected in ``solution_commits``.
    """

    session_id: str
    """Short hex ID embedded in the session's commit messages."""

    entry_commit: str
    """SHA of the commit immediately before the first session commit.

    ``git checkout entry_commit`` restores the workspace to the state when
    the session started.
    """

    solution_commits: list[str]
    """Ordered list of commit SHAs produced during the session.

    These are the ground-truth changes the model should replicate.
    """

    files_changed: list[str]
    """All files touched across solution_commits (de-duplicated, sorted)."""

    task_description: str
    """First user message in the session, used as the training prompt."""

    category: str
    """Broad task category: ``code``, ``docs``, ``journal``, ``other``."""

    outcome: str
    """Session outcome: ``productive`` (≥1 commit) or ``no_commits``."""

    tool_call_counts: dict[str, int] = field(default_factory=dict)
    """Count of each tool type used during the session."""

    model: str = ""
    """Model identifier from the session metadata (empty if unknown)."""

    duration_seconds: float = 0.0
    """Wall-clock session duration in seconds (0 if unrecoverable)."""

    def to_jsonl(self) -> str:
        """Serialise to a single JSONL line."""
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "TaskEnvironment":
        return cls(**d)


def _run_git(args: list[str], cwd: Path) -> str:
    """Run a git command and return stdout; returns '' on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            logger.debug("git %s failed: %s", " ".join(args), result.stderr.strip())
            return ""
        return result.stdout.strip()
    except Exception as exc:
        logger.debug("git %s exception: %s", " ".join(args), exc)
        return ""


def _attribution_ids(conversation_id: str) -> list[str]:
    """Return exact IDs that may identify *conversation_id* in a commit."""
    ids = [conversation_id]
    short_match = re.search(
        r"(?:^|[-_])([0-9a-f]{4,})$", conversation_id, re.IGNORECASE
    )
    if short_match and short_match.group(1).casefold() != conversation_id.casefold():
        ids.append(short_match.group(1))
    return ids


def _find_session_commits(session_id: str, repo: Path) -> list[str]:
    """Return reachable commits exactly attributed to a session, oldest first."""
    attribution_ids = _attribution_ids(session_id)
    grep_args: list[str] = []
    for attribution_id in attribution_ids:
        grep_args.extend(["--grep", attribution_id])
    log_output = _run_git(
        [
            "log",
            "HEAD",
            *grep_args,
            "--format=%H%x1f%B%x1e",
            "--reverse",
        ],
        cwd=repo,
    )
    if not log_output:
        return []

    commits = []
    for record in log_output.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        sha, separator, message = record.partition("\x1f")
        if not separator:
            continue
        trailers = re.findall(
            r"^Git-Session-Id:\s*(\S+)\s*$", message, re.IGNORECASE | re.MULTILINE
        )
        exact_trailer = any(
            trailer.casefold() == attribution_id.casefold()
            for trailer in trailers
            for attribution_id in attribution_ids
        )
        exact_subject_marker = any(
            re.search(
                _SESSION_MARKER_TEMPLATE.format(id=re.escape(attribution_id)),
                message.splitlines()[0],
                re.IGNORECASE,
            )
            for attribution_id in attribution_ids
        )
        if exact_trailer or exact_subject_marker:
            commits.append(sha.strip())

    return commits


def _get_entry_commit(first_session_commit: str, repo: Path) -> str:
    """Return the commit immediately before *first_session_commit*."""
    parent = _run_git(
        ["rev-parse", f"{first_session_commit}^"],
        cwd=repo,
    )
    return parent


def _get_files_changed(commits: list[str], repo: Path) -> list[str]:
    """Return files changed versus each commit's first parent."""
    files: set[str] = set()
    for sha in commits:
        output = _run_git(
            ["diff", "--name-only", f"{sha}^", sha],
            cwd=repo,
        )
        for line in output.splitlines():
            line = line.strip()
            if line:
                files.add(line)
    return sorted(files)


def _categorise_files(files: list[str]) -> str:
    """Infer a broad category from the set of changed files."""
    if not files:
        return "other"
    has_code = any(
        f.endswith((".py", ".ts", ".js", ".rs", ".go", ".sh", ".yaml", ".toml"))
        for f in files
    )
    has_docs = any(
        f.endswith((".md", ".rst", ".txt")) or "docs/" in f or "knowledge/" in f
        for f in files
    )
    has_journal = any("journal/" in f for f in files)

    if has_journal:
        return "journal"
    if has_code:
        return "code"
    if has_docs:
        return "docs"
    return "other"


def _extract_tool_call_counts(messages: list[dict]) -> dict[str, int]:
    """Count tool calls by tool name across all *messages*."""
    counts: Counter[str] = Counter()
    for msg in messages:
        role = msg.get("role", "")
        if role != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            # Markdown tool-use pattern: ```tool_name\n...```
            for m in re.finditer(r"```(\w[\w-]*)\n", content):
                counts[m.group(1)] += 1
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    counts[block.get("name", "unknown")] += 1
    return dict(counts)


def _get_task_description(messages: list[dict]) -> str:
    """Return the first non-system user message content (the task prompt)."""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                # Flatten content blocks to text
                content = "\n".join(
                    block["text"]
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            if isinstance(content, str) and content.strip():
                # Trim to a reasonable length for the training prompt
                return content.strip()[:2000]
    return ""


def _get_session_model(messages: list[dict]) -> str:
    """Return the model from the last assistant message metadata, or ''."""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            meta = msg.get("metadata", {})
            if isinstance(meta, dict):
                model = meta.get("model", "")
                if model:
                    return model
    return ""


def _get_session_duration(messages: list[dict]) -> float:
    """Return session duration in seconds by diffing first and last timestamps."""
    timestamps = []
    for msg in messages:
        ts = msg.get("timestamp", "")
        if ts:
            timestamps.append(ts)
    if len(timestamps) < 2:
        return 0.0
    try:
        from dateutil.parser import isoparse

        t0 = isoparse(timestamps[0])
        t1 = isoparse(timestamps[-1])
        return (t1 - t0).total_seconds()
    except Exception:
        return 0.0


def _load_session_messages(conv_path: Path) -> list[dict]:
    """Read a conversation.jsonl and return the list of message dicts."""
    messages = []
    try:
        with conv_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logger.debug("Could not read %s: %s", conv_path, exc)
    return messages


def scan_sessions(
    logs_dir: Path | None = None,
    limit: int | None = None,
    include_test: bool = False,
) -> Iterator[tuple[str, list[dict]]]:
    """Yield ``(session_id, messages)`` pairs for each gptme conversation.

    Args:
        logs_dir: Override the default gptme logs directory.
        limit: Maximum number of sessions to scan (newest first).
        include_test: Include conversations whose IDs indicate test/eval runs.
    """
    base = logs_dir or get_logs_dir()
    conv_files = sorted(
        base.glob("*/conversation.jsonl"),
        key=lambda p: -p.stat().st_mtime,
    )

    scanned = 0
    for conv_path in conv_files:
        if limit is not None and scanned >= limit:
            break
        conv_id = conv_path.parent.name
        if not include_test and _is_test_conversation_id(conv_id):
            continue
        messages = _load_session_messages(conv_path)
        if not messages:
            continue
        yield conv_id, messages
        scanned += 1


def _resolve_repo(repo_path: Path | None) -> Path:
    """Resolve *repo_path* to its enclosing Git worktree root."""
    candidate = (repo_path or Path.cwd()).resolve()
    root = _run_git(["rev-parse", "--show-toplevel"], cwd=candidate)
    if not root:
        raise ValueError(f"{candidate} is not a git repository")
    return Path(root).resolve()


def extract_environments(
    repo_path: Path | None = None,
    logs_dir: Path | None = None,
    limit: int | None = None,
    include_test: bool = False,
    min_commits: int = 1,
) -> Iterator[TaskEnvironment]:
    """Yield ``TaskEnvironment`` instances from the local gptme session corpus.

    Each environment is derived from a session that produced at least
    *min_commits* commits in *repo_path*.  Sessions with no matching commits
    are skipped (they are not convertible into reproducible environments).

    Args:
        repo_path: Git repository to mine for session-attributed commits.
            Defaults to the current working directory.
        logs_dir: Override the gptme logs directory.
        limit: Cap on how many sessions to scan (newest first).
        include_test: Include test/eval conversation IDs.
        min_commits: Minimum number of solution commits required (default 1).

    Yields:
        :class:`TaskEnvironment` for each convertible session.

    Example::

        envs = list(extract_environments(repo_path=Path("/path/to/repo"), limit=500))
        print(f"Convertible: {len(envs)}")
    """
    if min_commits < 1:
        raise ValueError("min_commits must be at least 1")
    repo = _resolve_repo(repo_path)

    for session_id, messages in scan_sessions(
        logs_dir=logs_dir, limit=limit, include_test=include_test
    ):
        commits = _find_session_commits(session_id, repo)
        if len(commits) < min_commits:
            logger.debug(
                "Skipping %s: only %d commits (need %d)",
                session_id,
                len(commits),
                min_commits,
            )
            continue

        entry = _get_entry_commit(commits[0], repo)
        if not entry:
            logger.debug("Skipping %s: no parent commit for %s", session_id, commits[0])
            continue

        files_changed = _get_files_changed(commits, repo)
        category = _categorise_files(files_changed)
        task_description = _get_task_description(messages)
        tool_call_counts = _extract_tool_call_counts(messages)
        model = _get_session_model(messages)
        duration_seconds = _get_session_duration(messages)

        yield TaskEnvironment(
            session_id=session_id,
            entry_commit=entry,
            solution_commits=commits,
            files_changed=files_changed,
            task_description=task_description,
            category=category,
            outcome="productive",
            tool_call_counts=tool_call_counts,
            model=model,
            duration_seconds=duration_seconds,
        )


def corpus_stats(
    repo_path: Path | None = None,
    logs_dir: Path | None = None,
    limit: int | None = None,
) -> dict:
    """Return a summary dict of corpus statistics.

    Scans sessions and reports how many are convertible into training
    environments.  Useful for a quick feasibility check before a full export.

    Args:
        repo_path: Git repository to mine for session-attributed commits.
        logs_dir: Override the gptme logs directory.
        limit: Cap on how many sessions to scan.

    Returns:
        A dict with keys: ``scanned``, ``convertible``, ``yield_pct``,
        ``category_counts``, ``model_counts``.
    """
    repo = _resolve_repo(repo_path)

    total = 0
    convertible = 0
    categories: Counter[str] = Counter()
    models: Counter[str] = Counter()

    for session_id, messages in scan_sessions(
        logs_dir=logs_dir, limit=limit, include_test=False
    ):
        total += 1
        commits = _find_session_commits(session_id, repo)
        if commits:
            convertible += 1
            files = _get_files_changed(commits, repo)
            categories[_categorise_files(files)] += 1
        model = _get_session_model(messages)
        if model:
            models[model] += 1

    return {
        "scanned": total,
        "convertible": convertible,
        "yield_pct": round(100 * convertible / total, 1) if total else 0.0,
        "category_counts": dict(categories),
        "model_counts": dict(models),
    }
