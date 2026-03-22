"""Content scoring and extractive compression for auto-compacting.

Provides heuristic-based sentence scoring and extractive summarization
for compressing long messages while preserving high-value content.
"""

import re

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
