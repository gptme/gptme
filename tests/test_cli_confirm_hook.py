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


def test_looks_like_diff_plus_only_append() -> None:
    """Plus-only diffs (append to empty file) should be detected."""
    content = "+line1\n+line2\n+line3\n"
    assert _looks_like_diff(content) is True


def test_looks_like_diff_plus_only_code_append() -> None:
    """Plus-only code diffs from Patch.diff_minimal() should be detected."""
    content = '+def hello():\n+    print("world")'
    assert _looks_like_diff(content) is True


def test_get_lang_for_append_uses_diff_when_preview_is_diff() -> None:
    """Append tool should detect diff content the same as save."""
    content = " existing\n+new line\n"
    assert _get_lang_for_tool("append", content) == "diff"


def test_get_lang_for_append_plain_text_fallback() -> None:
    """Append tool should fall back to text for non-diff content."""
    content = "Just some plain text to append.\n"
    assert _get_lang_for_tool("append", content) == "text"


def test_get_lang_for_append_plus_only_diff() -> None:
    """Append to empty file produces plus-only diff — should detect as diff."""
    content = "+line1\n+line2\n+line3"
    assert _get_lang_for_tool("append", content) == "diff"
