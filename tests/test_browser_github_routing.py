import json
import subprocess
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock

import pytest

from gptme.tools.browser import _read_github_repo

if TYPE_CHECKING:
    from playwright.sync_api import Page
else:
    Page = Any


def test_read_github_repo_uses_shared_browser_fallback(monkeypatch):
    url = "https://github.com/gptme/gptme"
    fallback = Mock(return_value="fallback content")

    monkeypatch.setattr("gptme.tools.browser._read_url_with_browser", fallback)
    monkeypatch.setattr(
        "gptme.tools.browser.subprocess.run",
        Mock(side_effect=FileNotFoundError),
    )

    result = _read_github_repo(url)

    assert result == "fallback content"
    fallback.assert_called_once_with(url)


def test_extract_main_content_passes_noise_selector_as_argument():
    pytest.importorskip("playwright")
    from gptme.tools._browser_playwright import _extract_main_content

    class FakeElement:
        def inner_text(self):
            return "body text"

        def inner_html(self):
            return "<body>body text</body>"

    class FakePage:
        def __init__(self):
            self.evaluate_calls = []

        def query_selector(self, selector):
            if selector == "body":
                return FakeElement()
            return None

        def wait_for_timeout(self, timeout_ms):
            assert timeout_ms == 1000

        def evaluate(self, expression, arg=None):
            self.evaluate_calls.append((expression, arg))

        def inner_html(self, selector):
            assert selector == "body"
            return "<body>body text</body>"

    fake_page = FakePage()
    page = cast(Page, fake_page)

    result = _extract_main_content(page)

    assert result == "<body>body text</body>"
    assert any(arg == "[aria-hidden='true']" for _, arg in fake_page.evaluate_calls)
    assert all(
        "querySelectorAll(selector)" in expression
        for expression, _ in fake_page.evaluate_calls
    )
    assert all(
        "[aria-hidden='true']" not in expression
        for expression, _ in fake_page.evaluate_calls
    )


def test_read_github_repo_keeps_metadata_when_readme_parse_fails(monkeypatch):
    url = "https://github.com/gptme/gptme"
    fallback = Mock(return_value="fallback content")

    repo_payload = {
        "name": "gptme",
        "description": "Terminal AI assistant",
        "url": url,
        "stargazerCount": 123,
        "forkCount": 45,
        "licenseInfo": {"name": "MIT"},
        "repositoryTopics": [{"name": "agents"}],
        "homepageUrl": "https://gptme.org",
        "defaultBranchRef": {"name": "master"},
    }

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(repo_payload), stderr=""
            )
        if cmd[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr("gptme.tools.browser._read_url_with_browser", fallback)
    monkeypatch.setattr("gptme.tools.browser.subprocess.run", fake_run)

    result = _read_github_repo(url)

    assert result.startswith("<!-- Source: gh repo view (GitHub CLI) -->")
    assert "# gptme" in result
    assert "**Default branch**: master" in result
    assert "## README" not in result
    fallback.assert_not_called()
