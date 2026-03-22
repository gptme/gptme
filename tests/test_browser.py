from unittest.mock import Mock

import pytest

# TODO: we should also test lynx backend
playwright = pytest.importorskip("playwright")

# noreorder
from gptme.tools.browser import (  # fmt: skip
    _available_search_engines,
    click_element,
    close_page,
    fill_element,
    open_page,
    read_page_text,
    read_url,
    scroll_page,
    search,
    snapshot_url,
)


@pytest.mark.slow
def test_read_url_with_links():
    s = read_url("https://superuserlabs.org")

    # check that "Erik Bjäreholt" is present
    assert "Erik Bjäreholt" in s

    # check that link to activitywatch is present
    assert "https://activitywatch.net/" in s


@pytest.mark.slow
def test_snapshot_url():
    """Test ARIA accessibility snapshot of a webpage."""
    snapshot = snapshot_url("https://example.com")

    # Should return non-empty string
    assert snapshot, "Snapshot should not be empty"
    assert len(snapshot) > 50, f"Snapshot too short: {len(snapshot)} chars"

    # Should contain page metadata header
    assert snapshot.startswith("Page: "), "Snapshot should start with page metadata"
    assert "URL: " in snapshot, "Snapshot should include current URL"

    # Should contain typical ARIA elements
    # example.com has a heading and a link
    assert "Example Domain" in snapshot, "Should contain the page title/heading"
    assert "link" in snapshot.lower() or "heading" in snapshot.lower(), (
        "Should contain ARIA role information"
    )


@pytest.mark.slow
def test_read_url_arxiv_pdf():
    """Test reading PDF content from arxiv."""
    url = "https://arxiv.org/pdf/2410.12361v2"
    content = read_url(url)

    # Verify we got text content (not an error message)
    assert not content.startswith("Error:"), f"PDF reading failed: {content}"

    # Verify we got substantial content
    assert len(content) > 1000, f"PDF content too short: {len(content)} chars"

    # Verify page markers are present
    assert "--- Page" in content, "Missing page markers in PDF content"

    # Should contain typical academic paper content
    # (avoid being too specific about content as papers may change)
    assert any(
        word in content.lower()
        for word in ["abstract", "introduction", "method", "result"]
    ), "PDF doesn't contain typical academic paper structure"


def test_pdf_url_detection():
    """Test PDF URL detection."""
    # noreorder
    from gptme.tools.browser import _is_pdf_url  # fmt: skip

    # Should detect PDFs by extension
    assert _is_pdf_url("https://example.com/document.pdf")
    assert _is_pdf_url("https://example.com/paper.PDF")
    assert _is_pdf_url("https://arxiv.org/pdf/2410.12361v2.pdf")

    # Should not detect non-PDFs
    assert not _is_pdf_url("https://example.com/page.html")
    assert not _is_pdf_url("https://example.com/")


def test_search_auto_selects_perplexity(monkeypatch):
    monkeypatch.setattr("gptme.tools.browser.has_perplexity", True)
    monkeypatch.setattr("gptme.tools.browser.browser", None)
    mock = Mock(return_value="perplexity ok")
    monkeypatch.setattr("gptme.tools.browser.search_perplexity", mock)

    result = search("what is gptme")

    assert result == "perplexity ok"
    mock.assert_called_once_with("what is gptme")


def test_search_falls_back_after_failure(monkeypatch):
    monkeypatch.setattr("gptme.tools.browser.has_perplexity", True)
    monkeypatch.setattr("gptme.tools.browser.browser", "lynx")
    monkeypatch.setattr(
        "gptme.tools.browser.search_perplexity",
        Mock(return_value="Error: Perplexity timeout"),
    )
    lynx_search = Mock(return_value="google ok")
    monkeypatch.setattr("gptme.tools.browser.search_lynx", lynx_search, raising=False)

    result = search("latest AI news")

    assert result == "google ok"
    lynx_search.assert_called_once_with("latest AI news", "google")


def test_search_rejects_unavailable_engine(monkeypatch):
    monkeypatch.setattr("gptme.tools.browser.has_perplexity", False)
    monkeypatch.setattr("gptme.tools.browser.browser", "lynx")

    result = search("query", "perplexity")

    assert result.startswith(
        "Error: Search engine 'perplexity' is not currently available."
    )
    assert "Available engines: google, duckduckgo" in result


