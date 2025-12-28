"""Tests for lynx browser backend security."""

import pytest

from gptme.tools._browser_lynx import _validate_url_scheme


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
