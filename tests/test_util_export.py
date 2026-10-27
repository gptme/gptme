"""Tests for util/export.py — replace_or_fail utility and HTML export."""

from unittest.mock import patch

import pytest

from gptme.logmanager import Log
from gptme.message import Message
from gptme.util.export import (
    _embed_cdn_resources,
    _fetch_cdn_resource,
    export_chat_to_html,
    replace_or_fail,
)

CDN_STUB = "/* cdn-stub */"

CDN_PATCH = "gptme.util.export._fetch_cdn_resource"


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


def test_export_chat_to_html_is_self_contained(tmp_path):
    """The real export must find its templates and inline them.

    Covers the template lookup itself: the files live in a package-data
    directory, so a missing or misplaced template only shows up here.
    """
    log = Log(
        [
            Message("user", "what is 2+2?"),
            Message("assistant", "4"),
        ]
    )
    out = tmp_path / "chat.html"

    with patch(CDN_PATCH, return_value=CDN_STUB):
        export_chat_to_html("test-conversation", log, out)

    html = out.read_text(encoding="utf-8")
    # templates were found and inlined, not left as external references
    assert "<style>" in html
    assert '<script type="module" src="/static/main.js">' not in html
    # CDN links were replaced with inline content
    assert "cdn.jsdelivr.net" not in html
    assert "cdnjs.cloudflare.com" not in html
    assert "cdn.tailwindcss.com" not in html
    # the conversation itself made it in
    assert "what is 2+2?" in html
    assert "test-conversation" in html


def test_embed_cdn_resources_inlines_scripts():
    """CDN <script src> tags are replaced with <script>CONTENT</script>."""
    html_in = (
        '<script src="https://cdn.jsdelivr.net/npm/vue@2.6.14/dist/vue.js"></script>\n'
        '<script src="https://cdn.tailwindcss.com"></script>\n'
    )
    with patch("gptme.util.export._fetch_cdn_resource", return_value=CDN_STUB):
        result = _embed_cdn_resources(html_in)
    assert f"<script>{CDN_STUB}</script>" in result
    assert "cdn.jsdelivr.net" not in result
    assert "cdn.tailwindcss.com" not in result


def test_embed_cdn_resources_inlines_stylesheets():
    """CDN <link rel=stylesheet> tags are replaced with <style>CONTENT</style>."""
    html_in = (
        '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/highlight.css">\n'
    )
    with patch("gptme.util.export._fetch_cdn_resource", return_value=CDN_STUB):
        result = _embed_cdn_resources(html_in)
    assert f"<style>{CDN_STUB}</style>" in result
    assert "cdnjs.cloudflare.com" not in result


def test_embed_cdn_resources_ignores_local_links():
    """Local <link> and <script> tags are not touched."""
    html_in = (
        '<link rel="icon" type="image/png" href="/favicon.png">\n'
        '<script type="module" src="/static/main.js"></script>\n'
    )
    with patch("gptme.util.export._fetch_cdn_resource", return_value=CDN_STUB) as m:
        result = _embed_cdn_resources(html_in)
    m.assert_not_called()
    assert result == html_in


def test_embed_cdn_resources_integrity_attrs_stripped():
    """Script tags with integrity/crossorigin attrs are still inlined."""
    html_in = (
        '<script src="https://cdnjs.cloudflare.com/highlight.min.js"'
        ' integrity="sha512-abc" crossorigin="anonymous"'
        ' referrerpolicy="no-referrer"></script>\n'
    )
    with patch("gptme.util.export._fetch_cdn_resource", return_value=CDN_STUB):
        result = _embed_cdn_resources(html_in)
    assert f"<script>{CDN_STUB}</script>" in result
    assert "integrity=" not in result


def test_fetch_cdn_resource_uses_cache(tmp_path):
    """Second fetch reads from disk cache without calling urlopen."""
    url = "https://example.com/lib.js"
    cached_content = "/* cached */"

    with patch("gptme.util.export._cdn_cache_dir", return_value=tmp_path):
        # Prime the cache
        url_hash = __import__("hashlib").sha256(url.encode()).hexdigest()[:16]
        (tmp_path / f"{url_hash}.js").write_text(cached_content, encoding="utf-8")

        with patch("urllib.request.urlopen") as mock_open:
            result = _fetch_cdn_resource(url)
            mock_open.assert_not_called()

    assert result == cached_content


def test_fetch_cdn_resource_raises_on_network_error(tmp_path):
    """A network failure raises a descriptive RuntimeError."""
    url = "https://example.com/missing.js"

    with (
        patch("gptme.util.export._cdn_cache_dir", return_value=tmp_path),
        patch("urllib.request.urlopen", side_effect=OSError("connection refused")),
        pytest.raises(RuntimeError, match="Failed to fetch CDN resource"),
    ):
        _fetch_cdn_resource(url)
