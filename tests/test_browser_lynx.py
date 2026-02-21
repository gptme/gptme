"""Tests for lynx browser backend."""

import shutil
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
        result.stdout = b"mock page content"
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
        result.stdout = b"search results"
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
        result.stdout = b"mock content"
        return result

    with patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run):
        result = read_url("https://example.com/page")
        assert result == "mock content"
