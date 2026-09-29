"""Content scoring and extractive compression for auto-compacting.

Provides heuristic-based sentence scoring and extractive summarization
for compressing long messages while preserving high-value content.

Also provides relevance scoring for tool outputs (Phase 0 pre-pass):
score_tool_output_relevance() assigns each tool result a keep/drop score
based on age, error content, and whether any of its paths/commands appear
in later messages.  A low score + large size → eligible for pre-pass drop
before the Phase 2 truncation even fires.

PruneDecision is the per-message record emitted by the shadow/dry-run pass
(shadow_prune_stale_tool_outputs) so callers can analyse what Phase 0 would
have done without actually modifying the log.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ...message import Message


@dataclass(frozen=True)
class PruneDecision:
    """Record of a Phase-0 keep/drop decision for one tool-output message.

    Fields
    ------
    idx:
        Position in the original log.
    decision:
        ``"keep"`` — message would be left unchanged.
        ``"drop"`` — message would be replaced by a short stub.
    score:
        Relevance score returned by :func:`score_tool_output_relevance`.
        ``None`` when the message was not eligible (too recent, non-tool, etc.)
        and is always kept without scoring.
    tokens:
        Original token count of the message.
    stub_tokens:
        Token count of the replacement stub (only meaningful when
        ``decision == "drop"``; 0 for kept messages).
    tokens_saved:
        ``tokens - stub_tokens`` when dropped, else 0.
    content_digest:
        First 8 hex characters of the SHA-256 of *normalized* original
        content (see :func:`normalize_for_digest`). Compare against later
        tool outputs after the same normalization to measure false-drop
        rate. A later ``read`` of the same payload matches even though the
        raw message includes a path label, line numbers, and code fences.
        Shell-formatted output (prompts, pytest, ``ls``) still will not
        match a later ``read`` of an underlying file — that is a remaining
        limit of this metric, not a content match.
    """

    idx: int
    decision: Literal["keep", "drop"]
    score: float | None
    tokens: int
    stub_tokens: int
    tokens_saved: int
    content_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "idx": self.idx,
            "decision": self.decision,
            "score": self.score,
            "tokens": self.tokens,
            "stub_tokens": self.stub_tokens,
            "tokens_saved": self.tokens_saved,
            "content_digest": self.content_digest,
        }

    @staticmethod
    def _digest(content: str) -> str:
        return hashlib.sha256(normalize_for_digest(content).encode()).hexdigest()[:8]


_FENCE_OPEN = re.compile(r"^(`{3,4})[^\n]*\n")
_LINE_NUMBER_PREFIX = re.compile(r"(?m)^[ \t]*\d+[ \t|]")


def normalize_for_digest(content: str) -> str:
    """Strip read-tool framing so a reread hashes like the original payload.

    gptme's ``read`` tool wraps file contents in a markdown fence (3 or 4
    backticks) with a path label and cat -n line numbers. Hashing the raw
    wrapper would miss that reread and understate false drops.

    Remaining limit: shell-formatted output (prompts, pytest, ``ls`` columns)
    still will not match a later ``read`` of an underlying file. This digest
    is a same-payload check, not a path-identity check.
    """
    text = content.strip("\n")
    match = _FENCE_OPEN.match(text)
    if match:
        fence = match.group(1)
        rest = text[match.end() :]
        closing = f"\n{fence}"
        if rest.endswith(fence):
            text = rest.removesuffix(fence).removesuffix("\n")
        elif closing in rest:
            text = rest[: rest.rfind(closing)]
    first_line, sep, remainder = text.partition("\n")
    if (
        sep
        and first_line.startswith("[")
        and first_line.endswith("]")
        and "#" in first_line
    ):
        text = remainder
    return _LINE_NUMBER_PREFIX.sub("", text)


# --- Enhanced Scoring Patterns (Issue #149) ---
# Semantic patterns for value-aware retention
# These patterns identify high-value content that should be preserved during compression
# Pre-compiled at module level for performance

# Decision patterns - highest value (+2.0)
_DECISION_PATTERNS = [
    re.compile(r"\bwe('ll| will) use\b", re.IGNORECASE),
    re.compile(r"\bdecided to\b", re.IGNORECASE),
    re.compile(r"\bgoing with\b", re.IGNORECASE),
    re.compile(r"\bchoosing\b", re.IGNORECASE),
    re.compile(r"\bsolution is\b", re.IGNORECASE),
    re.compile(r"\bapproach is\b", re.IGNORECASE),
    re.compile(r"\bwe chose\b", re.IGNORECASE),
]

# Conclusion patterns (+1.5)
_CONCLUSION_PATTERNS = [
    re.compile(r"\btherefore\b", re.IGNORECASE),
    re.compile(r"\bin summary\b", re.IGNORECASE),
    re.compile(r"\bthe result is\b", re.IGNORECASE),
    re.compile(r"\bthis means\b", re.IGNORECASE),
    re.compile(r"\bconfirmed that\b", re.IGNORECASE),
    re.compile(r"\bin conclusion\b", re.IGNORECASE),
    re.compile(r"\bkey finding\b", re.IGNORECASE),
]

# Commitment patterns (+1.5)
# NOTE: More specific patterns to reduce false positives from generic "i will" usage
_COMMITMENT_PATTERNS = [
    re.compile(r"\bi'll\b", re.IGNORECASE),  # Contraction is usually commitment
    re.compile(
        r"\bi will (implement|create|fix|add|update|write|build)\b", re.IGNORECASE
    ),
    re.compile(r"\bnext steps?:?", re.IGNORECASE),  # Colon optional
    re.compile(r"\baction items?:?", re.IGNORECASE),  # Colon optional
    re.compile(r"\btodo:", re.IGNORECASE),
    re.compile(r"\bwill implement\b", re.IGNORECASE),
    re.compile(r"\bplan to\b", re.IGNORECASE),
    re.compile(
        r"\bgoing to (implement|create|fix|add|update|write|build)\b", re.IGNORECASE
    ),
]

# Action result patterns (+1.0)
_ACTION_RESULT_PATTERNS = [
    re.compile(r"\bcreated file\b", re.IGNORECASE),
    re.compile(r"\bfixed\b", re.IGNORECASE),
    re.compile(r"\bupdated\b", re.IGNORECASE),
    re.compile(r"\bimplemented\b", re.IGNORECASE),
    re.compile(r"\bcompleted\b", re.IGNORECASE),
    re.compile(r"\bmerged\b", re.IGNORECASE),
]

# Reference patterns - content likely to be referenced later
# Unix paths: /path/to/file.ext, ~/path/to/file, /usr/bin/script (extension optional)
# Windows paths: C:\path\to\file.ext, C:/path/to/file (extension optional)
_FILE_PATH_PATTERN = re.compile(
    r"(?:[/~][a-zA-Z0-9_\-./]+(?:\.[a-zA-Z0-9]+)?|[A-Za-z]:[/\\][a-zA-Z0-9_\-./\\]+(?:\.[a-zA-Z0-9]+)?)"
)
# Relative paths the absolute/home/drive pattern misses, e.g. src/config.py.
# Requires at least one slash and a file extension so "and/or" does not match.
# Negative lookbehind avoids matching inside URLs (https://host/path/file.py).
_RELATIVE_FILE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_./-])(?:\./)?(?:[A-Za-z0-9_.-]+/)+\.?[A-Za-z0-9_.-]+\.[A-Za-z0-9]+"
)
_URL_PATTERN = re.compile(r'https?://[^\s<>"\')]+')
_ERROR_INDICATOR_PATTERNS = [
    re.compile(r"\b(error|exception|traceback)\b", re.IGNORECASE),
    re.compile(r"\bfailed\b", re.IGNORECASE),
    re.compile(r"\bfailure\b", re.IGNORECASE),
]


def _score_semantic_importance(sentence: str) -> float:
    """
    Score sentence based on semantic content type.

    High-value content types:
    - Decisions: +2.0
    - Conclusions: +1.5
    - Commitments: +1.5
    - Action results: +1.0
    """
    score = 0.0

    # Decision patterns (highest value)
    # Patterns are pre-compiled with IGNORECASE, so no need to lowercase
    for pattern in _DECISION_PATTERNS:
        if pattern.search(sentence):
            score += 2.0
            break  # Don't double-count

    # Conclusion patterns
    for pattern in _CONCLUSION_PATTERNS:
        if pattern.search(sentence):
            score += 1.5
            break

    # Commitment patterns
    for pattern in _COMMITMENT_PATTERNS:
        if pattern.search(sentence):
            score += 1.5
            break

    # Action result patterns
    for pattern in _ACTION_RESULT_PATTERNS:
        if pattern.search(sentence):
            score += 1.0
            break

    return score


def _score_reference_potential(sentence: str) -> float:
    """
    Score sentence based on likelihood of being referenced later.

    High-reference content:
    - File paths (Unix and Windows): +1.0
    - URLs: +0.5
    - Error messages: +1.5
    """
    score = 0.0

    # File paths (Unix: /path/file.ext, ~/path, Windows: C:\path\file.ext)
    if _FILE_PATH_PATTERN.search(sentence):
        score += 1.0

    # URLs
    if _URL_PATTERN.search(sentence):
        score += 0.5

    # Error indicators (pre-compiled with IGNORECASE)
    for pattern in _ERROR_INDICATOR_PATTERNS:
        if pattern.search(sentence):
            score += 1.5
            break

    return score


def extract_code_blocks(content: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Extract code blocks from content, returning cleaned content and blocks.

    Args:
        content: Message content with potential code blocks

    Returns:
        Tuple of (content without code blocks, list of (marker, code block) tuples)
    """
    code_blocks: list[tuple[str, str]] = []
    code_block_pattern = r"```[\s\S]*?```"

    def replacer(match):
        marker = f"__CODE_BLOCK_{len(code_blocks)}__"
        code_blocks.append((marker, match.group(0)))
        return marker

    cleaned = re.sub(code_block_pattern, replacer, content)
    return cleaned, code_blocks


