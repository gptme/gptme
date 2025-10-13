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
