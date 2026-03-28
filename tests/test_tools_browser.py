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
    _read_github_repo,
    _search_failed,
    _search_with_engine,
    has_lynx,
    has_playwright,
    read_url,
    search,
    tool,
)

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
            mock_resp.headers = {"Content-Type": "text/html"}
            mock_req.head.return_value = mock_resp
            assert _is_pdf_url("https://example.com/page.html") is False

    def test_no_extension_pdf_content_type(self):
        with patch("gptme.tools.browser.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "application/pdf"}
            mock_req.head.return_value = mock_resp
            assert _is_pdf_url("https://example.com/document") is True

    def test_no_extension_html_content_type(self):
        with patch("gptme.tools.browser.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
            mock_req.head.return_value = mock_resp
            assert _is_pdf_url("https://example.com/page") is False

    def test_request_failure_non_pdf(self):
        """When HEAD request fails and URL doesn't end in .pdf, returns False."""
        with patch("gptme.tools.browser.requests") as mock_req:
            mock_req.head.side_effect = Exception("connection failed")
            mock_req.RequestException = Exception
            assert _is_pdf_url("https://example.com/page") is False

    def test_pdf_extension_with_query_params(self):
        # .pdf?v=2 doesn't end with .pdf, so it falls through to HEAD check — mock it
        with patch("gptme.tools.browser.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "application/pdf"}
            mock_req.head.return_value = mock_resp
            mock_req.RequestException = Exception
            assert _is_pdf_url("https://example.com/doc.pdf?v=2") is True
        with patch("gptme.tools.browser.requests") as mock_req:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "text/html"}
            mock_req.head.return_value = mock_resp
            mock_req.RequestException = Exception
            assert _is_pdf_url("https://example.com/doc.pdf?v=2") is False


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
    def test_download_failure(self, mock_requests):
        from gptme.tools.browser import _read_pdf_url

        mock_requests.get.side_effect = Exception("connection error")
        result = _read_pdf_url("https://example.com/doc.pdf")
        assert "Error" in result

    @patch("gptme.tools.browser.has_pypdf", True)
    @patch("gptme.tools.browser.requests")
    def test_successful_pdf_read(self, mock_requests):
        """Test PDF reading with mocked pypdf."""
        from gptme.tools.browser import _read_pdf_url

        # Create a minimal mock for pypdf
        mock_response = MagicMock()
        mock_response.content = b"fake pdf content"
        mock_requests.get.return_value = mock_response

        with patch("gptme.tools.browser.pypdf") as mock_pypdf:
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
        mock_response.content = b"fake"
        mock_requests.get.return_value = mock_response

        with patch("gptme.tools.browser.pypdf") as mock_pypdf:
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
        mock_response.content = b"fake"
        mock_requests.get.return_value = mock_response

        with patch("gptme.tools.browser.pypdf") as mock_pypdf:
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
        mock_response.content = b"fake"
        mock_requests.get.return_value = mock_response

        with patch("gptme.tools.browser.pypdf") as mock_pypdf:
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
        mock_response.content = b"fake"
        mock_requests.get.return_value = mock_response

        with patch("gptme.tools.browser.pypdf") as mock_pypdf:
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
        mock_response.content = b"%PDF-fake"
        mock_requests.get.return_value = mock_response
        mock_convert.return_value = []

        pdf_to_images("https://example.com/doc.pdf", output_dir=tmp_path)

        mock_requests.get.assert_called_once()

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
        fn_names = [f.__name__ for f in tool.functions]
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
