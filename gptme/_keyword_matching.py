"""Shared keyword matching utilities for lessons and context selectors.

This module contains the core keyword/pattern matching logic used by both
the lesson matcher and context selector systems.
"""

import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=256)
def _keyword_to_pattern(keyword: str) -> re.Pattern[str] | None:
    """Convert a keyword (possibly with wildcards) to a compiled regex pattern.

    Wildcards:
    - '*' matches zero or more word characters (\\w*)

    All matching is case-insensitive. Input is normalized to lowercase
    for cache efficiency (different cases map to the same pattern).

    Args:
        keyword: Keyword string, optionally containing * wildcards

    Returns:
        Compiled regex pattern for matching, or None if keyword is empty

    Examples:
        "error" -> matches "error" literally
        "process killed at * seconds" -> matches "process killed at 120 seconds"
        "timeout*" -> matches "timeout", "timeout30s", "timeouts"
        "*" -> matches any word characters (including empty)
        "" -> returns None (empty keyword)
    """
    # Handle empty keyword
    if not keyword or not keyword.strip():
        return None

    # Normalize to lowercase for consistent caching
    keyword = keyword.lower().strip()

    if "*" in keyword:
        # Escape special regex chars except *, then replace * with \w*
        # First escape everything, then un-escape \* and replace with \w*
        escaped = re.escape(keyword)
        # re.escape converts * to \*, so we replace \* with \w*
        pattern_str = escaped.replace(r"\*", r"\w*")
    else:
        # Literal match - escape for safety
        pattern_str = re.escape(keyword)

    return re.compile(pattern_str, re.IGNORECASE)


@lru_cache(maxsize=128)
def _compile_pattern(pattern: str) -> re.Pattern[str] | None:
    """Compile a regex pattern string with error handling.

    Args:
        pattern: Raw regex pattern string

    Returns:
        Compiled pattern or None if invalid or empty
    """
    # Handle empty pattern
    if not pattern or not pattern.strip():
        return None

    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        logger.warning(f"Invalid regex pattern '{pattern}': {e}")
        return None


def _match_keyword(keyword: str, text: str) -> bool:
    """Check if a keyword matches the text.

    Supports wildcard (*) in keywords.

    Args:
        keyword: Keyword to match (may contain * wildcards)
        text: Text to search in

    Returns:
        True if keyword matches somewhere in text, False if no match or empty keyword
    """
    pattern = _keyword_to_pattern(keyword)
    if pattern is None:
        return False
    return pattern.search(text) is not None


def _match_pattern(pattern_str: str, text: str) -> bool:
    """Check if a regex pattern matches the text.

    Args:
        pattern_str: Regex pattern string
        text: Text to search in

    Returns:
        True if pattern matches somewhere in text, False if no match or invalid pattern
    """
    pattern = _compile_pattern(pattern_str)
    if pattern is None:
        return False
    return pattern.search(text) is not None
