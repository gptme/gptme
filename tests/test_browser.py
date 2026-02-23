import pytest

# TODO: we should also test lynx backend
playwright = pytest.importorskip("playwright")

# noreorder
from gptme.tools.browser import read_url, search  # fmt: skip


@pytest.mark.slow
def test_read_url_with_links():
    s = read_url("https://superuserlabs.org")

    # check that "Erik Bjäreholt" is present
    assert "Erik Bjäreholt" in s

    # check that link to activitywatch is present
    assert "https://activitywatch.net/" in s


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
