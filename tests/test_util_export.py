"""Tests for util/export.py — replace_or_fail utility."""

import pytest

from gptme.util.export import replace_or_fail


def test_replace_or_fail_basic():
    """Successful replacement returns modified string."""
    result = replace_or_fail("<title>old</title>", "old", "new")
    assert result == "<title>new</title>"


def test_replace_or_fail_no_match():
    """Raises ValueError when old string not found."""
    with pytest.raises(ValueError, match="Failed to replace"):
        replace_or_fail("hello world", "missing", "replacement")


def test_replace_or_fail_custom_desc():
    """Error message includes custom description."""
    with pytest.raises(ValueError, match="the title tag"):
        replace_or_fail("hello", "missing", "new", desc="the title tag")


def test_replace_or_fail_default_desc():
    """Error message shows old string repr when no desc given."""
    with pytest.raises(ValueError, match="'missing'"):
        replace_or_fail("hello", "missing", "new")


def test_replace_or_fail_multiple_occurrences():
    """Replaces all occurrences (str.replace behavior)."""
    result = replace_or_fail("aaa", "a", "b")
    assert result == "bbb"


def test_replace_or_fail_empty_old_string():
    """Empty old string always matches (str.replace behavior)."""
    result = replace_or_fail("hello", "", "X")
    # str.replace with empty string inserts between every char
    assert result == "XhXeXlXlXoX"


def test_replace_or_fail_empty_new_string():
    """Replacing with empty string (deletion) works."""
    result = replace_or_fail("hello world", " world", "")
    assert result == "hello"


def test_replace_or_fail_html_content():
    """Works with realistic HTML content."""
    html = '<link rel="stylesheet" href="/static/style.css">'
    css = "body { color: red; }"
    result = replace_or_fail(
        html,
        '<link rel="stylesheet" href="/static/style.css">',
        f"<style>{css}</style>",
        "stylesheet link",
    )
    assert result == "<style>body { color: red; }</style>"


def test_replace_or_fail_preserves_surrounding():
    """Content before and after the match is preserved."""
    result = replace_or_fail("before TARGET after", "TARGET", "REPLACED")
    assert result == "before REPLACED after"