def score_sentence(sentence: str, position: int, total: int) -> float:
    """
    Score sentence importance using heuristics and semantic patterns.

    Higher scores for:
    - Sentences at beginning/end (positional bias)
    - Sentences with key terms
    - Shorter sentences (more information-dense)
    - Decisions, conclusions, and commitments (semantic patterns)
    - File paths, URLs, and error messages (reference potential)

    Args:
        sentence: The sentence to score
        position: Position in message (0-indexed)
        total: Total number of sentences

    Returns:
        Importance score (higher = more important)
    """
    score = 0.0

    # Positional bias: first and last sentences more important
    if position == 0:
        score += 2.0
    elif position == total - 1:
        score += 1.5
    elif position < 3:
        score += 1.0

    # Key term presence
    key_terms = [
        "error",
        "fail",
        "success",
        "complete",
        "implement",
        "fix",
        "bug",
        "issue",
        "result",
        "output",
        "TODO",
        "FIXME",
        "NOTE",
        "WARNING",
    ]
    lower_sentence = sentence.lower()
    for term in key_terms:
        if term.lower() in lower_sentence:
            score += 0.5

    # Length penalty: prefer shorter, denser sentences
    # But not too short (less than 10 chars is probably not useful)
    length = len(sentence)
    if length < 10:
        score -= 1.0
    elif length < 50:
        score += 0.3
    elif length > 200:
        score -= 0.2

    # Enhanced scoring: semantic importance and reference potential (Issue #149)
    # These patterns help preserve decisions, conclusions, file paths, etc.
    score += _score_semantic_importance(sentence)
    score += _score_reference_potential(sentence)

    return score


