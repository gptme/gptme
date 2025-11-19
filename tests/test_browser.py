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
    # TODO: test that we can read it
    # url = "https://arxiv.org/pdf/2410.12361v2"
    pass


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
