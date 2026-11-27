"""Tests for the browser tool — web browsing, search, PDF reading, and screenshots.

Tests cover:
- URL type detection: _is_pdf_url, _is_github_repo_url
- Search engine management: _available_search_engines, _search_with_engine, search()
- PDF utilities: _get_pdf_to_image_hints, _read_pdf_url, pdf_to_images
- GitHub reading: _read_github_repo
- read_url routing: PDF, GitHub issue/PR, GitHub repo, normal browser
- Helper detection: has_playwright, has_lynx, _has_imagemagick, etc.
- Backend delegation: screenshot_url, snapshot_url, open_page, etc.
- Tool spec: registration, functions, init
"""

import base64
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from gptme.tools.browser import (
    DEFAULT_PDF_MAX_PAGES,
    SEARCH_ENGINE_ERROR_PREFIX,
    _available_search_engines,
    _available_search_engines_text,
    _get_pdf_to_image_hints,
    _is_github_repo_url,
    _is_pdf_url,
    _pdf_source_is_remote,
    _read_github_repo,
    _search_failed,
    _search_with_engine,
    has_lynx,
    has_playwright,
    read_url,
    search,
    tool,
)


def test_search_falls_back_when_perplexity_raises(monkeypatch):
    """A Perplexity client exception is an engine failure, so search() falls back."""
    import sys
    import types

    from gptme.tools._browser_perplexity import search_perplexity

    class _RaisingOpenAI:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("rate limited")

    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(OpenAI=_RaisingOpenAI)
    )
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    result = search_perplexity("x")
    assert result.startswith("Error:"), result
    assert "rate limited" in result

    monkeypatch.setattr("gptme.tools.browser.has_perplexity", True)
    monkeypatch.setattr("gptme.tools.browser.browser", "lynx")
    monkeypatch.setattr("gptme.tools.browser.search_perplexity", search_perplexity)
    lynx_search = MagicMock(return_value="google ok")
    monkeypatch.setattr("gptme.tools.browser.search_lynx", lynx_search, raising=False)

    assert search("x") == "google ok"


# ── URL type detection ────────────────────────────────────────────────