def compress_content(content: str, target_ratio: float = 0.7) -> str:
    """
    Compress content using extractive summarization.

    Preserves:
    - Code blocks (always kept)
    - Important sentences based on scoring
    - Overall structure

    Args:
        content: Content to compress
        target_ratio: Target length as ratio of original (0.7 = 30% reduction)

    Returns:
        Compressed content
    """
    # Extract and preserve code blocks
    cleaned, code_blocks = extract_code_blocks(content)

    # Split into sentences (simple split on . ! ?)
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    if len(sentences) <= 3:
        # Too few sentences to compress meaningfully
        return content

    # Keep sentences that contain code block markers (don't score them)
    marker_sentences = []
    scoreable_sentences = []
    for i, sent in enumerate(sentences):
        if "__CODE_BLOCK_" in sent:
            marker_sentences.append((i, sent))
        else:
            scoreable_sentences.append((i, sent))

    # Score scoreable sentences
    scored = [
        (score_sentence(sent, i, len(sentences)), i, sent)
        for i, sent in scoreable_sentences
    ]

    # Sort by score (keep highest scoring)
    scored.sort(key=lambda x: x[0], reverse=True)

    # Select top sentences to meet target ratio (excluding marker sentences)
    target_count = max(2, int(len(scoreable_sentences) * target_ratio))
    selected = scored[:target_count]

    # Combine selected sentences with marker sentences and sort by original position
    all_selected = [(i, sent) for _, i, sent in selected] + marker_sentences
    all_selected.sort(key=lambda x: x[0])

    # Reconstruct compressed content
    compressed = " ".join(sent for _, sent in all_selected)

    # Restore code blocks
    for marker, code_block in code_blocks:
        compressed = compressed.replace(marker, code_block)

    return compressed


