"""Tests for CLI confirmation preview language detection."""

from gptme.hooks.cli_confirm import _get_lang_for_tool, _looks_like_diff


def test_looks_like_diff_for_patch_minimal_body() -> None:
    """Patch.diff_minimal()-style body (no @@ headers) should be detected."""
    content = """ line1
-old
+new
 line3
"""
    assert _looks_like_diff(content) is True


def test_looks_like_diff_plus_minus_only() -> None:
    """Mixed +/- lines without context should still be detected as diff."""
    content = """-old
+new
"""
    assert _looks_like_diff(content) is True


def test_looks_like_diff_rejects_markdown_list() -> None:
    """Plain markdown bullet lists should not be treated as diffs."""
    content = """# Shopping

- Apples
- Bananas
- Oranges
"""
    assert _looks_like_diff(content) is False


def test_looks_like_diff_rejects_plus_list() -> None:
    """Plain + prefixed text should not be treated as diff."""
    content = "+ one\n+ two\n+ three\n"
    assert _looks_like_diff(content) is False


def test_get_lang_for_save_uses_diff_when_preview_is_diff() -> None:
    content = " line1\n-old\n+new\n"
    assert _get_lang_for_tool("save", content) == "diff"


def test_get_lang_for_save_plain_text_fallback() -> None:
    content = "# Notes\n\n- Apples\n- Bananas\n"
    assert _get_lang_for_tool("save", content) == "text"
