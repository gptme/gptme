"""Tests for gptme.util.keyword_matching module.

Tests the core keyword and pattern matching logic used by the lesson matcher
and context selector systems.
"""

import re

from gptme.util.keyword_matching import (
    _compile_pattern,
    _compile_pattern_cached,
    _keyword_to_pattern,
    _keyword_to_pattern_cached,
    _match_keyword,
    _match_pattern,
)


class TestKeywordToPattern:
    """Tests for _keyword_to_pattern — converting keywords to regex patterns."""

    def test_simple_keyword(self):
        """Simple keyword compiles to a case-insensitive pattern."""
        pattern = _keyword_to_pattern("error")
        assert pattern is not None
        assert pattern.search("error") is not None
        assert pattern.search("ERROR") is not None
        assert pattern.search("Error") is not None

    def test_empty_keyword_returns_none(self):
        assert _keyword_to_pattern("") is None

    def test_whitespace_only_returns_none(self):
        assert _keyword_to_pattern("   ") is None

    def test_single_wildcard_returns_none(self):
        """Single * returns None to prevent over-matching."""
        assert _keyword_to_pattern("*") is None
        assert _keyword_to_pattern(" * ") is None

    def test_wildcard_in_middle(self):
        """* matches zero or more word characters between literal spaces.

        The pattern `at * seconds` becomes `at \\w* seconds` — the spaces
        flanking the wildcard are literal, so zero-width \\w* expansion
        produces `at  seconds` (two spaces). Single-space `at seconds`
        does NOT match because the wildcard's surrounding spaces are both
        required.
        """
        pattern = _keyword_to_pattern("process killed at * seconds")
        assert pattern is not None
        assert pattern.search("process killed at 120 seconds") is not None
        assert pattern.search("PROCESS KILLED AT 5 SECONDS") is not None
        # Zero-width wildcard still leaves both literal spaces → "at  seconds"
        assert pattern.search("process killed at  seconds") is not None
        # Single space omits the wildcard slot entirely → no match
        assert pattern.search("process killed at seconds") is None

    def test_wildcard_at_end(self):
        pattern = _keyword_to_pattern("timeout*")
        assert pattern is not None
        assert pattern.search("timeout") is not None
        assert pattern.search("timeout30s") is not None
        assert pattern.search("timeouts") is not None

    def test_wildcard_at_start(self):
        pattern = _keyword_to_pattern("*error")
        assert pattern is not None
        assert pattern.search("RuntimeError") is not None
        assert pattern.search("error") is not None

    def test_wildcard_does_not_match_spaces(self):
        """Wildcard uses \\w* which does not match spaces."""
        pattern = _keyword_to_pattern("error*message")
        assert pattern is not None
        # \w* doesn't match spaces or punctuation
        assert pattern.search("error - message") is None
        assert pattern.search("error: message") is None
        # But matches word characters
        assert pattern.search("error_message") is not None
        assert pattern.search("errorXmessage") is not None

    def test_multiple_wildcards(self):
        pattern = _keyword_to_pattern("*test*")
        assert pattern is not None
        assert pattern.search("mytest123") is not None
        assert pattern.search("test") is not None

    def test_case_insensitive(self):
        pattern = _keyword_to_pattern("Hello World")
        assert pattern is not None
        assert pattern.search("hello world") is not None
        assert pattern.search("HELLO WORLD") is not None
        assert pattern.search("hElLo WoRlD") is not None

    def test_special_regex_chars_escaped(self):
        """Special regex characters in keywords are escaped."""
        pattern = _keyword_to_pattern("error (code)")
        assert pattern is not None
        assert pattern.search("error (code)") is not None
        # Should NOT treat parens as regex groups
        assert pattern.search("error code") is None

    def test_dot_escaped(self):
        pattern = _keyword_to_pattern("file.py")
        assert pattern is not None
        assert pattern.search("file.py") is not None
        # Dot should be literal, not regex "any char"
        assert pattern.search("fileXpy") is None

    def test_bracket_escaped(self):
        pattern = _keyword_to_pattern("[error]")
        assert pattern is not None
        assert pattern.search("[error]") is not None

    def test_leading_trailing_whitespace_stripped(self):
        """Keywords are stripped before processing."""
        pattern = _keyword_to_pattern("  error  ")
        assert pattern is not None
        assert pattern.search("error") is not None

    def test_case_normalization_for_cache(self):
        """Different cases produce the same pattern (cache hit)."""
        p1 = _keyword_to_pattern("Error")
        p2 = _keyword_to_pattern("error")
        p3 = _keyword_to_pattern("ERROR")
        # All should match the same things
        assert p1 is not None and p2 is not None and p3 is not None
        assert p1.search("test error here") is not None
        assert p2.search("test error here") is not None
        assert p3.search("test error here") is not None