# ---------------------------------------------------------------------------
# Phase-0 relevance scoring for tool outputs
# ---------------------------------------------------------------------------

# Minimum age (distance from the end of the log) before a tool output is even
# considered for the pre-pass.  Very recent results are always kept verbatim.
_PRUNE_MIN_AGE = 3


def _extract_file_paths(content: str) -> set[str]:
    """Return all file-path-like tokens found in content.

    Includes absolute Unix/Windows paths and relative paths such as
    ``src/config.py`` so a later mention of that file still counts as a
    reference.
    """
    return set(_FILE_PATH_PATTERN.findall(content)) | set(
        _RELATIVE_FILE_PATH_PATTERN.findall(content)
    )


def score_tool_output_relevance(
    msg: Message,
    idx: int,
    log: list[Message],
) -> float:
    """Score a tool-output message for 'still needed?'

    Returns a float in roughly [0, 5].  Higher = more likely to be needed.
    Callers should keep anything >= a threshold (e.g. 1.0) and drop the rest.

    Eligibility guards (hard-coded):
    - Pinned messages always score max.
    - Messages within the last ``_PRUNE_MIN_AGE`` positions always score max.
    - Only ``role=="system"`` messages are scored (callers still must filter
      to actual tool outputs so instructions/knowledge are not pruned).
    - Error/failure content always scores max (age must not override this).
    - File paths still referenced by a later message always score max.

    Heuristics (additive, for outputs that are not a hard keep):
    - Age penalty: -0.15 per position from the end (capped at -2.0)
    """
    if msg.role != "system":
        return 5.0  # non-tool messages: don't touch

    if msg.pinned:
        return 5.0

    log_length = len(log)
    distance_from_end = log_length - idx - 1

    if distance_from_end < _PRUNE_MIN_AGE:
        return 5.0  # too recent — always keep

    # Fail-safe: errors and still-referenced paths are always kept. An additive
    # boost is not enough — the age cap of -2.0 would otherwise drop an
    # unreferenced error (2.0 - 2.0 = 0.0) below the prune threshold.
    for pattern in _ERROR_INDICATOR_PATTERNS:
        if pattern.search(msg.content):
            return 5.0

    own_paths = _extract_file_paths(msg.content)
    if own_paths:
        later_content = "\n".join(m.content for m in log[idx + 1 :])
        if any(p in later_content for p in own_paths):
            return 5.0

    # Age penalty: older = less relevant (capped)
    return 0.0 - min(distance_from_end * 0.15, 2.0)
