"""Pure unit tests for browser formatting helpers — no playwright dependency."""

from gptme.tools._browser_format import format_snapshot as _format_snapshot


def test_format_snapshot():
    """Test that _format_snapshot prepends page metadata."""
    snapshot = '- heading "Hello World" [level=1]\n- link "About"'
    result = _format_snapshot(snapshot, "https://example.com/page", "Hello World")

    # Should start with metadata header
    assert result.startswith("Page: Hello World\n")
    assert "URL: https://example.com/page\n" in result

    # Should contain the original ARIA snapshot after metadata
    assert result.endswith(snapshot)

    # Metadata and snapshot should be separated by blank line
    lines = result.split("\n")
    assert lines[0] == "Page: Hello World"
    assert lines[1] == "URL: https://example.com/page"
    assert lines[2] == ""  # blank separator


def test_format_snapshot_redirect():
    """Test metadata reflects redirected URL, not original."""
    snapshot = '- heading "Redirected" [level=1]'
    result = _format_snapshot(
        snapshot, "https://example.com/new-location", "Redirected Page"
    )
    assert "URL: https://example.com/new-location" in result
    assert "Page: Redirected Page" in result