class TestKeywordToPatternCached:
    """Tests for the cached inner function."""

    def test_returns_compiled_pattern(self):
        pattern = _keyword_to_pattern_cached("hello")
        assert isinstance(pattern, re.Pattern)

    def test_wildcard_pattern(self):
        pattern = _keyword_to_pattern_cached("test*case")
        assert isinstance(pattern, re.Pattern)
        assert pattern.search("testXcase") is not None

    def test_literal_pattern(self):
        pattern = _keyword_to_pattern_cached("exact match")
        assert isinstance(pattern, re.Pattern)
        assert pattern.search("exact match") is not None
        assert pattern.search("exact  match") is None


class TestCompilePattern:
    """Tests for _compile_pattern — regex pattern compilation."""

    def test_valid_pattern(self):
        pattern = _compile_pattern(r"error\d+")
        assert pattern is not None
        assert pattern.search("error42") is not None
        assert pattern.search("error") is None

    def test_empty_pattern_returns_none(self):
        assert _compile_pattern("") is None

    def test_whitespace_only_returns_none(self):
        assert _compile_pattern("   ") is None

    def test_invalid_regex_returns_none(self):
        """Invalid regex patterns return None instead of raising."""
        assert _compile_pattern("[unclosed") is None

    def test_case_insensitive(self):
        pattern = _compile_pattern("Error")
        assert pattern is not None
        assert pattern.search("error") is not None
        assert pattern.search("ERROR") is not None

    def test_complex_regex(self):
        pattern = _compile_pattern(r"(timeout|error)\s+in\s+\w+")
        assert pattern is not None
        assert pattern.search("timeout in module") is not None
        assert pattern.search("error in handler") is not None
        assert pattern.search("warning in handler") is None

    def test_stripped_before_compile(self):
        pattern = _compile_pattern("  error  ")
        assert pattern is not None
        assert pattern.search("error") is not None


class TestCompilePatternCached:
    """Tests for the cached inner function."""

    def test_returns_compiled_pattern(self):
        pattern = _compile_pattern_cached(r"\d+")
        assert isinstance(pattern, re.Pattern)

    def test_invalid_returns_none(self):
        result = _compile_pattern_cached("[bad")
        assert result is None

    def test_caching_returns_same_object(self):
        """Cache returns the exact same compiled pattern object."""
        # Use a unique pattern unlikely to collide with other tests
        unique = r"cache_test_\d+_unique_sentinel"
        p1 = _compile_pattern_cached(unique)
        p2 = _compile_pattern_cached(unique)
        assert p1 is p2