def test_search_reports_all_failures(monkeypatch):
    monkeypatch.setattr("gptme.tools.browser.has_perplexity", True)
    monkeypatch.setattr("gptme.tools.browser.browser", "lynx")
    monkeypatch.setattr(
        "gptme.tools.browser.search_perplexity",
        Mock(return_value="Error: Perplexity quota exceeded"),
    )

    def fake_lynx(query: str, engine: str) -> str:
        if engine == "google":
            return "Error: Google blocked"
        return "Error: DuckDuckGo blocked"

    monkeypatch.setattr("gptme.tools.browser.search_lynx", fake_lynx, raising=False)

    result = search("query")

    assert result.startswith("Error: All available search backends failed")
    assert "- perplexity: Perplexity quota exceeded" in result
    assert "- google: Google blocked" in result
    assert "- duckduckgo: DuckDuckGo blocked" in result


def test_search_reports_requested_engine_failure(monkeypatch):
    monkeypatch.setattr("gptme.tools.browser.has_perplexity", False)
    monkeypatch.setattr("gptme.tools.browser.browser", "lynx")
    monkeypatch.setattr(
        "gptme.tools.browser.search_lynx",
        Mock(return_value="Error: Google blocked"),
        raising=False,
    )

    result = search("query", "google")

    assert result.startswith(
        "Error: The requested search backend 'google' failed for query 'query'."
    )
    assert "- google: Google blocked" in result


def test_search_continues_after_backend_exception(monkeypatch):
    monkeypatch.setattr("gptme.tools.browser.has_perplexity", True)
    monkeypatch.setattr("gptme.tools.browser.browser", "lynx")
    monkeypatch.setattr(
        "gptme.tools.browser.search_perplexity",
        Mock(return_value="Error: Perplexity timeout"),
    )

    def fake_lynx(query: str, engine: str) -> str:
        if engine == "google":
            raise RuntimeError("lynx exited 1")
        return "duckduckgo ok"

    monkeypatch.setattr("gptme.tools.browser.search_lynx", fake_lynx, raising=False)

    result = search("query")

    assert result == "duckduckgo ok"


def test_available_search_engines_prioritize_perplexity(monkeypatch):
    monkeypatch.setattr("gptme.tools.browser.has_perplexity", True)
    monkeypatch.setattr("gptme.tools.browser.browser", "lynx")

    assert _available_search_engines() == ["perplexity", "google", "duckduckgo"]


@pytest.mark.slow
def test_search_perplexity(monkeypatch):
    """Test Perplexity search with both API types."""
    import os

    # Skip if no API keys available
    has_perplexity = bool(os.getenv("PERPLEXITY_API_KEY"))
    has_openrouter = bool(os.getenv("OPENROUTER_API_KEY"))

    if not (has_perplexity or has_openrouter):
        pytest.skip("No PERPLEXITY_API_KEY or OPENROUTER_API_KEY available")

    # Test the search works
    results = search("what is gptme", "perplexity")
    assert results, "Should get results from Perplexity"
    assert "error" not in results.lower() or "Error" not in results, (
        f"Got error: {results}"
    )

    # If we have OpenRouter key, test that it works too
    if has_openrouter and not has_perplexity:
        # Clear Perplexity key to force OpenRouter usage
        monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
        results2 = search("what is gptme", "perplexity")
        assert results2, "Should get results from OpenRouter"
        assert "error" not in results2.lower() or "Error" not in results2, (
            f"Got error: {results2}"
        )


@pytest.mark.slow
def test_pdf_max_pages_default():
    """Test that PDF reading respects default max_pages limit."""
    # This PDF has 42 pages, so with default max_pages=10, we should see truncation note
    url = "https://arxiv.org/pdf/2410.12361v2"
    content = read_url(url)

    # Should have truncation note for large PDFs
    # Note: This test assumes the PDF has > 10 pages
    if "pages. Showing first" in content:
        # Verify the hint about reading more pages
        assert "max_pages=" in content, "Missing hint about max_pages parameter"