class TestIsPdfUrl:
    """Tests for _is_pdf_url — detects whether a URL points to a PDF."""

    def test_pdf_extension(self):
        assert _is_pdf_url("https://example.com/doc.pdf") is True

    def test_pdf_extension_uppercase(self):
        assert _is_pdf_url("https://example.com/DOC.PDF") is True

    def test_pdf_extension_mixed_case(self):
        assert _is_pdf_url("https://example.com/doc.Pdf") is True

    def test_html_extension(self):
        with patch("gptme.tools.browser.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.url = "https://example.com/page.html"
            mock_resp.headers = {"Content-Type": "text/html"}
            mock_resp.url = "https://example.com/page.html"
            mock_req.Session.return_value.request.return_value = mock_resp
            mock_req.RequestException = Exception
            assert _is_pdf_url("https://example.com/page.html") is False

    def test_no_extension_pdf_content_type(self):
        with patch("gptme.tools.browser.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.url = "https://example.com/document"
            mock_resp.headers = {"Content-Type": "application/pdf"}
            mock_resp.url = "https://example.com/document"
            mock_req.Session.return_value.request.return_value = mock_resp
            mock_req.RequestException = Exception
            assert _is_pdf_url("https://example.com/document") is True

    def test_no_extension_html_content_type(self):
        with patch("gptme.tools.browser.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.url = "https://example.com/page"
            mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
            mock_resp.url = "https://example.com/page"
            mock_req.Session.return_value.request.return_value = mock_resp
            mock_req.RequestException = Exception
            assert _is_pdf_url("https://example.com/page") is False

    def test_request_failure_non_pdf(self):
        """When HEAD request fails and URL doesn't end in .pdf, returns False."""
        with patch("gptme.tools.browser.requests") as mock_req:
            mock_req.Session.return_value.request.side_effect = Exception(
                "connection failed"
            )
            mock_req.RequestException = Exception
            assert _is_pdf_url("https://example.com/page") is False

    def test_pdf_extension_with_query_params(self):
        # .pdf?v=2 doesn't end with .pdf, so it falls through to HEAD check — mock it
        with patch("gptme.tools.browser.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.url = "https://example.com/doc.pdf?v=2"
            mock_resp.headers = {"Content-Type": "application/pdf"}
            mock_resp.url = "https://example.com/doc.pdf?v=2"
            mock_req.Session.return_value.request.return_value = mock_resp
            mock_req.RequestException = Exception
            assert _is_pdf_url("https://example.com/doc.pdf?v=2") is True
        with patch("gptme.tools.browser.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.url = "https://example.com/doc.pdf?v=2"
            mock_resp.headers = {"Content-Type": "text/html"}
            mock_resp.url = "https://example.com/doc.pdf?v=2"
            mock_req.Session.return_value.request.return_value = mock_resp
            mock_req.RequestException = Exception
            assert _is_pdf_url("https://example.com/doc.pdf?v=2") is False

    def test_rejects_file_scheme_before_head(self):
        with patch("gptme.tools.browser.requests") as mock_req:
            with pytest.raises(ValueError, match="not allowed"):
                _is_pdf_url("file:///etc/passwd")
            mock_req.Session.return_value.request.assert_not_called()

    def test_rejects_gopher_scheme_before_head(self):
        with patch("gptme.tools.browser.requests") as mock_req:
            with pytest.raises(ValueError, match="not allowed"):
                _is_pdf_url("gopher://example.com/x.pdf")
            mock_req.Session.return_value.request.assert_not_called()

    def test_accepts_uppercase_http_scheme(self):
        assert _is_pdf_url("HTTP://example.com/x.pdf") is True

    def test_rejects_missing_host(self):
        with pytest.raises(ValueError, match="hostname"):
            _is_pdf_url("https://")

    def test_rejects_embedded_credentials(self):
        with pytest.raises(ValueError, match="credentials"):
            _is_pdf_url("https://user:pass@example.com/x.pdf")


class TestIsGithubRepoUrl:
    """Tests for _is_github_repo_url — detects GitHub repository root URLs."""

    def test_simple_repo_url(self):
        assert _is_github_repo_url("https://github.com/owner/repo") is True

    def test_repo_url_trailing_slash(self):
        assert _is_github_repo_url("https://github.com/owner/repo/") is True

    def test_http_repo_url(self):
        assert _is_github_repo_url("http://github.com/owner/repo") is True

    def test_issue_url_not_repo(self):
        assert _is_github_repo_url("https://github.com/owner/repo/issues/1") is False

    def test_pr_url_not_repo(self):
        assert _is_github_repo_url("https://github.com/owner/repo/pull/1") is False

    def test_blob_url_not_repo(self):
        assert (
            _is_github_repo_url("https://github.com/owner/repo/blob/main/file.py")
            is False
        )

    def test_non_github_url(self):
        assert _is_github_repo_url("https://gitlab.com/owner/repo") is False

    def test_github_root(self):
        assert _is_github_repo_url("https://github.com") is False

    def test_github_user_only(self):
        assert _is_github_repo_url("https://github.com/owner") is False


# ── Search engine management ──────────────────────────────────────────


class TestSearchFailed:
    """Tests for _search_failed — checks if a search result is an error."""

    def test_error_result(self):
        assert _search_failed("Error: something went wrong") is True

    def test_success_result(self):
        assert _search_failed("Here are the search results...") is False

    def test_empty_result(self):
        assert _search_failed("") is False

    def test_error_prefix_only(self):
        assert _search_failed(SEARCH_ENGINE_ERROR_PREFIX) is True


class TestAvailableSearchEngines:
    """Tests for _available_search_engines — detects usable search backends."""

    @patch("gptme.tools.browser.has_perplexity", True)
    @patch("gptme.tools.browser.browser", "playwright")
    def test_all_available(self):
        engines = _available_search_engines()
        assert "perplexity" in engines
        assert "google" in engines

    @patch("gptme.tools.browser.has_perplexity", False)
    @patch("gptme.tools.browser.browser", None)
    def test_none_available(self):
        engines = _available_search_engines()
        assert len(engines) == 0

    @patch("gptme.tools.browser.has_perplexity", False)
    @patch("gptme.tools.browser.browser", "playwright")
    def test_playwright_only(self):
        engines = _available_search_engines()
        assert engines == ["google"]

    @patch("gptme.tools.browser.has_perplexity", False)
    @patch("gptme.tools.browser.browser", "lynx")
    def test_lynx_only(self):
        engines = _available_search_engines()
        assert "google" in engines
        assert "duckduckgo" in engines

    @patch("gptme.tools.browser.has_perplexity", True)
    @patch("gptme.tools.browser.browser", None)
    def test_perplexity_only(self):
        engines = _available_search_engines()
        assert engines == ["perplexity"]

    @patch("gptme.tools.browser.has_perplexity", True)
    @patch("gptme.tools.browser.browser", "playwright")
    def test_perplexity_first(self):
        """Perplexity should always be first (preferred)."""
        engines = _available_search_engines()
        assert engines[0] == "perplexity"


class TestAvailableSearchEnginesText:
    """Tests for _available_search_engines_text — display text for available engines."""

    @patch("gptme.tools.browser.has_perplexity", False)
    @patch("gptme.tools.browser.browser", None)
    def test_none_available(self):
        assert _available_search_engines_text() == "none"

    @patch("gptme.tools.browser.has_perplexity", True)
    @patch("gptme.tools.browser.browser", "playwright")
    def test_multiple_available(self):
        result = _available_search_engines_text()
        assert "perplexity" in result
        assert "google" in result


class TestSearchWithEngine:
    """Tests for _search_with_engine — executes search on a specific backend."""

    def test_unknown_engine_raises(self):
        with pytest.raises(ValueError, match="Unknown search engine"):
            _search_with_engine("query", "bing")  # type: ignore

    @patch("gptme.tools.browser.has_perplexity", False)
    def test_perplexity_unavailable(self):
        result = _search_with_engine("query", "perplexity")
        assert result.startswith("Error:")
        assert "Perplexity" in result

    @patch("gptme.tools.browser.has_perplexity", True)
    @patch("gptme.tools.browser.search_perplexity")
    def test_perplexity_available(self, mock_search):
        mock_search.return_value = "perplexity results"
        result = _search_with_engine("query", "perplexity")
        assert result == "perplexity results"
        mock_search.assert_called_once_with("query")

    @patch("gptme.tools.browser.browser", None)
    def test_google_no_browser(self):
        result = _search_with_engine("query", "google")
        assert result.startswith("Error:")
        assert "not available" in result

    @patch("gptme.tools.browser.browser", None)
    def test_duckduckgo_no_lynx(self):
        result = _search_with_engine("query", "duckduckgo")
        assert result.startswith("Error:")


class TestSearch:
    """Tests for search() — search with auto-fallback."""

    @patch("gptme.tools.browser.has_perplexity", False)
    @patch("gptme.tools.browser.browser", None)
    def test_no_backends_returns_error(self):
        result = search("query")
        assert result.startswith("Error:")
        assert "No search backends" in result

    @patch("gptme.tools.browser.has_perplexity", False)
    @patch("gptme.tools.browser.browser", None)
    def test_unavailable_engine_returns_error(self):
        result = search("query", engine="google")
        assert result.startswith("Error:")
        assert "not currently available" in result

    @patch("gptme.tools.browser._search_with_engine")
    @patch("gptme.tools.browser._available_search_engines")
    def test_first_engine_success(self, mock_available, mock_search):
        mock_available.return_value = ["perplexity", "google"]
        mock_search.return_value = "search results"

        result = search("query")

        assert result == "search results"
        mock_search.assert_called_once_with("query", "perplexity")

    @patch("gptme.tools.browser._search_with_engine")
    @patch("gptme.tools.browser._available_search_engines")
    def test_fallback_on_first_failure(self, mock_available, mock_search):
        mock_available.return_value = ["perplexity", "google"]
        mock_search.side_effect = [
            "Error: perplexity failed",
            "google search results",
        ]

        result = search("query")

        assert result == "google search results"
        assert mock_search.call_count == 2

    @patch("gptme.tools.browser._search_with_engine")
    @patch("gptme.tools.browser._available_search_engines")
    def test_all_engines_fail(self, mock_available, mock_search):
        mock_available.return_value = ["perplexity", "google"]
        mock_search.side_effect = [
            "Error: perplexity down",
            "Error: google blocked",
        ]

        result = search("query")

        assert result.startswith("Error:")
        assert "All available search backends failed" in result

    @patch("gptme.tools.browser._search_with_engine")
    @patch("gptme.tools.browser._available_search_engines")
    def test_exception_fallback(self, mock_available, mock_search):
        mock_available.return_value = ["perplexity", "google"]
        mock_search.side_effect = [
            RuntimeError("connection error"),
            "google results",
        ]

        result = search("query")

        assert result == "google results"

    @patch("gptme.tools.browser._search_with_engine")
    @patch("gptme.tools.browser._available_search_engines")
    def test_specific_engine(self, mock_available, mock_search):
        mock_available.return_value = ["perplexity", "google"]
        mock_search.return_value = "google results"

        search("query", engine="google")

        mock_search.assert_called_once_with("query", "google")

    @patch("gptme.tools.browser._search_with_engine")
    @patch("gptme.tools.browser._available_search_engines")
    def test_specific_engine_failure(self, mock_available, mock_search):
        """When a specific engine is requested and fails, no fallback."""
        mock_available.return_value = ["perplexity", "google"]
        mock_search.return_value = "Error: google blocked"

        result = search("query", engine="google")

        assert result.startswith("Error:")
        # Should only try the requested engine
        mock_search.assert_called_once_with("query", "google")


# ── PDF utilities ─────────────────────────────────────────────────────


class TestGetPdfToImageHints:
    """Tests for _get_pdf_to_image_hints — CLI tool detection hints."""

    @patch("gptme.tools.browser._has_vips", return_value=False)
    @patch("gptme.tools.browser._has_imagemagick", return_value=False)
    @patch("gptme.tools.browser._has_pdftoppm", return_value=True)
    def test_pdftoppm_available(self, *_):
        result = _get_pdf_to_image_hints()
        assert "pdftoppm" in result
        assert "pdf_to_images" in result

    @patch("gptme.tools.browser._has_vips", return_value=False)
    @patch("gptme.tools.browser._has_imagemagick", return_value=True)
    @patch("gptme.tools.browser._has_pdftoppm", return_value=True)
    def test_multiple_tools_available(self, *_):
        result = _get_pdf_to_image_hints()
        assert "pdftoppm" in result
        assert "convert" in result

    @patch("gptme.tools.browser._has_vips", return_value=False)
    @patch("gptme.tools.browser._has_imagemagick", return_value=False)
    @patch("gptme.tools.browser._has_pdftoppm", return_value=False)
    def test_no_tools_available(self, *_):
        result = _get_pdf_to_image_hints()
        assert "No PDF-to-image tools detected" in result
        assert "Install" in result


class TestReadPdfUrl:
    """Tests for _read_pdf_url — PDF text extraction from URLs."""

    @patch("gptme.tools.browser.has_pypdf", False)
    def test_no_pypdf_returns_error(self):
        from gptme.tools.browser import _read_pdf_url

        result = _read_pdf_url("https://example.com/doc.pdf")
        assert "pypdf" in result
        assert "Error" in result

    @patch("gptme.tools.browser.has_pypdf", True)
    @patch("gptme.tools.browser.requests")
    def test_rejects_file_scheme_before_get(self, mock_requests):
        from gptme.tools.browser import _read_pdf_url

        with pytest.raises(ValueError, match="not allowed"):
            _read_pdf_url("file:///tmp/x.pdf")
        mock_requests.Session.return_value.request.assert_not_called()

    @patch("gptme.tools.browser.has_pypdf", True)
    @patch("gptme.tools.browser.requests")
    def test_rejects_gopher_scheme_before_get(self, mock_requests):
        from gptme.tools.browser import _read_pdf_url

        with pytest.raises(ValueError, match="not allowed"):
            _read_pdf_url("gopher://example.com/x.pdf")
        mock_requests.Session.return_value.request.assert_not_called()

    @patch("gptme.tools.browser.has_pypdf", True)
    @patch("gptme.tools.browser.requests")
    def test_download_failure(self, mock_requests):
        from gptme.tools.browser import _read_pdf_url

        mock_requests.Session.return_value.request.side_effect = Exception(
            "connection error"
        )
        # pypdf may be absent in the test env; patch it so the (failing) fetch
        # is what produces the error, not the import.
        mock_pypdf = MagicMock()
        with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
            result = _read_pdf_url("https://example.com/doc.pdf")
        assert "Error" in result

    @patch("gptme.tools.browser.has_pypdf", True)
    @patch("gptme.tools.browser.requests")
    def test_successful_pdf_read(self, mock_requests):
        """Test PDF reading with mocked pypdf."""
        from gptme.tools.browser import _read_pdf_url

        # Create a minimal mock for pypdf
        mock_response = MagicMock()
        mock_response.url = "https://example.com/doc.pdf"
        mock_response.content = b"fake pdf content"
        mock_response.url = "https://example.com/doc.pdf"
        mock_requests.Session.return_value.request.return_value = mock_response
        mock_requests.RequestException = Exception

        mock_pypdf = MagicMock()
        with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
            mock_reader = MagicMock()
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "Page 1 text content"
            mock_reader.pages = [mock_page]
            mock_pypdf.PdfReader.return_value = mock_reader

            result = _read_pdf_url("https://example.com/doc.pdf")
            assert "Page 1" in result
            assert "Page 1 text content" in result

    @patch("gptme.tools.browser.has_pypdf", True)
    @patch("gptme.tools.browser.requests")
    def test_pdf_max_pages_truncation(self, mock_requests):
        """PDF with more pages than max_pages shows truncation notice."""
        from gptme.tools.browser import _read_pdf_url

        mock_response = MagicMock()
        mock_response.url = "https://example.com/doc.pdf"
        mock_response.content = b"fake"
        mock_response.url = "https://example.com/doc.pdf"
        mock_requests.Session.return_value.request.return_value = mock_response
        mock_requests.RequestException = Exception

        mock_pypdf = MagicMock()
        with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
            pages = []
            for i in range(20):
                p = MagicMock()
                p.extract_text.return_value = f"Page {i + 1} content"
                pages.append(p)
            mock_reader = MagicMock()
            mock_reader.pages = pages
            mock_pypdf.PdfReader.return_value = mock_reader

            result = _read_pdf_url("https://example.com/doc.pdf", max_pages=5)
            assert "Page 1" in result
            assert "5 pages" in result or "20 pages" in result
            # Should mention there are more pages
            assert "Note" in result

    @patch("gptme.tools.browser.has_pypdf", True)
    @patch("gptme.tools.browser.requests")
    def test_pdf_max_pages_zero_reads_all(self, mock_requests):
        """max_pages=0 reads all pages."""
        from gptme.tools.browser import _read_pdf_url

        mock_response = MagicMock()
        mock_response.url = "https://example.com/doc.pdf"
        mock_response.content = b"fake"
        mock_response.url = "https://example.com/doc.pdf"
        mock_requests.Session.return_value.request.return_value = mock_response
        mock_requests.RequestException = Exception

        mock_pypdf = MagicMock()
        with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
            pages = []
            for i in range(3):
                p = MagicMock()
                p.extract_text.return_value = f"Page {i + 1}"
                pages.append(p)
            mock_reader = MagicMock()
            mock_reader.pages = pages
            mock_pypdf.PdfReader.return_value = mock_reader

            result = _read_pdf_url("https://example.com/doc.pdf", max_pages=0)
            assert "Page 1" in result
            assert "Page 2" in result
            assert "Page 3" in result

    @patch("gptme.tools.browser.has_pypdf", True)
    @patch("gptme.tools.browser.requests")
    def test_pdf_empty_pages(self, mock_requests):
        """PDF with only empty pages returns error."""
        from gptme.tools.browser import _read_pdf_url

        mock_response = MagicMock()
        mock_response.url = "https://example.com/doc.pdf"
        mock_response.content = b"fake"
        mock_response.url = "https://example.com/doc.pdf"
        mock_requests.Session.return_value.request.return_value = mock_response
        mock_requests.RequestException = Exception

        mock_pypdf = MagicMock()
        with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "   "  # whitespace only
            mock_reader = MagicMock()
            mock_reader.pages = [mock_page]
            mock_pypdf.PdfReader.return_value = mock_reader

            result = _read_pdf_url("https://example.com/doc.pdf")
            assert "empty" in result.lower() or "Error" in result

    @patch("gptme.tools.browser.has_pypdf", True)
    @patch("gptme.tools.browser.requests")
    def test_pdf_default_max_pages(self, mock_requests):
        """Default max_pages uses DEFAULT_PDF_MAX_PAGES constant."""
        from gptme.tools.browser import _read_pdf_url

        mock_response = MagicMock()
        mock_response.url = "https://example.com/doc.pdf"
        mock_response.content = b"fake"
        mock_response.url = "https://example.com/doc.pdf"
        mock_requests.Session.return_value.request.return_value = mock_response
        mock_requests.RequestException = Exception

        mock_pypdf = MagicMock()
        with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
            pages = []
            for i in range(15):
                p = MagicMock()
                p.extract_text.return_value = f"Page {i + 1}"
                pages.append(p)
            mock_reader = MagicMock()
            mock_reader.pages = pages
            mock_pypdf.PdfReader.return_value = mock_reader

            result = _read_pdf_url("https://example.com/doc.pdf")  # no max_pages
            # Should only show DEFAULT_PDF_MAX_PAGES pages
            assert f"Page {DEFAULT_PDF_MAX_PAGES}" in result
            assert f"Page {DEFAULT_PDF_MAX_PAGES + 1}" not in result


class TestPdfSourceIsRemote:
    """Tests for _pdf_source_is_remote — HTTP vs local path classification."""

    def test_http_urls_are_remote(self):
        assert _pdf_source_is_remote("https://example.com/doc.pdf") is True
        assert _pdf_source_is_remote("HTTP://example.com/x.pdf") is True

    def test_posix_local_paths_stay_local(self):
        assert _pdf_source_is_remote("/tmp/doc.pdf") is False
        assert _pdf_source_is_remote("./doc.pdf") is False
        assert _pdf_source_is_remote("../doc.pdf") is False
        assert _pdf_source_is_remote("doc.pdf") is False

    def test_url_like_local_filenames_stay_local(self):
        assert _pdf_source_is_remote("https:report.pdf") is False
        assert _pdf_source_is_remote("/tmp/foo://bar.pdf") is False
        assert _pdf_source_is_remote("/tmp/https://example.com/doc.pdf") is False
        assert _pdf_source_is_remote("./https://example.com/doc.pdf") is False
        assert _pdf_source_is_remote("../https://example.com/doc.pdf") is False
        # Implicit-relative: :// is inside a path component, not a URL scheme.
        assert _pdf_source_is_remote("downloads/https://example.com/doc.pdf") is False
        assert _pdf_source_is_remote("C:/Users/https://example.com/doc.pdf") is False

    def test_windows_drive_letter_stays_local(self):
        assert _pdf_source_is_remote(r"C:\Users\file.pdf") is False
        assert _pdf_source_is_remote("C:/Users/file.pdf") is False
        # urlparse scheme is "c" and the string starts with "c://". Still a
        # drive letter, not a network URL.
        assert _pdf_source_is_remote("C://temp/file.pdf") is False

    def test_file_and_gopher_schemes_are_rejected(self):
        with pytest.raises(ValueError, match="not allowed"):
            _pdf_source_is_remote("file:///tmp/x.pdf")
        with pytest.raises(ValueError, match="not allowed"):
            _pdf_source_is_remote("gopher://example.com/x.pdf")

    def test_https_empty_host_is_rejected(self):
        with pytest.raises(ValueError, match="hostname"):
            _pdf_source_is_remote("https://")

    def test_embedded_credentials_are_rejected(self):
        with pytest.raises(ValueError, match="credentials"):
            _pdf_source_is_remote("https://user:pass@example.com/x.pdf")

    def test_http_url_stays_remote_even_if_cwd_has_scheme_dir(
        self, tmp_path, monkeypatch
    ):
        # POSIX collapses https://x to https:/x. A CWD directory named
        # "https:" must not skip host/credential checks or substitute a
        # local PDF for a fetch.
        monkeypatch.chdir(tmp_path)
        dest = tmp_path / "https:" / "example.com"
        dest.mkdir(parents=True)
        (dest / "x.pdf").write_bytes(b"%PDF-fake")
        cred_dest = tmp_path / "https:" / "user:pass@example.com"
        cred_dest.mkdir(parents=True)
        (cred_dest / "x.pdf").write_bytes(b"%PDF-fake")
        assert _pdf_source_is_remote("https://example.com/x.pdf") is True
        with pytest.raises(ValueError, match="credentials"):
            _pdf_source_is_remote("https://user:pass@example.com/x.pdf")

    def test_posix_collapsed_single_slash_spelling_stays_local(self):
        # Already-collapsed HTTP:/host/file is a path, not a URL (no ://).
        assert _pdf_source_is_remote("HTTP:/example.com/doc.pdf") is False

    def test_dotdot_in_http_url_stays_remote(self):
        assert (
            _pdf_source_is_remote("https://example.com/../../etc/ssh-report.pdf")
            is True
        )


class TestPdfToImages:
    """Tests for pdf_to_images — PDF conversion to image files."""

    def test_no_tools_raises(self, tmp_path):
        from gptme.tools.browser import pdf_to_images

        # File must exist — pdf_to_images checks existence before tool availability
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-fake")
        with (
            patch("gptme.tools.browser._has_pdftoppm", return_value=False),
            patch("gptme.tools.browser._has_imagemagick", return_value=False),
            patch("gptme.tools.browser._has_vips", return_value=False),
            pytest.raises(RuntimeError, match="No PDF-to-image tools"),
        ):
            pdf_to_images(str(pdf_file))

    @patch("gptme.tools.browser._has_pdftoppm", return_value=True)
    def test_local_file_not_found(self, _):
        from gptme.tools.browser import pdf_to_images

        with pytest.raises(FileNotFoundError):
            pdf_to_images("/tmp/nonexistent_pdf_file_12345.pdf")

    @patch("gptme.tools.browser._convert_with_pdftoppm")
    @patch("gptme.tools.browser._has_pdftoppm", return_value=True)
    def test_uses_pdftoppm_when_available(self, _, mock_convert, tmp_path):
        from gptme.tools.browser import pdf_to_images

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-fake")
        mock_convert.return_value = [tmp_path / "page-1.png"]

        result = pdf_to_images(str(pdf_file), output_dir=tmp_path)

        assert mock_convert.called
        assert len(result) == 1

    @patch("gptme.tools.browser._convert_with_imagemagick")
    @patch("gptme.tools.browser._has_imagemagick", return_value=True)
    @patch("gptme.tools.browser._has_pdftoppm", return_value=False)
    def test_falls_back_to_imagemagick(self, _, __, mock_convert, tmp_path):
        from gptme.tools.browser import pdf_to_images

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-fake")
        mock_convert.return_value = [tmp_path / "page-1.png"]

        pdf_to_images(str(pdf_file), output_dir=tmp_path)

        assert mock_convert.called

    @patch("gptme.tools.browser._convert_with_vips")
    @patch("gptme.tools.browser._has_vips", return_value=True)
    @patch("gptme.tools.browser._has_imagemagick", return_value=False)
    @patch("gptme.tools.browser._has_pdftoppm", return_value=False)
    def test_falls_back_to_vips(self, _, __, ___, mock_convert, tmp_path):
        from gptme.tools.browser import pdf_to_images

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-fake")
        mock_convert.return_value = [tmp_path / "page-1.png"]

        pdf_to_images(str(pdf_file), output_dir=tmp_path)

        assert mock_convert.called

    @patch("gptme.tools.browser.requests")
    @patch("gptme.tools.browser._convert_with_pdftoppm")
    @patch("gptme.tools.browser._has_pdftoppm", return_value=True)
    def test_downloads_url(self, _, mock_convert, mock_requests, tmp_path):
        from gptme.tools.browser import pdf_to_images

        mock_response = MagicMock()
        mock_response.url = "https://example.com/doc.pdf"
        mock_response.content = b"%PDF-fake"
        mock_response.url = "https://example.com/doc.pdf"
        mock_requests.Session.return_value.request.return_value = mock_response
        mock_requests.ConnectionError = Exception
        mock_convert.return_value = []

        pdf_to_images("https://example.com/doc.pdf", output_dir=tmp_path)

        mock_requests.Session.return_value.request.assert_called_once()

    @patch("gptme.tools.browser.requests")
    @patch("gptme.tools.browser._convert_with_pdftoppm")
    @patch("gptme.tools.browser._has_pdftoppm", return_value=True)
    def test_downloads_uppercase_http_url(
        self, _, mock_convert, mock_requests, tmp_path
    ):
        from gptme.tools.browser import pdf_to_images

        mock_response = MagicMock()
        mock_response.url = "https://example.com/doc.pdf"
        mock_response.content = b"%PDF-fake"
        mock_response.url = "HTTP://example.com/x.pdf"
        mock_requests.Session.return_value.request.return_value = mock_response
        mock_requests.ConnectionError = Exception
        mock_convert.return_value = []

        pdf_to_images("HTTP://example.com/x.pdf", output_dir=tmp_path)

        mock_requests.Session.return_value.request.assert_called_once_with(
            "GET",
            "HTTP://example.com/x.pdf",
            timeout=60,
            allow_redirects=False,
        )

    @patch("gptme.tools.browser.requests")
    @patch("gptme.tools.browser._convert_with_pdftoppm")
    @patch("gptme.tools.browser._has_pdftoppm", return_value=True)
    def test_http_url_fetches_even_if_cwd_has_scheme_dir(
        self, _, mock_convert, mock_requests, tmp_path, monkeypatch
    ):
        from gptme.tools.browser import pdf_to_images

        monkeypatch.chdir(tmp_path)
        dest = tmp_path / "HTTP:" / "example.com"
        dest.mkdir(parents=True)
        (dest / "doc.pdf").write_bytes(b"%PDF-fake")
        mock_response = MagicMock()
        mock_response.url = "HTTP://example.com/doc.pdf"
        mock_response.content = b"%PDF-remote"
        mock_response.url = "HTTP://example.com/doc.pdf"
        mock_requests.Session.return_value.request.return_value = mock_response
        mock_requests.ConnectionError = Exception
        mock_convert.return_value = []

        pdf_to_images("HTTP://example.com/doc.pdf", output_dir=tmp_path)

        mock_requests.Session.return_value.request.assert_called_once_with(
            "GET",
            "HTTP://example.com/doc.pdf",
            timeout=60,
            allow_redirects=False,
        )

    @patch("gptme.tools.browser.requests")
    @patch("gptme.tools.browser._convert_with_pdftoppm")
    @patch("gptme.tools.browser._has_pdftoppm", return_value=True)
    def test_absolute_path_with_embedded_url_substring(
        self, _, mock_convert, mock_requests, tmp_path
    ):
        from gptme.tools.browser import pdf_to_images

        url_dir = tmp_path / "https:" / "example.com"
        url_dir.mkdir(parents=True)
        pdf_file = url_dir / "doc.pdf"
        pdf_file.write_bytes(b"%PDF-fake")
        mock_convert.return_value = []

        # POSIX collapses the extra slash, so this string contains "://"
        # but names the same local file.
        weird = f"{tmp_path}/https://example.com/doc.pdf"
        pdf_to_images(weird, output_dir=tmp_path)

        mock_requests.Session.return_value.request.assert_not_called()
        mock_convert.assert_called_once()

    @patch("gptme.tools.browser.requests")
    @patch("gptme.tools.browser._convert_with_pdftoppm")
    @patch("gptme.tools.browser._has_pdftoppm", return_value=True)
    def test_https_colon_filename_is_local(
        self, _, mock_convert, mock_requests, tmp_path, monkeypatch
    ):
        from gptme.tools.browser import pdf_to_images

        monkeypatch.chdir(tmp_path)
        pdf_file = tmp_path / "https:report.pdf"
        pdf_file.write_bytes(b"%PDF-fake")
        mock_convert.return_value = []

        pdf_to_images("https:report.pdf", output_dir=tmp_path)

        mock_requests.Session.return_value.request.assert_not_called()
        mock_convert.assert_called_once()

    @patch("gptme.tools.browser.requests")
    @patch("gptme.tools.browser._has_pdftoppm", return_value=True)
    def test_rejects_file_url(self, _, mock_requests, tmp_path):
        from gptme.tools.browser import pdf_to_images

        with pytest.raises(ValueError, match="not allowed"):
            pdf_to_images("file:///tmp/x.pdf", output_dir=tmp_path)
        mock_requests.Session.return_value.request.assert_not_called()

    @patch("gptme.tools.browser.requests")
    @patch("gptme.tools.browser._has_pdftoppm", return_value=True)
    def test_rejects_gopher_url(self, _, mock_requests, tmp_path):
        from gptme.tools.browser import pdf_to_images

        with pytest.raises(ValueError, match="not allowed"):
            pdf_to_images("gopher://example.com/x.pdf", output_dir=tmp_path)
        mock_requests.Session.return_value.request.assert_not_called()

    @patch("gptme.tools.browser._convert_with_pdftoppm")
    @patch("gptme.tools.browser._has_pdftoppm", return_value=True)
    def test_creates_output_dir(self, _, mock_convert, tmp_path):
        from gptme.tools.browser import pdf_to_images

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-fake")
        out_dir = tmp_path / "new_subdir"
        mock_convert.return_value = []

        pdf_to_images(str(pdf_file), output_dir=out_dir)

        assert out_dir.exists()


# ── GitHub reading ────────────────────────────────────────────────────


class TestReadGithubRepo:
    """Tests for _read_github_repo — reads GitHub repo info via gh CLI."""

    @patch("gptme.tools.browser.subprocess.run")
    def test_successful_read(self, mock_run):
        repo_data = {
            "name": "gptme",
            "description": "A tool for terminal AI",
            "url": "https://github.com/gptme/gptme",
            "stargazerCount": 1234,
            "forkCount": 56,
            "licenseInfo": {"name": "MIT License"},
            "repositoryTopics": [{"name": "ai"}, {"name": "terminal"}],
            "homepageUrl": "https://gptme.org",
            "defaultBranchRef": {"name": "master"},
        }
        readme_data = {"content": base64.b64encode(b"# gptme\n\nA tool").decode()}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(repo_data)),
            MagicMock(returncode=0, stdout=json.dumps(readme_data)),
        ]

        result = _read_github_repo("https://github.com/gptme/gptme")

        assert "gptme" in result
        assert "1234" in result  # stars
        assert "MIT" in result
        assert "ai" in result  # topic
        assert "master" in result
        assert "# gptme" in result  # readme content
        assert "gh repo view" in result  # source attribution

    @patch("gptme.tools.browser._read_url_with_browser")
    @patch("gptme.tools.browser.subprocess.run")
    def test_gh_failure_falls_back_to_browser(self, mock_run, mock_browser):
        mock_run.return_value = MagicMock(returncode=1, stderr="gh: not logged in")
        mock_browser.return_value = "browser content"

        result = _read_github_repo("https://github.com/owner/repo")

        assert result == "browser content"

    @patch("gptme.tools.browser._read_url_with_browser")
    @patch("gptme.tools.browser.subprocess.run")
    def test_gh_not_installed_falls_back(self, mock_run, mock_browser):
        mock_run.side_effect = FileNotFoundError("gh not found")
        mock_browser.return_value = "browser content"

        result = _read_github_repo("https://github.com/owner/repo")

        assert result == "browser content"

    @patch("gptme.tools.browser._read_url_with_browser")
    def test_non_github_url_falls_back(self, mock_browser):
        mock_browser.return_value = "browser content"
        result = _read_github_repo("https://not-github.com/owner/repo")
        assert result == "browser content"

    @patch("gptme.tools.browser.subprocess.run")
    def test_missing_readme_still_works(self, mock_run):
        repo_data = {
            "name": "test-repo",
            "description": None,
            "url": "https://github.com/o/r",
            "stargazerCount": 0,
            "forkCount": 0,
            "defaultBranchRef": None,
        }
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(repo_data)),
            MagicMock(returncode=1, stderr="not found"),  # no readme
        ]

        result = _read_github_repo("https://github.com/o/r")

        assert "test-repo" in result
        # Should work without readme
        assert "Error" not in result


# ── read_url routing ──────────────────────────────────────────────────


class TestReadUrl:
    """Tests for read_url — routes to appropriate handler based on URL type."""

    @patch("gptme.tools.browser._read_pdf_url")
    @patch("gptme.tools.browser._is_pdf_url", return_value=True)
    def test_routes_pdf_to_pdf_reader(self, _, mock_pdf):
        mock_pdf.return_value = "PDF content"
        result = read_url("https://example.com/doc.pdf")
        assert result == "PDF content"

    @patch("gptme.tools.browser._read_pdf_url")
    @patch("gptme.tools.browser._is_pdf_url", return_value=True)
    def test_passes_max_pages_to_pdf(self, _, mock_pdf):
        mock_pdf.return_value = "PDF content"
        read_url("https://example.com/doc.pdf", max_pages=5)
        mock_pdf.assert_called_once_with("https://example.com/doc.pdf", 5)

    @patch("gptme.tools.browser.get_github_issue_content")
    @patch("gptme.tools.browser.parse_github_url")
    @patch("gptme.tools.browser._is_pdf_url", return_value=False)
    def test_routes_github_issue(self, _, mock_parse, mock_issue):
        mock_parse.return_value = {
            "owner": "gptme",
            "repo": "gptme",
            "number": 123,
            "type": "issue",
        }
        mock_issue.return_value = "Issue #123 content"

        result = read_url("https://github.com/gptme/gptme/issues/123")

        assert "Issue #123 content" in result
        assert "gh issue view" in result

    @patch("gptme.tools.browser.get_github_pr_content")
    @patch("gptme.tools.browser.parse_github_url")
    @patch("gptme.tools.browser._is_pdf_url", return_value=False)
    def test_routes_github_pr(self, _, mock_parse, mock_pr):
        mock_parse.return_value = {
            "owner": "gptme",
            "repo": "gptme",
            "number": 456,
            "type": "pull",
        }
        mock_pr.return_value = "PR #456 content"

        result = read_url("https://github.com/gptme/gptme/pull/456")

        assert "PR #456 content" in result
        assert "gh pr view" in result

    @patch("gptme.tools.browser._read_github_repo")
    @patch("gptme.tools.browser._is_github_repo_url", return_value=True)
    @patch("gptme.tools.browser.parse_github_url", return_value=None)
    @patch("gptme.tools.browser._is_pdf_url", return_value=False)
    def test_routes_github_repo(self, _, __, ___, mock_repo):
        mock_repo.return_value = "repo content"
        result = read_url("https://github.com/gptme/gptme")
        assert result == "repo content"

    @patch("gptme.tools.browser._read_url_with_browser")
    @patch("gptme.tools.browser.transform_github_url")
    @patch("gptme.tools.browser._is_github_repo_url", return_value=False)
    @patch("gptme.tools.browser.parse_github_url", return_value=None)
    @patch("gptme.tools.browser._is_pdf_url", return_value=False)
    def test_routes_normal_url_to_browser(
        self, _, __, ___, mock_transform, mock_browser
    ):
        mock_transform.return_value = "https://example.com"  # no transform
        mock_browser.return_value = "page content"
        result = read_url("https://example.com")
        assert result == "page content"

    @patch("gptme.tools.browser._read_url_with_browser")
    @patch("gptme.tools.browser.transform_github_url")
    @patch("gptme.tools.browser._is_github_repo_url", return_value=False)
    @patch("gptme.tools.browser.parse_github_url", return_value=None)
    @patch("gptme.tools.browser._is_pdf_url", return_value=False)
    def test_transforms_github_blob_url(self, _, __, ___, mock_transform, mock_browser):
        """GitHub blob URLs are transformed to raw content URLs."""
        mock_transform.return_value = (
            "https://raw.githubusercontent.com/o/r/main/file.py"
        )
        mock_browser.return_value = "file content"

        read_url("https://github.com/o/r/blob/main/file.py")

        # Browser should receive the transformed raw URL
        mock_browser.assert_called_once_with(
            "https://raw.githubusercontent.com/o/r/main/file.py"
        )


# ── Backend delegation (playwright-only functions) ─────────────────────


class TestBackendDelegation:
    """Tests for functions that delegate to playwright/lynx backends."""

    @patch("gptme.tools.browser.browser", None)
    def test_screenshot_url_no_browser(self):
        from gptme.tools.browser import screenshot_url

        with pytest.raises(AssertionError):
            screenshot_url("https://example.com")

    @patch("gptme.tools.browser.browser", None)
    def test_snapshot_url_no_browser(self):
        from gptme.tools.browser import snapshot_url

        with pytest.raises(AssertionError):
            snapshot_url("https://example.com")

    @patch("gptme.tools.browser.browser", None)
    def test_open_page_no_browser(self):
        from gptme.tools.browser import open_page

        with pytest.raises(AssertionError):
            open_page("https://example.com")

    @patch("gptme.tools.browser.browser", None)
    def test_close_page_no_browser(self):
        from gptme.tools.browser import close_page

        with pytest.raises(AssertionError):
            close_page()

    @patch("gptme.tools.browser.browser", None)
    def test_read_page_text_no_browser(self):
        from gptme.tools.browser import read_page_text

        with pytest.raises(AssertionError):
            read_page_text()

    @patch("gptme.tools.browser.browser", None)
    def test_click_element_no_browser(self):
        from gptme.tools.browser import click_element

        with pytest.raises(AssertionError):
            click_element("button")

    @patch("gptme.tools.browser.browser", None)
    def test_fill_element_no_browser(self):
        from gptme.tools.browser import fill_element

        with pytest.raises(AssertionError):
            fill_element("input", "text")

    @patch("gptme.tools.browser.browser", None)
    def test_scroll_page_no_browser(self):
        from gptme.tools.browser import scroll_page

        with pytest.raises(AssertionError):
            scroll_page("down")

    @patch("gptme.tools.browser.browser", None)
    def test_read_logs_no_browser(self):
        from gptme.tools.browser import read_logs

        with pytest.raises(AssertionError):
            read_logs()


# ── Helper detection ──────────────────────────────────────────────────


class TestHelperDetection:
    """Tests for tool/backend detection functions."""

    def test_has_playwright_returns_bool(self):
        result = has_playwright()
        assert isinstance(result, bool)

    def test_has_lynx_returns_bool(self):
        result = has_lynx()
        assert isinstance(result, bool)

    @patch("gptme.tools.browser.shutil.which", return_value="/usr/bin/convert")
    def test_has_imagemagick_true(self, _):
        from gptme.tools.browser import _has_imagemagick

        assert _has_imagemagick() is True

    @patch("gptme.tools.browser.shutil.which", return_value=None)
    def test_has_imagemagick_false(self, _):
        from gptme.tools.browser import _has_imagemagick

        assert _has_imagemagick() is False

    @patch("gptme.tools.browser.shutil.which", return_value="/usr/bin/pdftoppm")
    def test_has_pdftoppm_true(self, _):
        from gptme.tools.browser import _has_pdftoppm

        assert _has_pdftoppm() is True

    @patch("gptme.tools.browser.shutil.which", return_value=None)
    def test_has_pdftoppm_false(self, _):
        from gptme.tools.browser import _has_pdftoppm

        assert _has_pdftoppm() is False

    @patch("gptme.tools.browser.shutil.which", return_value="/usr/bin/vips")
    def test_has_vips_true(self, _):
        from gptme.tools.browser import _has_vips

        assert _has_vips() is True

    @patch("gptme.tools.browser.shutil.which", return_value=None)
    def test_has_vips_false(self, _):
        from gptme.tools.browser import _has_vips

        assert _has_vips() is False


# ── Tool spec ─────────────────────────────────────────────────────────


class TestToolSpec:
    """Tests for browser tool registration and metadata."""

    def test_tool_name(self):
        assert tool.name == "browser"

    def test_tool_has_description(self):
        assert tool.desc
        assert "browse" in tool.desc.lower() or "web" in tool.desc.lower()

    def test_tool_has_functions(self):
        assert tool.functions is not None
        assert len(tool.functions) >= 10

    def test_tool_functions_include_core(self):
        assert tool.functions is not None
        fn_names = [f.name for f in tool.functions]
        assert "read_url" in fn_names
        assert "search" in fn_names
        assert "screenshot_url" in fn_names
        assert "snapshot_url" in fn_names
        assert "open_page" in fn_names
        assert "close_page" in fn_names
        assert "read_page_text" in fn_names
        assert "click_element" in fn_names
        assert "fill_element" in fn_names
        assert "scroll_page" in fn_names
        assert "read_logs" in fn_names
        assert "pdf_to_images" in fn_names

    def test_tool_available_is_callable(self):
        assert callable(tool.available)

    def test_tool_has_init(self):
        assert tool.init is not None

    def test_tool_has_examples(self):
        assert tool.examples is not None

    def test_tool_instructions_format(self):
        assert tool.instructions_format is not None
        assert "tool" in tool.instructions_format


class TestExamples:
    """Tests for example output generation."""

    def test_examples_markdown(self):
        from gptme.tools.browser import examples

        result = examples("markdown")
        assert "read_url" in result
        assert "search" in result

    def test_examples_xml(self):
        from gptme.tools.browser import examples

        result = examples("xml")
        assert "read_url" in result

    def test_examples_contain_search(self):
        from gptme.tools.browser import examples

        result = examples("markdown")
        assert "search(" in result

    def test_examples_contain_screenshot(self):
        from gptme.tools.browser import examples

        result = examples("markdown")
        assert "screenshot_url" in result

    def test_examples_contain_pdf(self):
        from gptme.tools.browser import examples

        result = examples("markdown")
        assert "pdf" in result.lower() or "PDF" in result


# ── Playwright Markdown response handling ────────────────────────────


@pytest.mark.skipif(not has_playwright(), reason="playwright not installed")
class TestPlaywrightMarkdownResponse:
    @pytest.mark.parametrize(
        "content_type",
        ["text/markdown", "Text/Markdown", "text/markdown; charset=utf-8"],
    )
    def test_load_page_returns_markdown_with_per_call_metadata(self, content_type):
        from gptme.tools import _browser_playwright as bp

        markdown = "    indented code\n\n| a  | b |\n| -- | - |"
        page = MagicMock()
        page.url = "https://example.com"
        page.goto.return_value.headers = {"content-type": content_type}
        page.text_content.return_value = markdown
        managed = MagicMock(page=page)

        with (
            patch.object(bp, "_create_page", return_value=managed),
            patch.object(bp, "get_context_options", return_value={}),
        ):
            result = bp._load_page(MagicMock(), "https://example.com")

        assert result == (markdown, True)
        page.text_content.assert_called_once_with("body")
        page.inner_text.assert_not_called()
        managed.close.assert_called_once_with()

    def test_read_url_uses_markdown_flag_from_same_load(self):
        from gptme.tools import _browser_playwright as bp

        with (
            patch.object(
                bp,
                "_execute_with_retry",
                return_value=("# Already Markdown", True),
            ),
            patch.object(bp, "html_to_markdown") as convert,
        ):
            assert bp.read_url("https://example.com") == "# Already Markdown"

        convert.assert_not_called()

    def test_read_url_strips_inline_data_images_from_markdown(self):
        from gptme.tools import _browser_playwright as bp

        markdown = "before ![inline](data:image/png;base64,abc123) after"
        with patch.object(
            bp,
            "_execute_with_retry",
            return_value=(markdown, True),
        ):
            assert bp.read_url("https://example.com") == "before  after"


# ── _load_page network-idle wait (gptme/gptme#5126) ───────────────────


@pytest.mark.skipif(not has_playwright(), reason="playwright not installed")
class TestLoadPageNetworkIdle:
    """The network-idle wait after goto() is best effort: a timeout is
    ignored, other failures are logged, and the page is read either way."""

    def _load(self, wait_error: Exception):
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.url = "https://example.com"
        page.goto.return_value.headers = {"content-type": "text/markdown"}
        page.text_content.return_value = "# loaded"
        page.wait_for_load_state.side_effect = wait_error
        managed = MagicMock(page=page)

        with (
            patch.object(bp, "_create_page", return_value=managed),
            patch.object(bp, "get_context_options", return_value={}),
        ):
            result = bp._load_page(MagicMock(), "https://example.com")

        page.wait_for_load_state.assert_called_once_with(
            "networkidle", timeout=bp.NETWORK_IDLE_TIMEOUT_MS
        )
        managed.close.assert_called_once_with()
        return result, bp._last_logs[bp._conversation_key()]["errors"]

    def test_network_idle_timeout_is_ignored(self):
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        result, errors = self._load(PlaywrightTimeoutError("never idle"))

        assert result == ("# loaded", True)
        assert errors == []

    def test_other_network_idle_failure_is_logged(self):
        result, errors = self._load(RuntimeError("target closed"))

        assert result == ("# loaded", True)
        assert errors == ["Navigation error: target closed"]


# ── _create_page (CDP tab reuse vs launched contexts) ─────────────────


@pytest.mark.skipif(not has_playwright(), reason="playwright not installed")
class TestCreatePage:
    """Test page creation: CDP reuses a session context (tabs), launched
    browsers get an isolated owned context."""

    def test_cdp_reuses_session_context_and_applies_headers(self):
        from gptme.tools import _browser_playwright as bp

        thread = MagicMock()
        thread.cdp_url = "http://127.0.0.1:9222"
        ctx = thread._session_context = MagicMock()
        page = ctx.new_page.return_value
        browser = MagicMock()

        with patch.object(bp, "_browser", thread):
            managed = bp._create_page(
                browser,
                locale="en-US",
                extra_http_headers={"Accept": "text/markdown"},
            )

        # A new tab in the shared context, not a new window/context.
        ctx.new_page.assert_called_once_with()
        browser.new_context.assert_not_called()
        # Per-call request headers are applied to the individual tab.
        page.set_extra_http_headers.assert_called_once_with({"Accept": "text/markdown"})
        assert managed.page is page
        assert managed._owned_context is None

    def test_cdp_without_headers_skips_set_headers(self):
        from gptme.tools import _browser_playwright as bp

        thread = MagicMock()
        thread.cdp_url = "http://127.0.0.1:9222"
        ctx = thread._session_context = MagicMock()
        page = ctx.new_page.return_value

        with patch.object(bp, "_browser", thread):
            bp._create_page(MagicMock(), locale="en-US")

        page.set_extra_http_headers.assert_not_called()

    def test_launched_creates_owned_context_with_kwargs(self):
        from gptme.tools import _browser_playwright as bp

        thread = MagicMock()
        thread.cdp_url = None  # launched mode
        browser = MagicMock()
        context = browser.new_context.return_value
        page = context.new_page.return_value

        with patch.object(bp, "_browser", thread):
            managed = bp._create_page(browser, locale="en-US")

        browser.new_context.assert_called_once_with(locale="en-US")
        assert managed.page is page
        assert managed._owned_context is context


# ── open_page session reuse (issue #4720) ─────────────────────────────


@pytest.mark.skipif(not has_playwright(), reason="playwright not installed")
class TestOpenPageSessionReuse:
    """Ordinary open_page() navigation must keep the live interactive context.

    Explicit close_page() / load_browser_state() still reset it. See gptme#4720.
    """

    @pytest.fixture(autouse=True)
    def _reset_interactive_session(self):
        from gptme.tools import _browser_playwright as bp

        bp._current_page = None
        bp._current_context = None
        bp._session_owner = None
        yield
        bp._current_page = None
        bp._current_context = None
        bp._session_owner = None

    def test_second_open_navigates_existing_page_without_closing_context(self):
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com/account"
        ctx = MagicMock()
        bp._current_page = page
        bp._current_context = ctx

        with (
            patch.object(bp, "_browser", None),
            patch.object(bp, "_create_page") as create_page,
            patch.object(bp, "_page_snapshot", return_value="snapshot"),
            patch.object(bp, "_close_current_page") as close,
        ):
            result = bp._open_page(MagicMock(), "https://example.com/account")

        assert result == "snapshot"
        page.goto.assert_called_once_with(
            "https://example.com/account",
            wait_until="commit",
            timeout=bp.INTERACTIVE_NAVIGATION_TIMEOUT_MS,
        )
        create_page.assert_not_called()
        ctx.close.assert_not_called()
        ctx.new_page.assert_not_called()
        close.assert_not_called()
        assert bp._current_page is page
        assert bp._current_context is ctx

    def test_first_open_creates_owned_context(self):
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com"
        ctx = MagicMock()
        managed = MagicMock(page=page, _owned_context=ctx)

        with (
            patch.object(bp, "_browser", None),
            patch.object(bp, "_create_page", return_value=managed) as create_page,
            patch.object(bp, "get_context_options", return_value={"locale": "en-US"}),
            patch.object(bp, "_page_snapshot", return_value="snapshot"),
        ):
            result = bp._open_page(MagicMock(), "https://example.com")

        assert result == "snapshot"
        create_page.assert_called_once()
        page.goto.assert_called_once_with(
            "https://example.com",
            wait_until="commit",
            timeout=bp.INTERACTIVE_NAVIGATION_TIMEOUT_MS,
        )
        assert bp._current_page is page
        assert bp._current_context is ctx

    def test_dead_page_opens_new_tab_in_existing_context(self):
        from gptme.tools import _browser_playwright as bp

        dead_page = MagicMock()
        dead_page.is_closed.return_value = True
        ctx = MagicMock()
        new_page = MagicMock()
        new_page.is_closed.return_value = False
        new_page.url = "https://example.com/account"
        ctx.new_page.return_value = new_page
        bp._current_page = dead_page
        bp._current_context = ctx

        with (
            patch.object(bp, "_browser", None),
            patch.object(bp, "_create_page") as create_page,
            patch.object(bp, "_page_snapshot", return_value="snapshot"),
            patch.object(bp, "_close_current_page") as close,
        ):
            result = bp._open_page(MagicMock(), "https://example.com/account")

        assert result == "snapshot"
        create_page.assert_not_called()
        close.assert_not_called()
        ctx.close.assert_not_called()
        ctx.new_page.assert_called_once_with()
        new_page.set_extra_http_headers.assert_called_once_with(bp._OPEN_PAGE_HEADERS)
        new_page.goto.assert_called_once_with(
            "https://example.com/account",
            wait_until="commit",
            timeout=bp.INTERACTIVE_NAVIGATION_TIMEOUT_MS,
        )
        dead_page.close.assert_called()
        assert bp._current_page is new_page
        assert bp._current_context is ctx

    def test_cdp_second_open_navigates_tab_without_owning_context(self):
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com/account"
        thread = MagicMock()
        thread.cdp_url = "http://127.0.0.1:9222"
        session_ctx = MagicMock()
        thread._session_context = session_ctx
        bp._current_page = page
        bp._current_context = None

        with (
            patch.object(bp, "_browser", thread),
            patch.object(bp, "_create_page") as create_page,
            patch.object(bp, "_page_snapshot", return_value="snapshot"),
            patch.object(bp, "_close_current_page") as close,
        ):
            result = bp._open_page(MagicMock(), "https://example.com/account")

        assert result == "snapshot"
        page.goto.assert_called_once_with(
            "https://example.com/account",
            wait_until="commit",
            timeout=bp.INTERACTIVE_NAVIGATION_TIMEOUT_MS,
        )
        create_page.assert_not_called()
        close.assert_not_called()
        session_ctx.close.assert_not_called()
        session_ctx.new_page.assert_not_called()
        assert bp._current_context is None

    def test_allowlist_failure_still_closes_owned_context(self):
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://evil.example"
        ctx = MagicMock()
        bp._current_page = page
        bp._current_context = ctx

        with (
            patch.object(bp, "_browser", None),
            patch.object(bp, "_create_page") as create_page,
            patch.object(
                bp, "_validate_page_url", side_effect=ValueError("blocked host")
            ),
            patch.object(bp, "_close_current_page") as close,
            pytest.raises(ValueError, match="blocked host"),
        ):
            bp._open_page(MagicMock(), "https://evil.example")

        create_page.assert_not_called()
        close.assert_called_once_with()

    def test_load_timeout_after_commit_keeps_session_and_reads_page(self):
        """A load event held past the budget must not log the user out."""
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com/account"
        ctx = MagicMock()
        bp._current_page = page
        bp._current_context = ctx

        # goto's commit wait succeeds even for a reload of the same URL;
        # only the subsequent load wait times out.
        page.wait_for_load_state.side_effect = bp.PlaywrightTimeoutError(
            "Timeout waiting for load"
        )

        with (
            patch.object(bp, "_browser", None),
            patch.object(bp, "_page_snapshot", return_value="snapshot") as snapshot,
            patch.object(bp, "_close_current_page") as close,
        ):
            result = bp._open_page(MagicMock(), "https://example.com/account")

        assert result == "snapshot"
        snapshot.assert_called_once_with(timeout_ms=bp.TIMED_OUT_SNAPSHOT_TIMEOUT_MS)
        page.goto.assert_called_once_with(
            "https://example.com/account",
            wait_until="commit",
            timeout=bp.INTERACTIVE_NAVIGATION_TIMEOUT_MS,
        )
        page.wait_for_load_state.assert_called_once()
        assert page.wait_for_load_state.call_args.args == ("load",)
        assert 0 < page.wait_for_load_state.call_args.kwargs["timeout"] <= 16_000
        close.assert_not_called()
        ctx.close.assert_not_called()
        assert bp._current_page is page
        assert bp._current_context is ctx

    @pytest.mark.parametrize(
        ("commit_finished", "remaining_ms"), [(110.0, 6000.0), (116.5, 1.0)]
    )
    def test_commit_and_load_share_navigation_budget(
        self, commit_finished, remaining_ms
    ):
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.url = "https://example.com/account"
        bp._current_page = page
        with (
            # Patch the module's local alias, not the stdlib module: replacing
            # time.monotonic globally would affect every thread in the process.
            patch.object(bp, "_monotonic", side_effect=[100.0, commit_finished]),
            patch.object(bp, "_page_snapshot", return_value="snapshot"),
        ):
            assert bp._goto_and_snapshot(page.url) == "snapshot"

        page.goto.assert_called_once_with(
            page.url, wait_until="commit", timeout=bp.INTERACTIVE_NAVIGATION_TIMEOUT_MS
        )
        page.wait_for_load_state.assert_called_once_with("load", timeout=remaining_ms)

    def test_load_timeout_before_commit_drops_tab_and_keeps_session(self):
        """A stalled commit must not leave an ambiguous tab live for later actions.

        Playwright stops waiting, but the browser may still commit the
        navigation later. The tab is closed so a following click/fill cannot hit
        a half-navigated page, while the context (and its login) survives.
        """
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com/account"

        def stalled_commit(*args, **kwargs):
            # A same-document URL update in the old page must not count either.
            page.url = "https://example.com/account#updated"
            # Playwright emits framenavigated even for replaceState/hash changes.
            # Deliver it if the implementation ever listens for that event, so a
            # regression that treats it as a commit fails this test.
            for call in page.on.call_args_list:
                if call.args[0] == "framenavigated":
                    call.args[1](page.main_frame)
            raise bp.PlaywrightTimeoutError("Timeout 16000ms exceeded")

        page.goto.side_effect = stalled_commit
        ctx = MagicMock()
        bp._current_page = page
        bp._current_context = ctx

        with (
            patch.object(bp, "_browser", None),
            patch.object(bp, "_page_snapshot") as snapshot,
            patch.object(bp, "_close_current_page") as close,
            pytest.raises(RuntimeError, match="Timed out navigating"),
        ):
            bp._open_page(MagicMock(), "https://example.com/unreachable")

        snapshot.assert_not_called()
        page.wait_for_load_state.assert_not_called()
        page.close.assert_called_once_with()
        assert bp._current_page is None
        # The session context is kept, so the login survives.
        close.assert_not_called()
        assert bp._current_context is ctx
        ctx.close.assert_not_called()

    def test_open_page_after_pre_commit_timeout_reuses_session_context(self):
        """The dropped tab's context is reused, so cookies/localStorage survive."""
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com/account"
        page.goto.side_effect = bp.PlaywrightTimeoutError("Timeout 16000ms exceeded")
        ctx = MagicMock()
        new_page = MagicMock()
        new_page.is_closed.return_value = False
        new_page.url = "https://example.com/login"
        ctx.new_page.return_value = new_page
        bp._current_page = page
        bp._current_context = ctx

        with (
            patch.object(bp, "_browser", None),
            patch.object(bp, "_page_snapshot", return_value="snapshot"),
            patch.object(bp, "_close_current_page") as close,
            pytest.raises(RuntimeError, match="Timed out navigating"),
        ):
            bp._open_page(MagicMock(), "https://example.com/unreachable")

        with (
            patch.object(bp, "_browser", None),
            patch.object(bp, "_page_snapshot", return_value="snapshot"),
            patch.object(bp, "_close_current_page") as close,
        ):
            assert bp._open_page(MagicMock(), "https://example.com/login") == "snapshot"

        ctx.new_page.assert_called_once_with()
        new_page.goto.assert_called_once_with(
            "https://example.com/login",
            wait_until="commit",
            timeout=bp.INTERACTIVE_NAVIGATION_TIMEOUT_MS,
        )
        close.assert_not_called()
        ctx.close.assert_not_called()
        assert bp._current_page is new_page
        assert bp._current_context is ctx

    def test_close_page_resets_owned_context_when_no_tab_remains(self):
        """close_page() must still reset the session after a dropped tab."""
        from gptme.tools import _browser_playwright as bp

        ctx = MagicMock()
        bp._current_page = None
        bp._current_context = ctx
        bp._session_owner = bp._conversation_key()

        with (
            patch.object(bp, "_browser", None),
            patch.object(
                bp,
                "_execute_with_retry",
                side_effect=lambda func, *args, **kwargs: func(
                    MagicMock(), *args, **kwargs
                ),
            ),
        ):
            assert bp.close_page() == "Page closed."

        ctx.close.assert_called_once_with()
        assert bp._current_context is None

    def test_close_page_keeps_friendly_message_for_another_owner(self):
        """A dropped tab must not turn close_page() into an ownership error."""
        from gptme.tools import _browser_playwright as bp

        ctx = MagicMock()
        bp._current_page = None
        bp._current_context = ctx
        bp._session_owner = "conv-a"

        with (
            patch.object(bp, "_browser", None),
            patch.object(bp, "_conversation_key", return_value="conv-b"),
        ):
            assert bp.close_page() == "No page is currently open."

        ctx.close.assert_not_called()
        assert bp._current_context is ctx

    def test_snapshot_failure_after_commit_timeout_names_the_committed_url(self):
        """A snapshot failure must not read like a failed navigation."""
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com/account"
        page.wait_for_load_state.side_effect = bp.PlaywrightTimeoutError(
            "Timeout waiting for load"
        )
        ctx = MagicMock()
        bp._current_page = page
        bp._current_context = ctx

        with (
            patch.object(bp, "_browser", None),
            patch.object(
                bp,
                "_page_snapshot",
                side_effect=bp.PlaywrightTimeoutError("aria_snapshot timeout"),
            ),
            patch.object(bp, "_close_current_page") as close,
            pytest.raises(RuntimeError, match="snapshot failed"),
        ):
            bp._open_page(MagicMock(), "https://example.com/account")

        close.assert_not_called()
        assert bp._current_context is ctx

    def test_allowlist_failure_in_timeout_snapshot_still_propagates(self):
        """Allowlist enforcement keeps raising ValueError through the fallback."""
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com/account"
        page.wait_for_load_state.side_effect = bp.PlaywrightTimeoutError(
            "Timeout waiting for load"
        )
        bp._current_page = page
        bp._current_context = MagicMock()

        with (
            patch.object(bp, "_browser", None),
            patch.object(bp, "_page_snapshot", side_effect=ValueError("blocked host")),
            pytest.raises(ValueError, match="blocked host"),
        ):
            bp._open_page(MagicMock(), "https://example.com/account")

    def test_load_timeout_on_blocked_host_closes_session_before_reading(self):
        """The timeout fallback still enforces the allowlist on the live URL."""
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com/account"
        bp._current_page = page
        bp._current_context = MagicMock()

        def redirect(url, **kwargs):
            page.url = "https://evil.example/landing"

        page.goto.side_effect = redirect
        page.wait_for_load_state.side_effect = bp.PlaywrightTimeoutError(
            "Timeout waiting for load"
        )

        def block_evil(url):
            if "evil.example" in url:
                raise ValueError("blocked host")

        with (
            patch.object(bp, "_browser", None),
            patch.object(bp, "_validate_page_url", side_effect=block_evil),
            patch.object(bp, "_close_current_page") as close,
            pytest.raises(ValueError, match="blocked host"),
        ):
            bp._open_page(MagicMock(), "https://example.com/next")

        close.assert_called_once_with()
        page.locator.assert_not_called()

    def test_non_timeout_navigation_error_still_closes_session(self):
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com/account"
        page.goto.side_effect = Exception("net::ERR_NAME_NOT_RESOLVED")
        bp._current_page = page
        bp._current_context = MagicMock()

        with (
            patch.object(bp, "_browser", None),
            patch.object(bp, "_close_current_page") as close,
            pytest.raises(RuntimeError, match="Failed to navigate"),
        ):
            bp._open_page(MagicMock(), "https://example.com/missing")

        close.assert_called_once_with()

    def test_close_current_page_closes_owned_context(self):
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        ctx = MagicMock()
        bp._current_page = page
        bp._current_context = ctx
        bp._session_owner = "conv-a"

        bp._close_current_page()

        page.close.assert_called_once_with()
        ctx.close.assert_called_once_with()
        assert bp._current_page is None
        assert bp._current_context is None
        assert bp._session_owner is None

    def test_same_conversation_reuses_owned_context(self):
        from gptme.hooks import current_conversation_id
        from gptme.tools import _browser_playwright as bp

        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com/account"
        ctx = MagicMock()
        bp._current_page = page
        bp._current_context = ctx
        bp._session_owner = "conv-a"
        token = current_conversation_id.set("conv-a")
        try:
            with (
                patch.object(bp, "_browser", None),
                patch.object(bp, "_create_page") as create_page,
                patch.object(bp, "_page_snapshot", return_value="snapshot"),
                patch.object(bp, "_close_current_page") as close,
            ):
                result = bp._open_page(MagicMock(), "https://example.com/account")
        finally:
            current_conversation_id.reset(token)

        assert result == "snapshot"
        page.goto.assert_called_once_with(
            "https://example.com/account",
            wait_until="commit",
            timeout=bp.INTERACTIVE_NAVIGATION_TIMEOUT_MS,
        )
        create_page.assert_not_called()
        close.assert_not_called()
        assert bp._session_owner == "conv-a"

    @pytest.mark.parametrize("new_owner", ["conv-b", None])
    def test_other_conversation_replaces_cdp_session_context(self, new_owner):
        from gptme.hooks import current_conversation_id
        from gptme.tools import _browser_playwright as bp

        old_page = MagicMock()
        old_ctx = MagicMock()
        old_ctx.new_page.return_value.url = "https://example.com/account"
        new_page = MagicMock()
        new_page.url = "https://example.com/account"
        new_ctx = MagicMock()
        new_ctx.new_page.return_value = new_page
        browser = MagicMock()
        browser.new_context.return_value = new_ctx
        thread = MagicMock()
        thread.cdp_url = "http://127.0.0.1:9222"
        thread._session_context = old_ctx
        bp._current_page = old_page
        bp._session_owner = "conv-a"
        token = current_conversation_id.set(new_owner)
        try:
            with (
                patch.object(bp, "_browser", thread),
                patch.object(bp, "get_context_options", return_value={}),
                patch.object(bp, "_page_snapshot", return_value="snapshot"),
            ):
                result = bp._open_page(browser, "https://example.com/account")
        finally:
            current_conversation_id.reset(token)

        assert result == "snapshot"
        old_page.close.assert_called_once_with()
        old_ctx.close.assert_called_once_with()
        old_ctx.new_page.assert_not_called()
        browser.new_context.assert_called_once_with()
        assert thread._session_context is new_ctx
        assert bp._current_page is new_page
        assert bp._current_context is None  # BrowserThread retains CDP ownership
        assert bp._session_owner == new_owner

    def test_other_conversation_gets_fresh_context(self):
        from gptme.hooks import current_conversation_id
        from gptme.tools import _browser_playwright as bp

        old_page = MagicMock()
        old_page.is_closed.return_value = False
        old_ctx = MagicMock()
        new_page = MagicMock()
        new_page.is_closed.return_value = False
        new_page.url = "https://example.com/account"
        new_ctx = MagicMock()
        managed = MagicMock(page=new_page, _owned_context=new_ctx)
        bp._current_page = old_page
        bp._current_context = old_ctx
        bp._session_owner = "conv-a"
        token = current_conversation_id.set("conv-b")
        try:
            with (
                patch.object(bp, "_browser", None),
                patch.object(bp, "_create_page", return_value=managed) as create_page,
                patch.object(bp, "get_context_options", return_value={}),
                patch.object(bp, "_page_snapshot", return_value="snapshot"),
            ):
                result = bp._open_page(MagicMock(), "https://example.com/account")
        finally:
            current_conversation_id.reset(token)

        assert result == "snapshot"
        create_page.assert_called_once()
        old_page.close.assert_called()
        old_ctx.close.assert_called()
        new_page.goto.assert_called_once_with(
            "https://example.com/account",
            wait_until="commit",
            timeout=bp.INTERACTIVE_NAVIGATION_TIMEOUT_MS,
        )
        assert bp._current_page is new_page
        assert bp._current_context is new_ctx
        assert bp._session_owner == "conv-b"


# ── _select_option — no label= fallback (issue #3100) ────────────────


@pytest.mark.skipif(not has_playwright(), reason="playwright not installed")
class TestSelectOption:
    """Regression tests for _select_option — label= fallback was unreachable
    and doubled the timeout on a miss (gptme#3100)."""

    def test_select_option_value_calls_select_once(self):
        """select_option(value=) is called exactly once — no label= retry."""
        from unittest.mock import MagicMock, patch

        from gptme.tools import _browser_playwright as bp

        mock_page = MagicMock()
        mock_locator = MagicMock()
        mock_page.locator.return_value = mock_locator

        with (
            patch.object(bp, "_current_page", mock_page),
            patch.object(bp, "_page_snapshot", return_value="snapshot"),
        ):
            result = bp._select_option(MagicMock(), "[name='s']", "Large")

        assert result == "snapshot"
        mock_locator.select_option.assert_called_once_with(value="Large", timeout=10000)

    def test_select_option_timeout_propagates_after_one_call(self):
        """On timeout, the error surfaces immediately — no second call with label=."""
        from unittest.mock import MagicMock, patch

        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        from gptme.tools import _browser_playwright as bp

        mock_page = MagicMock()
        mock_locator = MagicMock()
        mock_locator.select_option.side_effect = PlaywrightTimeoutError("timed out")
        mock_page.locator.return_value = mock_locator

        with (
            patch.object(bp, "_current_page", mock_page),
            pytest.raises(PlaywrightTimeoutError),
        ):
            bp._select_option(MagicMock(), "[name='s']", "missing-value")

        # Old code would call twice (value= then label=); fix makes it call once.
        mock_locator.select_option.assert_called_once()


@pytest.mark.skipif(not has_playwright(), reason="playwright not installed")
@pytest.mark.parametrize(
    ("operation", "args"),
    [
        ("_snapshot_current_page", ()),
        ("_read_page_text", ()),
        ("_click", ("button",)),
        ("_fill", ("input", "value")),
        ("_scroll", ("down", 100)),
        ("_press_key", ("Enter",)),
        ("_select_option", ("select", "value")),
        ("_wait_for_element", ("button", 100)),
        ("_hover", ("button",)),
        ("_get_current_url", ()),
        ("_do_save_browser_state", ("state.json",)),
        ("_do_close_page", ()),
    ],
)
def test_interactive_operations_reject_another_session(operation, args, tmp_path):
    from gptme.hooks import current_conversation_id
    from gptme.tools import _browser_playwright as bp

    page = MagicMock()
    context = MagicMock()
    token = current_conversation_id.set("conv-b")
    if operation == "_do_save_browser_state":
        args = (str(tmp_path / "state.json"),)
    try:
        with (
            patch.object(bp, "_current_page", page),
            patch.object(bp, "_current_context", context),
            patch.object(bp, "_session_owner", "conv-a"),
            pytest.raises(RuntimeError, match="another conversation"),
        ):
            getattr(bp, operation)(MagicMock(), *args)
    finally:
        current_conversation_id.reset(token)
    assert page.mock_calls == []
    assert context.mock_calls == []
    assert not (tmp_path / "state.json").exists()


# ── read_logs isolation ───────────────────────────────────────────────


@pytest.mark.skipif(not has_playwright(), reason="playwright not installed")
class TestPlaywrightReadLogsIsolation:
    """read_logs must not leak a blocked redirect's or another conversation's logs."""

    @staticmethod
    def _fake_browser(final_url: str, console_text: str) -> MagicMock:
        page = MagicMock()
        page.url = final_url
        handlers: dict = {}
        page.on.side_effect = lambda event, fn: handlers.__setitem__(event, fn)

        def goto(url):
            msg = MagicMock()
            msg.type = "log"
            msg.text = console_text
            msg.location = None
            handlers["console"](msg)
            resp = MagicMock()
            resp.headers = {"content-type": "text/html"}
            return resp

        page.goto.side_effect = goto
        context = MagicMock()
        context.new_page.return_value = page
        browser = MagicMock()
        browser.new_context.return_value = context
        return browser

    def test_blocked_redirect_logs_are_not_stored(self, monkeypatch):
        import gptme.tools._browser_playwright as pw

        monkeypatch.setattr(pw, "_last_logs", {})

        def validate(url: str) -> None:
            if "internal" in url:
                raise ValueError("blocked host")

        monkeypatch.setattr(pw, "_validate_page_url", validate)
        browser = self._fake_browser("http://internal.example/admin", "SECRET-TOKEN")
        with pytest.raises(ValueError, match="blocked host"):
            pw._load_page(browser, "https://allowed.example/")
        assert "SECRET-TOKEN" not in pw.read_logs()
        # Nothing from the blocked page is stored at all, not even its URL.
        assert pw._last_logs.get(pw._conversation_key()) is None

    def test_blocked_redirect_drops_the_previous_pages_logs(self, monkeypatch):
        import gptme.tools._browser_playwright as pw
        from gptme.hooks import current_conversation_id

        monkeypatch.setattr(pw, "_last_logs", {})

        def validate(url: str) -> None:
            if "internal" in url:
                raise ValueError("blocked host")

        monkeypatch.setattr(pw, "_validate_page_url", validate)
        monkeypatch.setattr(pw, "_extract_main_content", lambda page: "")
        token = current_conversation_id.set("conv-a")
        try:
            pw._load_page(
                self._fake_browser("https://allowed.example/", "FIRST-PAGE-LOG"),
                "https://allowed.example/",
            )
            assert "FIRST-PAGE-LOG" in pw.read_logs()

            # The caller reads logs to debug a load that was blocked; it must
            # not get the previous page's logs back.
            with pytest.raises(ValueError, match="blocked host"):
                pw._load_page(
                    self._fake_browser("http://internal.example/admin", "BLOCKED"),
                    "http://internal.example/admin",
                )
            assert "FIRST-PAGE-LOG" not in pw.read_logs()
            assert "BLOCKED" not in pw.read_logs()
            assert pw.read_logs() == "No URL has been read yet."
        finally:
            current_conversation_id.reset(token)

    def test_logs_are_scoped_to_the_reading_conversation(self, monkeypatch):
        import gptme.tools._browser_playwright as pw
        from gptme.hooks import current_conversation_id

        monkeypatch.setattr(pw, "_last_logs", {})
        monkeypatch.setattr(pw, "_validate_page_url", lambda url: None)
        monkeypatch.setattr(pw, "_extract_main_content", lambda page: "")
        browser = self._fake_browser("https://allowed.example/", "PRIVATE-LOG")
        token = current_conversation_id.set("conv-a")
        try:
            pw._load_page(browser, "https://allowed.example/")
            assert "PRIVATE-LOG" in pw.read_logs()
        finally:
            current_conversation_id.reset(token)
        token = current_conversation_id.set("conv-b")
        try:
            assert "PRIVATE-LOG" not in pw.read_logs()
        finally:
            current_conversation_id.reset(token)
        # conv-b reading its own page must not evict conv-a's logs.
        token = current_conversation_id.set("conv-b")
        try:
            pw._load_page(
                self._fake_browser("https://allowed.example/b", "CONV-B-LOG"),
                "https://allowed.example/b",
            )
            assert "CONV-B-LOG" in pw.read_logs()
        finally:
            current_conversation_id.reset(token)
        token = current_conversation_id.set("conv-a")
        try:
            assert "PRIVATE-LOG" in pw.read_logs()
        finally:
            current_conversation_id.reset(token)
