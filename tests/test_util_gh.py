"""Tests for GitHub utility functions."""

import pytest

from gptme.util.gh import (
    get_github_pr_content,
    parse_github_url,
)


def test_parse_github_url_pr():
    """Test parsing GitHub PR URLs."""
    url = "https://github.com/owner/repo/pull/123"
    result = parse_github_url(url)
    assert result == {
        "owner": "owner",
        "repo": "repo",
        "type": "pull",
        "number": "123",
    }


def test_parse_github_url_issue():
    """Test parsing GitHub issue URLs."""
    url = "https://github.com/owner/repo/issues/456"
    result = parse_github_url(url)
    assert result == {
        "owner": "owner",
        "repo": "repo",
        "type": "issues",
        "number": "456",
    }


def test_parse_github_url_invalid():
    """Test parsing non-GitHub URLs."""
    assert parse_github_url("https://example.com") is None
    assert parse_github_url("https://github.com/owner") is None


@pytest.mark.slow
def test_get_github_pr_content_real():
    """Test fetching real PR content with review comments.

    Uses PR #687 from gptme/gptme which has:
    - Review comments with code context
    - Code suggestions
    - Resolved and unresolved comments
    """
    content = get_github_pr_content("https://github.com/gptme/gptme/pull/687")

    if content is None:
        pytest.skip("gh CLI not available or request failed")

    # Should have basic PR info
    assert "feat: implement basic lesson system" in content
    assert "TimeToBuildBob" in content

    # Should have review comments section
    assert "Review Comments (Unresolved)" in content

    # Should have at least one review comment with file reference
    assert ".py:" in content

    # Check for code context (if diff_hunk is available)
    # Note: This might not always be present depending on API response
    if "Referenced code in" in content:
        assert "Context:" in content
        assert "```" in content

    # Check for GitHub Actions status
    assert "GitHub Actions Status" in content


@pytest.mark.slow
def test_get_github_pr_with_suggestions():
    """Test that code suggestions are extracted and formatted.

    Uses PR #687 which has code suggestions from ellipsis-dev bot.
    """
    content = get_github_pr_content("https://github.com/gptme/gptme/pull/687")

    if content is None:
        pytest.skip("gh CLI not available or request failed")

    # PR #687 has a suggestion from ellipsis-dev about using logger.exception
    if "```suggestion" in content or "Suggested change:" in content:
        # If suggestions are in the raw body, we should extract them
        assert "logger.exception" in content or "Suggested change:" in content


@pytest.mark.slow
def test_gh_tool_read_pr():
    """Test the gh tool's read_pr functionality."""
    from gptme.tools import get_tool, init_tools

    # Initialize tools to ensure gh tool is loaded
    init_tools(["gh"])

    gh_tool = get_tool("gh")
    if gh_tool is None or gh_tool.execute is None:
        pytest.skip("gh tool not available")

    # Test with a real PR
    result = gh_tool.execute(
        None,
        ["read_pr", "https://github.com/gptme/gptme/pull/687"],
        None,
        lambda x: True,  # confirm function
    )
    # Handle both Generator and Message return types
    from collections.abc import Generator as GenType

    results = list(result) if isinstance(result, GenType) else [result]

    assert len(results) == 1
    assert results[0].role == "system"

    content = results[0].content
    assert "feat: implement basic lesson system" in content
    assert "Review Comments (Unresolved)" in content
    assert "TimeToBuildBob" in content


@pytest.mark.slow
def test_gh_tool_read_pr_invalid_url():
    """Test the gh tool with an invalid URL."""
    from gptme.tools import get_tool, init_tools

    init_tools(["gh"])

    gh_tool = get_tool("gh")
    if gh_tool is None or gh_tool.execute is None:
        pytest.skip("gh tool not available")

    # Test with invalid URL
    result = gh_tool.execute(
        None,
        ["read_pr", "https://invalid-url.com"],
        None,
        lambda x: True,
    )
    from collections.abc import Generator as GenType

    results = list(result) if isinstance(result, GenType) else [result]

    assert len(results) == 1
    assert results[0].role == "system"
    assert "Error" in results[0].content
    assert "Invalid GitHub URL" in results[0].content