@pytest.mark.slow
def test_pdf_max_pages_custom():
    """Test PDF reading with custom max_pages parameter."""
    url = "https://arxiv.org/pdf/2410.12361v2"

    # Read only first 2 pages
    content = read_url(url, max_pages=2)

    # Should have Page 1 (required)
    assert "--- Page 1 ---" in content, "Page 1 should be present"

    # Should NOT have Page 3 or higher (the key test for max_pages)
    assert "--- Page 3 ---" not in content, (
        "Page 3 should NOT be present with max_pages=2"
    )
    assert "--- Page 4 ---" not in content, (
        "Page 4 should NOT be present with max_pages=2"
    )

    # Should have truncation note
    assert "pages. Showing first" in content, "Missing truncation note"
    assert "max_pages=" in content, "Missing hint about max_pages parameter"


@pytest.mark.slow
def test_pdf_vision_hint():
    """Test that PDF output includes vision-based reading hint."""
    url = "https://arxiv.org/pdf/2410.12361v2"
    content = read_url(url)

    # Should include vision fallback hint
    assert "vision" in content.lower(), "Missing vision-based reading hint"
    assert "garbled" in content.lower() or "incomplete" in content.lower(), (
        "Missing context for when to use vision"
    )


@pytest.mark.slow
def test_open_page():
    """Test opening a page for interactive browsing."""
    snapshot = open_page("https://example.com")

    # Should return ARIA snapshot with metadata
    assert snapshot, "Snapshot should not be empty"
    assert snapshot.startswith("Page: "), "Should include page metadata header"
    assert "URL: " in snapshot, "Should include current URL"
    assert "Example Domain" in snapshot, "Should contain the page title"


@pytest.mark.slow
def test_click_element():
    """Test clicking an element on an open page."""
    # Open example.com which has a link
    open_page("https://example.com")

    # Click the link using CSS selector (more reliable than text matching)
    snapshot = click_element("a")
    assert snapshot, "Click should return a valid snapshot"


@pytest.mark.slow
def test_scroll_page():
    """Test scrolling the current page."""
    open_page("https://example.com")

    # Scroll down — should return valid snapshot
    snapshot = scroll_page("down", 300)
    assert snapshot, "Scroll should return a valid snapshot"


@pytest.mark.slow
def test_fill_element():
    """Test filling a form field on an open page."""
    # Use DuckDuckGo which has a simple search input
    open_page("https://duckduckgo.com")

    # Fill the search box
    snapshot = fill_element("input[name='q']", "gptme test")
    assert snapshot, "Fill should return a valid snapshot"
    assert "gptme test" in snapshot, "Snapshot should reflect filled value"


def test_click_without_open_page():
    """Test that click_element fails gracefully without open_page."""
    close_page()  # Ensure no page is open
    with pytest.raises(RuntimeError, match="No page is open"):
        click_element("#some-button")


def test_fill_without_open_page():
    """Test that fill_element fails gracefully without open_page."""
    close_page()  # Ensure no page is open
    with pytest.raises(RuntimeError, match="No page is open"):
        fill_element("#some-input", "value")


def test_scroll_without_open_page():
    """Test that scroll_page fails gracefully without open_page."""
    close_page()  # Ensure no page is open
    with pytest.raises(RuntimeError, match="No page is open"):
        scroll_page("down", 300)


@pytest.mark.slow
def test_read_page_text():
    """Test reading text content of the current interactive page."""
    open_page("https://example.com")

    text = read_page_text()
    assert text, "Text content should not be empty"
    assert "Example Domain" in text, "Should contain the page heading"


def test_read_page_text_without_open_page():
    """Test that read_page_text fails gracefully without open_page."""
    close_page()  # Ensure no page is open
    with pytest.raises(RuntimeError, match="No page is open"):
        read_page_text()


def test_scroll_invalid_amount():
    """Test that scroll_page raises ValueError for non-positive amount."""
    # Argument validation runs before page-state check, so no browser needed
    with pytest.raises(ValueError, match="amount must be positive"):
        scroll_page("down", -100)
    with pytest.raises(ValueError, match="amount must be positive"):
        scroll_page("down", 0)
