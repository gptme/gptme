"""Tests for browser search fallback logic and URL detection.

These tests use only monkeypatch/mock and have no playwright dependency.
Slow integration tests that require real browsers or API keys are in test_browser.py.
"""

from unittest.mock import Mock

from gptme.tools.browser import (  # fmt: skip
    _available_search_engines,
    _is_pdf_url,
    search,
)


def test_pdf_url_detection():
    """Test PDF URL detection."""
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