class TestMatchKeyword:
    """Tests for _match_keyword — keyword-in-text matching."""

    def test_basic_match(self):
        assert _match_keyword("error", "An error occurred")

    def test_no_match(self):
        assert not _match_keyword("error", "Everything is fine")

    def test_substring_match(self):
        """Keywords match as substrings."""
        assert _match_keyword("err", "error")
        assert _match_keyword("err", "berry")

    def test_case_insensitive_match(self):
        assert _match_keyword("ERROR", "an error occurred")
        assert _match_keyword("error", "AN ERROR OCCURRED")

    def test_empty_keyword_no_match(self):
        assert not _match_keyword("", "some text")

    def test_wildcard_match(self):
        assert _match_keyword("test*case", "testXcase")
        assert not _match_keyword("test*case", "test case")  # space not matched

    def test_empty_text(self):
        assert not _match_keyword("error", "")

    def test_multi_word_keyword(self):
        assert _match_keyword(
            "failed to connect", "The system failed to connect to the server"
        )
        assert not _match_keyword("failed to connect", "connection failed")

    def test_special_chars_in_keyword(self):
        """Special regex chars in keywords are treated literally."""
        assert _match_keyword("error (code)", "got error (code) 42")
        assert not _match_keyword("error (code)", "got error code 42")

    def test_keyword_in_long_text(self):
        text = "Lorem ipsum " * 100 + "specific keyword here" + " dolor sit" * 100
        assert _match_keyword("specific keyword", text)


class TestMatchPattern:
    """Tests for _match_pattern — regex pattern matching."""

    def test_basic_regex_match(self):
        assert _match_pattern(r"error\d+", "found error42 in logs")

    def test_no_match(self):
        assert not _match_pattern(r"error\d+", "found error in logs")

    def test_empty_pattern_no_match(self):
        assert not _match_pattern("", "some text")

    def test_invalid_pattern_no_match(self):
        """Invalid regex patterns don't raise, just return False."""
        assert not _match_pattern("[unclosed", "some text")

    def test_case_insensitive(self):
        assert _match_pattern("ERROR", "an error occurred")

    def test_complex_pattern(self):
        assert _match_pattern(
            r"(warn|error):\s+\w+",
            "2026-03-26 error: timeout",
        )

    def test_anchored_pattern(self):
        assert _match_pattern(r"^start", "start of line")
        assert not _match_pattern(r"^start", "not at start")

    def test_empty_text(self):
        assert not _match_pattern(r"something", "")

    def test_pattern_with_groups(self):
        """Patterns with capture groups work correctly."""
        assert _match_pattern(r"(timeout|error) in (\w+)", "timeout in handler")


class TestEdgeCases:
    """Edge cases and integration scenarios."""

    def test_keyword_with_newlines_in_text(self):
        """Keywords can match across a single line in multi-line text."""
        text = "line one\nerror occurred\nline three"
        assert _match_keyword("error occurred", text)

    def test_unicode_keyword(self):
        pattern = _keyword_to_pattern("café")
        assert pattern is not None
        assert pattern.search("I love café") is not None

    def test_unicode_text(self):
        assert _match_keyword("error", "日本語 error メッセージ")

    def test_very_long_keyword(self):
        """Long keywords don't cause issues."""
        keyword = "this is a very long keyword " * 10
        text = "prefix " + keyword + " suffix"
        assert _match_keyword(keyword.strip(), text)

    def test_pattern_none_keyword(self):
        """None-producing keywords (empty, single *) return False in match."""
        assert not _match_keyword("", "any text")
        assert not _match_keyword("*", "any text")

    def test_keyword_with_plus(self):
        """Plus sign is escaped (not regex +)."""
        assert _match_keyword("c++", "I write c++ code")
        assert not _match_keyword("c++", "I write c code")

    def test_keyword_with_question_mark(self):
        """Question mark is escaped (not regex ?)."""
        assert _match_keyword("what?", "what? really")

    def test_keyword_with_pipe(self):
        """Pipe is escaped (not regex alternation)."""
        assert _match_keyword("a|b", "choose a|b")
        assert not _match_keyword("a|b", "choose a or b")

    def test_keyword_with_caret(self):
        """Caret is escaped (not regex anchor)."""
        assert _match_keyword("^start", "the ^start marker")

    def test_keyword_with_dollar(self):
        """Dollar is escaped (not regex anchor)."""
        assert _match_keyword("$var", "expand $var here")
