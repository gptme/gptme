"""Tests for lynx browser backend."""

import os
import shutil
import stat
import sys
import tempfile
from unittest.mock import patch

import pytest

from gptme.tools._browser_lynx import _validate_url_scheme, read_url, search

lynx_available = shutil.which("lynx") is not None


def test_url_scheme_validation():
    """Test that dangerous URL schemes are blocked in lynx backend."""
    # Valid schemes should work
    _validate_url_scheme("https://example.com")
    _validate_url_scheme("http://example.com")
    _validate_url_scheme("HTTP://EXAMPLE.COM")  # Case insensitive

    # Dangerous schemes should be blocked
    with pytest.raises(ValueError, match="not allowed"):
        _validate_url_scheme("file:///etc/passwd")

    with pytest.raises(ValueError, match="not allowed"):
        _validate_url_scheme("ftp://example.com")

    with pytest.raises(ValueError, match="not allowed"):
        _validate_url_scheme("javascript:alert(1)")


def test_url_validation_rejects_missing_host_and_credentials():
    with pytest.raises(ValueError, match="hostname"):
        _validate_url_scheme("https://")

    with pytest.raises(ValueError, match="hostname"):
        _validate_url_scheme("https:///etc/passwd")

    with pytest.raises(ValueError, match="credentials"):
        _validate_url_scheme("https://user:password@example.com")


def test_url_validation_enforces_input_length():
    prefix = "https://example.com/"
    _validate_url_scheme(prefix + "a" * (2048 - len(prefix)))

    with pytest.raises(ValueError, match="2048"):
        _validate_url_scheme(prefix + "a" * (2049 - len(prefix)))


@pytest.mark.parametrize("cookies", [{"bad\nname": "value"}, {"name": "bad\tvalue"}])
def test_read_url_rejects_cookie_line_injection(cookies):
    with pytest.raises(ValueError, match="tabs/newlines"):
        read_url("https://example.com", cookies=cookies)


def test_read_url_cookie_file_is_private():
    observed_mode = None
    cookie_path = None

    def mock_run(cmd, **kwargs):
        nonlocal cookie_path, observed_mode
        cookie_path = next(
            arg.split("=", 1)[1] for arg in cmd if arg.startswith("-cookie_file=")
        )
        observed_mode = stat.S_IMODE(os.stat(cookie_path).st_mode)

        from unittest.mock import MagicMock

        result = MagicMock()
        result.stdout = "mock page content"
        return result

    with patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run):
        read_url("https://example.com", cookies={"CONSENT": "YES"})

    assert cookie_path is not None
    assert not os.path.exists(cookie_path)
    if sys.platform != "win32":
        assert observed_mode == 0o600


def test_read_url_closes_cookie_descriptor_when_chmod_fails():
    fd, cookie_path = tempfile.mkstemp()
    try:
        with (
            patch(
                "gptme.tools._browser_lynx.tempfile.mkstemp",
                return_value=(fd, cookie_path),
            ),
            patch("gptme.tools._browser_lynx.Path.chmod", side_effect=PermissionError),
            pytest.raises(PermissionError),
        ):
            read_url("https://example.com", cookies={"CONSENT": "YES"})

        with pytest.raises(OSError, match="WinError 6|Bad file descriptor"):
            os.fstat(fd)
        assert not os.path.exists(cookie_path)
    finally:
        if os.path.exists(cookie_path):
            os.close(fd)
            os.unlink(cookie_path)


def test_search_rejects_invalid_input():
    with pytest.raises(ValueError, match="non-empty"):
        search("   ")

    with pytest.raises(ValueError, match="2048"):
        search("a" * 2049)

    with pytest.raises(ValueError, match="Unknown search engine"):
        search("query", "bing")


