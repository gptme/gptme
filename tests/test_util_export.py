"""Tests for util/export.py — replace_or_fail utility and HTML export."""

import base64
import hashlib
import html
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.logmanager import Log
from gptme.message import Message
from gptme.util.export import (
    _embed_cdn_resources,
    _fetch_cdn_resource,
    _verify_cdn_integrity,
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


@pytest.mark.parametrize(
    "name",
    [
        '<link rel="stylesheet" href="https://internal.example/private.css">',
        '</title><script src="https://internal.example/private.js"></script>',
    ],
)
def test_html_export_only_fetches_template_resources(tmp_path, name):
    with patch(CDN_PATCH, return_value=CDN_STUB) as fetch:
        export_chat_to_html("safe-name", Log([]), tmp_path / "baseline.html")
        template_calls = fetch.call_args_list.copy()
        fetch.reset_mock()
        export_chat_to_html(name, Log([]), tmp_path / "untrusted.html")
    assert fetch.call_args_list == template_calls
    exported = (tmp_path / "untrusted.html").read_text()
    assert f"<title>{html.escape(name)} - gptme</title>" in exported
    serialized_name = exported.split("window.CHAT_NAME = ", 1)[1].split(";", 1)[0]
    assert "<" not in serialized_name
    assert json.loads(serialized_name) == name


@pytest.mark.parametrize("closing_tag", ["</script>", "</ScRiPt >"])
def test_embed_cdn_script_preserves_closing_delimiter_in_string(closing_tag):
    source = f'const closingTag = "{closing_tag}"; window.ready = true;'
    with patch(CDN_PATCH, return_value=source):
        result = _embed_cdn_resources(
            '<script src="https://example.com/lib.js"></script>'
        )
    expected = source.replace("</", "<\\/")
    assert result == f"<script>{expected}</script>"


def test_embed_cdn_resources_does_not_scan_downloaded_source():
    script_url = "https://example.com/lib.js"
    stylesheet_url = "https://example.com/lib.css"
    source = 'const tag = \'<link rel="stylesheet" href="https://internal.example/private.css">\';'
    with patch(CDN_PATCH, side_effect=[source, "body {\n color: red;\n}"]) as fetch:
        result = _embed_cdn_resources(
            f'<script src="{script_url}"></script>'
            f'<link rel="stylesheet" href="{stylesheet_url}">'
        )
    assert [call.args for call in fetch.call_args_list] == [
        (script_url, ""),
        (stylesheet_url, ""),
    ]
    assert f"<script>{source}</script>" in result
    assert "<style>body {\n color: red;\n}</style>" in result


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


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("valid", [False, True])
def test_fetch_cdn_resource_verifies_integrity(tmp_path, cached, valid):
    content = b"// verified\r\nconst name = '\xc3\xa5';"
    expected = content if valid else b"different script"
    integrity = "sha256-" + base64.b64encode(hashlib.sha256(expected).digest()).decode()
    url = "https://example.com/lib.js"
    cache_path = tmp_path / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.js"
    if cached:
        cache_path.write_bytes(content)
    with (
        patch("gptme.util.export._cdn_cache_dir", return_value=tmp_path),
        patch("urllib.request.urlopen") as network,
    ):
        network.return_value.__enter__.return_value.read.return_value = content
        if valid:
            assert _fetch_cdn_resource(url, integrity) == content.decode()
        else:
            with pytest.raises(RuntimeError, match="integrity"):
                _fetch_cdn_resource(url, integrity)
            if not cached:
                assert not cache_path.exists()
        assert network.call_count == (0 if cached else 1)


def test_embed_cdn_resources_passes_integrity():
    with patch(CDN_PATCH, return_value=CDN_STUB) as fetch:
        _embed_cdn_resources(
            '<script src="https://example.com/lib.js" integrity="sha256-abc"></script>'
            '<link rel="stylesheet" href="https://example.com/lib.css" integrity="sha384-def">'
        )
    assert fetch.call_args_list[0].args == ("https://example.com/lib.js", "sha256-abc")
    assert fetch.call_args_list[1].args == ("https://example.com/lib.css", "sha384-def")


@pytest.mark.parametrize("failure", ["mkdir", "read", "write"])
def test_fetch_cdn_resource_cache_is_optional(tmp_path, failure):
    url = "https://example.com/lib.js"
    content = b"// downloaded"
    target = {
        "mkdir": "gptme.util.export._cdn_cache_dir",
        "read": "pathlib.Path.read_bytes",
        "write": "tempfile.NamedTemporaryFile",
    }[failure]
    cache_path = tmp_path / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.js"
    if failure == "read":
        cache_path.write_bytes(b"// unreadable cached copy")
    with (
        patch("gptme.util.export._cdn_cache_dir", return_value=tmp_path),
        patch("urllib.request.urlopen") as network,
        patch(target, side_effect=PermissionError("read-only cache")) as fail,
    ):
        network.return_value.__enter__.return_value.read.return_value = content
        assert _fetch_cdn_resource(url) == content.decode()
        fail.assert_called_once()
        network.assert_called_once()


def test_fetch_cdn_resource_publishes_complete_cache_atomically(tmp_path):
    url = "https://example.com/lib.js"
    content = b"// complete script"
    cache_path = tmp_path / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.js"
    replace = Path.replace
    published = []

    def check_replace(source, destination):
        assert source.parent == cache_path.parent
        assert source.read_bytes() == content
        assert not cache_path.exists()
        published.append(destination)
        return replace(source, destination)

    with (
        patch("gptme.util.export._cdn_cache_dir", return_value=tmp_path),
        patch("urllib.request.urlopen") as network,
        patch.object(Path, "replace", check_replace),
    ):
        network.return_value.__enter__.return_value.read.return_value = content
        assert _fetch_cdn_resource(url) == content.decode()
    assert published == [cache_path]
    assert list(tmp_path.iterdir()) == [cache_path]
    assert cache_path.read_bytes() == content


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


@pytest.mark.parametrize("algorithm", ["sha256", "sha384", "sha512"])
def test_cdn_integrity_supported_algorithms(algorithm):
    content = b"// script"
    digest = base64.b64encode(hashlib.new(algorithm, content).digest()).decode()
    _verify_cdn_integrity(content, f"{algorithm}-invalid {algorithm}-{digest}", "test")


def test_cdn_integrity_uses_strongest_algorithm():
    content = b"// script"
    weak = base64.b64encode(hashlib.sha256(content).digest()).decode()
    with pytest.raises(RuntimeError, match="integrity"):
        _verify_cdn_integrity(content, f"sha256-{weak} sha512-wrong", "test")


def test_fetch_cdn_resource_cleans_failed_cache_publication(tmp_path):
    with (
        patch("gptme.util.export._cdn_cache_dir", return_value=tmp_path),
        patch("urllib.request.urlopen") as network,
        patch.object(Path, "replace", side_effect=PermissionError("read-only")),
    ):
        network.return_value.__enter__.return_value.read.return_value = b"// script"
        assert _fetch_cdn_resource("https://example.com/lib.js") == "// script"
    assert not list(tmp_path.iterdir())
