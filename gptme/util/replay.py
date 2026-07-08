"""
Evidence replay for long-session context recovery.

Inspired by arXiv:2607.02509 (ReContext), this module scores messages from the
lossless master log by BM25 relevance to the current query, then injects top-k
relevant messages as pinned system messages before generation.

Enable with: GPTME_EVIDENCE_REPLAY=1

This prevents "lost in the middle" degradation in long sessions by surfacing
earlier relevant context that was compacted or truncated.
"""

import json
import logging
import re
import sqlite3
from pathlib import Path

from ..message import Message

logger = logging.getLogger(__name__)

EVIDENCE_PREFIX = "[Evidence from earlier in this conversation ("


def _dedup_content(content: str) -> str:
    """Return raw message content for both normal and replay-injected messages."""
    if content.startswith(EVIDENCE_PREFIX):
        _, sep, payload = content.partition("\n")
        if sep:
            return payload
    return content


def score_messages_bm25(
    messages: list[dict],
    query: str,
    top_k: int = 20,
) -> list[tuple[int, float]]:
    """
    Score messages by BM25 relevance to query using SQLite FTS5.

    Returns list of (message_idx, score) sorted by descending relevance.
    Only returns messages with a positive score.
    """
    if not messages or not query:
        return []

    # Build FTS5 query: use OR so documents matching any term score higher.
    # Strip punctuation from each token; skip very short stop-words.
    tokens = [re.sub(r"[^\w]", "", w) for w in query.split()]
    tokens = [t for t in tokens if len(t) > 2]
    if not tokens:
        return []
    safe_query = " OR ".join(f'"{t}"' for t in tokens)

    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute(
                "CREATE VIRTUAL TABLE msgs USING fts5(content, msg_idx UNINDEXED, tokenize='unicode61')"
            )
            conn.executemany(
                "INSERT INTO msgs VALUES (?, ?)",
                [
                    (str(msg.get("content", "")), i)
                    for i, msg in enumerate(messages)
                    if msg.get("content")
                ],
            )

            # FTS5 bm25() returns negative values: lower = more relevant
            rows = conn.execute(
                "SELECT msg_idx, bm25(msgs) as score FROM msgs WHERE msgs MATCH ?"
                " ORDER BY score LIMIT ?",
                (safe_query, top_k),
            ).fetchall()

        # Negate so higher = better, filter out zero/negative relevance
        return [(int(row[0]), -row[1]) for row in rows if row[1] < 0]

    except sqlite3.OperationalError as e:
        logger.debug(f"BM25 scoring failed: {e}")
        return []


def _read_master_messages(logfile: Path) -> list[dict]:
    """Read all messages from conversation.jsonl master log."""
    messages: list[dict] = []
    try:
        with open(logfile) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        logger.debug(f"Master log not found: {logfile}")
    return messages


def build_query(msgs: list[Message], n_turns: int = 3) -> str:
    """
    Build a BM25 query from the last n_turns user messages.

    Uses multiple recent turns so compound tasks (where the original intent is
    in an early message and the current sub-step is in a later one) surface
    transitively-relevant evidence that a single-message query would miss.
    """
    user_msgs = [m for m in msgs if m.role == "user"]
    if not user_msgs:
        return ""
    query_turns = user_msgs[-n_turns:] if n_turns > 0 else user_msgs[-1:]
    # Each turn truncated to 300 chars; joined compound query capped at 800 chars
    return " ".join(m.content[:300] for m in query_turns)[:800]


def inject_relevant_evidence(
    msgs: list[Message],
    master_logfile: Path,
    top_k: int = 5,
    token_budget_fraction: float = 0.10,
    query_n_turns: int = 3,
) -> list[Message]:
    """
    Inject top-k BM25-relevant messages from master log as pinned system messages.

    Scores master log messages by relevance to the last N user messages, then
    injects those not already present in the working context. Caps injected
    content at the available remaining context (model.context - current_tokens).

    Args:
        msgs: Current working context (already reduced/compacted by limit_log).
        master_logfile: Path to the lossless conversation.jsonl.
        top_k: Maximum number of messages to inject.
        token_budget_fraction: Unused; kept for API compatibility.
        query_n_turns: Number of recent user turns to use as the compound query.
            Default 3 covers the typical task horizon for multi-step sessions.
            Set to 1 to use only the last user message (note: per-turn truncation
            is 300 chars vs 500 chars in the original single-message implementation).

    Returns:
        Updated message list with relevant evidence injected as pinned system messages.
    """
    if not msgs:
        return msgs

    query = build_query(msgs, n_turns=query_n_turns)
    if not query:
        return msgs

    master_messages = _read_master_messages(master_logfile)
    scored = score_messages_bm25(master_messages, query, top_k=top_k * 4)
    if not scored:
        return msgs

    # Calculate budget as remaining space in context, not a fraction of total
    try:
        from ..llm.models import get_default_model
        from ..message import len_tokens as count_tokens

        model = get_default_model()
        model_context = model.context if model else 40_000
        current_tokens = count_tokens(msgs, model.model if model else "gpt-4")
        remaining = max(0, model_context - current_tokens)
        # Cap at 20% of total context to avoid aggressive over-filling
        token_budget = min(int(model_context * 0.20), remaining)
    except Exception:
        token_budget = 2_000

    # Build dedup set from current working context, including replay-injected
    # messages whose content is wrapped with an evidence prefix.
    existing_content = {_dedup_content(m.content) for m in msgs}

    injected: list[Message] = []
    tokens_used = 0

    for msg_idx, _score in scored:
        if len(injected) >= top_k:
            break

        raw = master_messages[msg_idx]
        content = raw.get("content", "")
        role = raw.get("role", "")

        if not content or content in existing_content:
            continue

        est_tokens = max(1, len(content) // 4)
        if tokens_used + est_tokens > token_budget:
            break

        evidence_msg = Message(
            role="system",
            content=f"[Evidence from earlier in this conversation ({role})]\n{content}",
            pinned=True,
            hide=True,
        )
        injected.append(evidence_msg)
        tokens_used += est_tokens
        existing_content.add(content)

    if not injected:
        return msgs

    logger.debug(
        "Evidence replay: injecting %d messages (~%d tokens)",
        len(injected),
        tokens_used,
    )

    # Insert evidence after the last pinned system message (init context boundary)
    insert_at = 0
    for i, m in enumerate(msgs):
        if m.role == "system" and m.pinned:
            insert_at = i + 1

    return msgs[:insert_at] + injected + msgs[insert_at:]
