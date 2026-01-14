import pytest

# TODO: we should also test lynx backend
playwright = pytest.importorskip("playwright")

# noreorder
from gptme.tools.browser import read_url, search  # fmt: skip


# FIXME: Broken? ~~Seems to work, for now~~ edit: not anymore
@pytest.mark.skip
@pytest.mark.slow
def test_search_ddg():
    results = search("test", "duckduckgo")
    assert "1." in results, f"{results=}"


# FIXME: Broken, sometimes hits CAPTCHA
@pytest.mark.skip
@pytest.mark.slow
def test_search_google():
    results = search("test", "google")
    assert "1." in results, f"{results=}"


@pytest.mark.slow
def test_read_url_with_links():
    s = read_url("https://superuserlabs.org")

    # check that "Erik Bjäreholt" is present
    assert "Erik Bjäreholt" in s

    # check that link to activitywatch is present
    assert "https://activitywatch.net/" in s


@pytest.mark.slow
def test_read_url_arxiv_html():
    # TODO: test that we can read it in a reasonable amount of tokens
    # url = "https://arxiv.org/html/2410.12361v2"
    pass


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
    assert any(word in content.lower() for word in ["abstract", "introduction", "method", "result"]), \
        "PDF doesn't contain typical academic paper structure"


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
    has_perplexity = os.getenv("PERPLEXITY_API_KEY") is not None
    has_openrouter = os.getenv("OPENROUTER_API_KEY") is not None

    if not (has_perplexity or has_openrouter):
        pytest.skip("No PERPLEXITY_API_KEY or OPENROUTER_API_KEY available")

    # Test the search works
    results = search("what is gptme", "perplexity")
    assert results, "Should get results from Perplexity"
    assert (
        "error" not in results.lower() or "Error" not in results
    ), f"Got error: {results}"

    # If we have OpenRouter key, test that it works too
    if has_openrouter and not has_perplexity:
        # Clear Perplexity key to force OpenRouter usage
        monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
        results2 = search("what is gptme", "perplexity")
        assert results2, "Should get results from OpenRouter"
        assert (
            "error" not in results2.lower() or "Error" not in results2
        ), f"Got error: {results2}"
