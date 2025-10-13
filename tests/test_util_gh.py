"""Tests for GitHub utility functions."""

import json
from unittest.mock import Mock, patch


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


@patch("gptme.util.gh.subprocess.run")
@patch("gptme.util.gh.shutil.which")
def test_get_github_pr_content_with_code_context(mock_which, mock_run):
    """Test that PR content includes code context from diff_hunk."""
    mock_which.return_value = "/usr/bin/gh"

    # Mock PR basic info
    pr_result = Mock()
    pr_result.returncode = 0
    pr_result.stdout = "title: Test PR\nstate: OPEN\n"

    # Mock PR comments
    comments_result = Mock()
    comments_result.returncode = 0
    comments_result.stdout = "Some comments"

    # Mock PR details
    pr_details = Mock()
    pr_details.returncode = 0
    pr_details.stdout = json.dumps({"head": {"sha": "abc123"}})

    # Mock review comments with diff_hunk
    review_comments = [
        {
            "id": 1,
            "user": {"login": "reviewer"},
            "body": "Please fix this",
            "path": "test.py",
            "line": 10,
            "diff_hunk": "@@ -8,7 +8,7 @@\n def example():\n-    old_code\n+    new_code\n     context_line",
        }
    ]
    review_comments_result = Mock()
    review_comments_result.returncode = 0
    review_comments_result.stdout = json.dumps(review_comments)

    # Mock GraphQL response (no resolved threads)
    graphql_result = Mock()
    graphql_result.returncode = 0
    graphql_result.stdout = json.dumps(
        {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}
    )

    # Mock check runs (no actions)
    check_runs_result = Mock()
    check_runs_result.returncode = 1
    check_runs_result.stdout = ""

    mock_run.side_effect = [
        pr_result,
        comments_result,
        pr_details,
        review_comments_result,
        graphql_result,
        check_runs_result,
    ]

    content = get_github_pr_content("https://github.com/owner/repo/pull/1")

    assert content is not None
    assert "**@reviewer** on test.py:10:" in content
    assert "Please fix this" in content
    # Check that code context is included
    assert "Referenced code in test.py:10:" in content
    assert "Context:" in content
    assert "```py" in content
    assert "def example():" in content
    assert "old_code" in content or "new_code" in content


@patch("gptme.util.gh.subprocess.run")
@patch("gptme.util.gh.shutil.which")
def test_get_github_pr_content_filters_resolved_comments(mock_which, mock_run):
    """Test that resolved review comments are filtered out."""
    mock_which.return_value = "/usr/bin/gh"

    pr_result = Mock()
    pr_result.returncode = 0
    pr_result.stdout = "title: Test PR\n"

    comments_result = Mock()
    comments_result.returncode = 0
    comments_result.stdout = ""

    pr_details = Mock()
    pr_details.returncode = 0
    pr_details.stdout = json.dumps({"head": {"sha": "abc123"}})

    # Two review comments, one will be marked resolved
    review_comments = [
        {
            "id": 1,
            "user": {"login": "reviewer"},
            "body": "Resolved comment",
            "path": "test.py",
            "line": 10,
            "diff_hunk": "@@ -8,7 +8,7 @@\n code",
        },
        {
            "id": 2,
            "user": {"login": "reviewer"},
            "body": "Unresolved comment",
            "path": "test.py",
            "line": 20,
            "diff_hunk": "@@ -18,7 +18,7 @@\n code",
        },
    ]
    review_comments_result = Mock()
    review_comments_result.returncode = 0
    review_comments_result.stdout = json.dumps(review_comments)

    # Mark first comment as resolved
    graphql_result = Mock()
    graphql_result.returncode = 0
    graphql_result.stdout = json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": True,
                                    "comments": {"nodes": [{"databaseId": 1}]},
                                }
                            ]
                        }
                    }
                }
            }
        }
    )

    check_runs_result = Mock()
    check_runs_result.returncode = 1

    mock_run.side_effect = [
        pr_result,
        comments_result,
        pr_details,
        review_comments_result,
        graphql_result,
        check_runs_result,
    ]

    content = get_github_pr_content("https://github.com/owner/repo/pull/1")

    assert content is not None
    # Resolved comment should not be in output
    assert "Resolved comment" not in content
    # Unresolved comment should be in output
    assert "Unresolved comment" in content
    assert "test.py:20" in content


@patch("gptme.util.gh.subprocess.run")
@patch("gptme.util.gh.shutil.which")
def test_get_github_pr_content_no_diff_hunk(mock_which, mock_run):
    """Test handling review comments without diff_hunk."""
    mock_which.return_value = "/usr/bin/gh"

    pr_result = Mock()
    pr_result.returncode = 0
    pr_result.stdout = "title: Test PR\n"

    comments_result = Mock()
    comments_result.returncode = 0
    comments_result.stdout = ""

    pr_details = Mock()
    pr_details.returncode = 0
    pr_details.stdout = json.dumps({"head": {"sha": "abc123"}})

    # Review comment without diff_hunk
    review_comments = [
        {
            "id": 1,
            "user": {"login": "reviewer"},
            "body": "Comment without context",
            "path": "test.py",
            "line": 10,
        }
    ]
    review_comments_result = Mock()
    review_comments_result.returncode = 0
    review_comments_result.stdout = json.dumps(review_comments)

    graphql_result = Mock()
    graphql_result.returncode = 0
    graphql_result.stdout = json.dumps(
        {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}
    )

    check_runs_result = Mock()
    check_runs_result.returncode = 1

    mock_run.side_effect = [
        pr_result,
        comments_result,
        pr_details,
        review_comments_result,
        graphql_result,
        check_runs_result,
    ]

    content = get_github_pr_content("https://github.com/owner/repo/pull/1")

    assert content is not None
    assert "Comment without context" in content
    # Should not have code context section
    assert "Referenced code" not in content