@pytest.mark.parametrize(
    ("engine", "url_template"),
    [
        ("google", "https://www.google.com/search?q={query}&hl=en&gl=us"),
        ("duckduckgo", "https://lite.duckduckgo.com/lite/?q={query}"),
    ],
)
def test_search_accepts_query_that_fits_final_url_limit(
    monkeypatch, engine, url_template
):
    captured_url = None

    def mock_read_url(url, cookies=None):
        nonlocal captured_url
        captured_url = url
        return "search results"

    monkeypatch.setattr("gptme.tools._browser_lynx.read_url", mock_read_url)
    query = "a" * (2048 - len(url_template.format(query="")))

    assert search(query, engine) == "search results"
    assert captured_url is not None
    assert len(captured_url) <= 2048


@pytest.mark.slow
@pytest.mark.skipif(not lynx_available, reason="lynx not installed")
def test_read_url():
    """Test reading URLs with lynx backend."""
    content = read_url("https://gptme.org/")
    assert "Getting Started" in content
    content = read_url("https://github.com/gptme/gptme/issues/205")
    assert "lynx-backed browser tool" in content


@pytest.mark.slow
@pytest.mark.skipif(not lynx_available, reason="lynx not installed")
def test_search():
    """Test search with lynx backend."""
    result = search("Erik Bjäreholt", "duckduckgo")
    assert "erik.bjareholt.com" in result


def test_read_url_cookie_file():
    """Test that cookies are passed to lynx via a temporary cookie file."""
    cookies = {"CONSENT": "YES+42"}
    captured_cmd = None

    def mock_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd

        # Verify cookie file was created and contains correct content
        cookie_args = [arg for arg in cmd if arg.startswith("-cookie_file=")]
        assert len(cookie_args) == 1, "Expected -cookie_file argument"
        cookie_path = cookie_args[0].split("=", 1)[1]

        with open(cookie_path) as f:
            content = f.read()
        assert "# Netscape HTTP Cookie File" in content
        assert ".example.com" in content
        assert "CONSENT" in content
        assert "YES+42" in content

        assert "-accept_all_cookies" in cmd

        # Return a mock result
        from unittest.mock import MagicMock

        result = MagicMock()
        result.stdout = "mock page content"
        return result

    with patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run):
        result = read_url("https://example.com/search", cookies=cookies)
        assert result == "mock page content"
        assert captured_cmd is not None


def test_search_google_consent_cookies():
    """Test that Google search passes correct consent cookies."""
    captured_cmd = None
    captured_cookies = None

    def mock_run(cmd, **kwargs):
        nonlocal captured_cmd, captured_cookies
        captured_cmd = cmd

        # Extract and read cookie file
        cookie_args = [arg for arg in cmd if arg.startswith("-cookie_file=")]
        if cookie_args:
            cookie_path = cookie_args[0].split("=", 1)[1]
            with open(cookie_path) as f:
                captured_cookies = f.read()

        from unittest.mock import MagicMock

        result = MagicMock()
        result.stdout = "search results"
        return result

    with patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run):
        result = search("test query", "google")
        assert result == "search results"
        assert captured_cmd is not None
        # Verify URL includes gl=us to avoid consent redirects
        assert "gl=us" in captured_cmd[2]
        assert "hl=en" in captured_cmd[2]
        # Verify SOCS cookie (newer Google consent format)
        assert captured_cookies is not None
        assert "SOCS" in captured_cookies
        # Verify CONSENT cookie (fallback for older consent)
        assert "CONSENT" in captured_cookies
        assert "PENDING+987" in captured_cookies


def test_read_url_no_cookies():
    """Test that no cookie file is created when cookies is None."""
    captured_cmd = None

    def mock_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        cookie_args = [arg for arg in cmd if arg.startswith("-cookie_file=")]
        assert len(cookie_args) == 0, "Should not have -cookie_file without cookies"

        from unittest.mock import MagicMock

        result = MagicMock()
        result.stdout = "mock content"
        return result

    with patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run):
        result = read_url("https://example.com/page")
        assert result == "mock content"
