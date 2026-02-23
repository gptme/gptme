"""Comprehensive mocked tests for GitHub utility functions.

These tests mock subprocess calls to ensure consistent test coverage
of the code context and suggestion extraction functionality.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gptme.util.gh import get_github_pr_content


@pytest.fixture
def mock_pr_basic_response():
    """Basic PR response without review comments."""
    return {
        "pr_view": "Test PR #123\nOpen\n@testuser\n\nTest PR body",
        "pr_comments": "",
        "pr_details": {
            "number": 123,
            "title": "Test PR",
            "state": "open",
        },
        "review_comments": [],
        "graphql_threads": {
            "data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}
        },
    }


@pytest.fixture
def mock_pr_with_context_response():
    """PR response with review comment containing diff_hunk."""
    return {
        "pr_view": "Test PR with context #123\nOpen\n@testuser\n\nTest PR body",
        "pr_comments": "",
        "pr_details": {
            "number": 123,
            "title": "Test PR with context",
        },
        "review_comments": [
            {
                "id": 1001,
                "user": {"login": "reviewer"},
                "body": "Please fix this",
                "path": "test.py",
                "line": 10,
                "diff_hunk": "@@ -8,6 +8,7 @@ def test():\n     return True\n+    # new line\n     pass",
            }
        ],
        "graphql_threads": {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": False,
                                    "comments": {"nodes": [{"databaseId": 1001}]},
                                }
                            ]
                        }
                    }
                }
            }
        },
    }


@pytest.fixture
def mock_pr_with_suggestion_response():
    """PR response with review comment containing code suggestion."""
    return {
        "pr_view": "Test PR with suggestion #123\nOpen\n@testuser\n\nTest PR body",
        "pr_comments": "",
        "pr_details": {
            "number": 123,
            "title": "Test PR with suggestion",
        },
        "review_comments": [
            {
                "id": 1002,
                "user": {"login": "reviewer"},
                "body": "Consider this change:\n```suggestion\ndef improved_function():\n    return 42\n```",
                "path": "test.py",
                "line": 15,
                "diff_hunk": "",
            }
        ],
        "graphql_threads": {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": False,
                                    "comments": {"nodes": [{"databaseId": 1002}]},
                                }
                            ]
                        }
                    }
                }
            }
        },
    }


@pytest.fixture
def mock_pr_with_both_response():
    """PR response with both code context and suggestions."""
    return {
        "pr_view": "Test PR with context and suggestion #123\nOpen\n@testuser\n\nTest PR body",
        "pr_comments": "",
        "pr_details": {
            "number": 123,
            "title": "Test PR with context and suggestion",
        },
        "review_comments": [
            {
                "id": 1003,
                "user": {"login": "reviewer"},
                "body": "Fix this code:\n```suggestion\nreturn True\n```",
                "path": "module.py",
                "line": 20,
                "diff_hunk": "@@ -18,4 +18,5 @@ class Test:\n def method(self):\n-    return False\n+    return True",
            }
        ],
        "graphql_threads": {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": False,
                                    "comments": {"nodes": [{"databaseId": 1003}]},
                                }
                            ]
                        }
                    }
                }
            }
        },
    }


@pytest.fixture
def mock_pr_multiple_suggestions():
    """PR response with multiple code suggestions in one comment."""
    return {
        "pr_view": "Test PR with multiple suggestions #123\nOpen\n@testuser\n\nTest PR body",
        "pr_comments": "",
        "pr_details": {
            "number": 123,
            "title": "Test PR with multiple suggestions",
        },
        "review_comments": [
            {
                "id": 1004,
                "user": {"login": "reviewer"},
                "body": "Two suggestions:\n```suggestion\nfirst_change()\n```\nAnd:\n```suggestion\nsecond_change()\n```",
                "path": "test.py",
                "line": 25,
                "diff_hunk": "",
            }
        ],
        "graphql_threads": {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": False,
                                    "comments": {"nodes": [{"databaseId": 1004}]},
                                }
                            ]
                        }
                    }
                }
            }
        },
    }


def mock_subprocess_run(mock_responses):
    """Create a mock subprocess.run that returns appropriate responses based on command.

    Args:
        mock_responses: Dict with keys: pr_view, pr_comments, pr_details, review_comments, graphql_threads
    """

    def _mock_run(cmd, *args, **kwargs):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        # Determine which command is being run and return appropriate response
        if "graphql" in cmd:
            # GraphQL query for review threads
            mock_result.stdout = json.dumps(mock_responses["graphql_threads"])
        elif "/comments" in " ".join(cmd):
            # Review comments API call - returns list of comments
            mock_result.stdout = json.dumps(mock_responses["review_comments"])
        elif "api" in cmd and "/pulls/" in " ".join(cmd):
            # PR details API call
            mock_result.stdout = json.dumps(mock_responses["pr_details"])
        elif "--comments" in cmd:
            # PR view with comments
            mock_result.stdout = mock_responses["pr_comments"]
        else:
            # Basic PR view
            mock_result.stdout = mock_responses["pr_view"]

        return mock_result

    return _mock_run


def test_code_context_extraction(mock_pr_with_context_response):
    """Test that diff_hunk is extracted and formatted correctly."""
    with patch(
        "subprocess.run", side_effect=mock_subprocess_run(mock_pr_with_context_response)
    ):
        content = get_github_pr_content("https://github.com/owner/repo/pull/123")

    assert content is not None

    # Should have review comment
    assert "**@reviewer** on test.py:10 (ID: 1001):" in content
    assert "Please fix this" in content

    # Should have code context section
    assert "Referenced code in test.py:10:" in content
    assert "Context:" in content
    assert "```py" in content

    # Should have formatted diff_hunk (without @@ lines and +/- markers)
    assert "return True" in content
    assert "# new line" in content
    assert "pass" in content

    # Verify the context block doesn't have leading +/- after formatting
    context_start = content.find("Context:")
    if context_start != -1:
        context_end = content.find("```", context_start + 20)
        if context_end != -1:
            context_block = content[context_start:context_end]
            # The content should not have @@ marker lines in the formatted output
            lines = [
                line.strip() for line in context_block.split("\n")[2:] if line.strip()
            ]  # Skip "Context:" and code fence
            for line in lines:
                # Check that code lines don't start with diff markers
                if line and not line.startswith("```"):
                    assert not line.startswith("@@"), (
                        f"Line should not have @@ marker: {line}"
                    )


def test_code_suggestion_extraction(mock_pr_with_suggestion_response):
    """Test that code suggestions are extracted and formatted correctly."""
    with patch(
        "subprocess.run",
        side_effect=mock_subprocess_run(mock_pr_with_suggestion_response),
    ):
        content = get_github_pr_content("https://github.com/owner/repo/pull/123")

    assert content is not None

    # Should have review comment
    assert "**@reviewer** on test.py:15 (ID: 1002):" in content
    assert "Consider this change:" in content

    # Should have extracted suggestion
    assert "Suggested change:" in content
    assert "```py" in content
    assert "def improved_function():" in content
    assert "return 42" in content


def test_both_context_and_suggestion(mock_pr_with_both_response):
    """Test PR with both code context and suggestion in same comment."""
    with patch(
        "subprocess.run", side_effect=mock_subprocess_run(mock_pr_with_both_response)
    ):
        content = get_github_pr_content("https://github.com/owner/repo/pull/123")

    assert content is not None

    # Should have review comment
    assert "**@reviewer** on module.py:20 (ID: 1003):" in content

    # Should have code context
    assert "Referenced code in module.py:20:" in content
    assert "Context:" in content

    # Should have suggestion
    assert "Suggested change:" in content
    assert "return True" in content


def test_multiple_suggestions(mock_pr_multiple_suggestions):
    """Test comment with multiple code suggestions."""
    with patch(
        "subprocess.run", side_effect=mock_subprocess_run(mock_pr_multiple_suggestions)
    ):
        content = get_github_pr_content("https://github.com/owner/repo/pull/123")

    assert content is not None

    # Should have review comment
    assert "**@reviewer** on test.py:25 (ID: 1004):" in content

    # Should have both suggestions
    assert content.count("Suggested change:") == 2
    assert "first_change()" in content
    assert "second_change()" in content


def test_no_context_or_suggestion(mock_pr_basic_response):
    """Test PR without code context or suggestions."""
    # Add a review comment without diff_hunk or suggestions
    mock_pr_basic_response["review_comments"] = [
        {
            "id": 1005,
            "user": {"login": "reviewer"},
            "body": "Looks good!",
            "path": "test.py",
            "line": 5,
            "diff_hunk": "",
        }
    ]
    mock_pr_basic_response["graphql_threads"] = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "isResolved": False,
                                "comments": {"nodes": [{"databaseId": 1005}]},
                            }
                        ]
                    }
                }
            }
        }
    }

    with patch(
        "subprocess.run", side_effect=mock_subprocess_run(mock_pr_basic_response)
    ):
        content = get_github_pr_content("https://github.com/owner/repo/pull/123")

    assert content is not None

    # Should have review comment
    assert "**@reviewer** on test.py:5 (ID: 1005):" in content
    assert "Looks good!" in content

    # Should NOT have context or suggestion sections
    assert "Referenced code" not in content
    assert "Suggested change:" not in content


def test_empty_diff_hunk():
    """Test review comment with empty diff_hunk string."""
    mock_response = {
        "pr_view": "Test PR #123\nOpen\n@testuser\n\nTest",
        "pr_comments": "",
        "pr_details": {
            "number": 123,
            "title": "Test PR",
        },
        "review_comments": [
            {
                "id": 1006,
                "user": {"login": "reviewer"},
                "body": "Comment",
                "path": "test.py",
                "line": 10,
                "diff_hunk": "",  # Empty string
            }
        ],
        "graphql_threads": {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": False,
                                    "comments": {"nodes": [{"databaseId": 1006}]},
                                }
                            ]
                        }
                    }
                }
            }
        },
    }

    with patch("subprocess.run", side_effect=mock_subprocess_run(mock_response)):
        content = get_github_pr_content("https://github.com/owner/repo/pull/123")

    assert content is not None
    assert "Comment" in content
    # Empty diff_hunk should not trigger context display
    assert "Referenced code" not in content


def test_file_extension_extraction():
    """Test that file extensions are correctly extracted for syntax highlighting."""
    test_cases = [
        ("test.py", "py"),
        ("module.ts", "ts"),
        ("script.js", "js"),
        ("style.css", "css"),
        ("config.json", "json"),
        ("readme.md", "md"),
        ("file", "file"),  # No extension
    ]

    for path, expected_ext in test_cases:
        mock_response = {
            "pr_view": "Test PR #123\nOpen\n@testuser\n\nTest",
            "pr_comments": "",
            "pr_details": {
                "number": 123,
                "title": "Test PR",
            },
            "review_comments": [
                {
                    "id": 1007,
                    "user": {"login": "reviewer"},
                    "body": "```suggestion\ntest\n```",
                    "path": path,
                    "line": 10,
                    "diff_hunk": "@@ test @@\n test line",
                }
            ],
            "graphql_threads": {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "isResolved": False,
                                        "comments": {"nodes": [{"databaseId": 1007}]},
                                    }
                                ]
                            }
                        }
                    }
                }
            },
        }

        with patch("subprocess.run", side_effect=mock_subprocess_run(mock_response)):
            content = get_github_pr_content("https://github.com/owner/repo/pull/123")

        assert content is not None
        # Both context and suggestion should use the file extension
        assert f"```{expected_ext}" in content


def test_malformed_suggestion_block():
    """Test handling of malformed suggestion blocks."""
    mock_response = {
        "pr_view": "Test PR #123\nOpen\n@testuser\n\nTest",
        "pr_comments": "",
        "pr_details": {
            "number": 123,
            "title": "Test PR",
        },
        "review_comments": [
            {
                "id": 1008,
                "user": {"login": "reviewer"},
                "body": "```suggestion\nunclosed block",  # Missing closing ```
                "path": "test.py",
                "line": 10,
                "diff_hunk": "",
            }
        ],
        "graphql_threads": {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": False,
                                    "comments": {"nodes": [{"databaseId": 1008}]},
                                }
                            ]
                        }
                    }
                }
            }
        },
    }

    with patch("subprocess.run", side_effect=mock_subprocess_run(mock_response)):
        content = get_github_pr_content("https://github.com/owner/repo/pull/123")

    assert content is not None
    # Should handle gracefully - unclosed blocks shouldn't cause extraction
    # The raw body will still contain the suggestion marker
    assert "```suggestion" in content or "unclosed block" in content


# Tests for the truncation helper function
from gptme.util.gh import _truncate_body


class TestTruncateBody:
    """Tests for comment body truncation."""

    def test_short_body_unchanged(self):
        """Short bodies should pass through unchanged."""
        body = "This is a short comment."
        result = _truncate_body(body)
        assert result == body

    def test_empty_body(self):
        """Empty body should return empty."""
        assert _truncate_body("") == ""

    def test_long_body_truncated(self):
        """Long bodies should be truncated from middle."""
        # Create a body that exceeds 1000 tokens (4000 chars)
        body = "A" * 5000  # 5000 chars = ~1250 tokens

        result = _truncate_body(body)

        # Should be truncated
        assert len(result) < len(body)
        # Should have truncation indicator
        assert "[... truncated" in result
        assert "chars" in result
        # Should preserve beginning and end
        assert result.startswith("A" * 100)
        assert result.endswith("A" * 100)

    def test_custom_max_tokens(self):
        """Custom max_tokens should be respected."""
        body = "B" * 2000  # 2000 chars = ~500 tokens

        # With default (1000 tokens), this should NOT be truncated
        result_default = _truncate_body(body)
        assert result_default == body

        # With 200 tokens (800 chars), this SHOULD be truncated
        result_custom = _truncate_body(body, max_tokens=200)
        assert "[... truncated" in result_custom
        assert len(result_custom) < len(body)

    def test_truncation_preserves_structure(self):
        """Truncation should preserve beginning and end content."""
        # Create a body with identifiable start and end
        body = "START_MARKER" + ("X" * 5000) + "END_MARKER"

        result = _truncate_body(body)

        # Beginning should be preserved
        assert "START_MARKER" in result
        # End should be preserved
        assert "END_MARKER" in result
        # Middle X's should be truncated
        assert "[... truncated" in result

    def test_truncated_output_within_limit(self):
        """Verify truncated output stays within max_chars limit."""
        # Test various body sizes
        test_cases = [
            ("A" * 5000, 1000),  # default 1000 tokens = 4000 chars
            ("B" * 10000, 500),  # 500 tokens = 2000 chars
            ("C" * 3000, 200),  # 200 tokens = 800 chars
        ]

        for body, max_tokens in test_cases:
            result = _truncate_body(body, max_tokens=max_tokens)
            max_chars = max_tokens * 4

            # Output should not exceed max_chars
            assert len(result) <= max_chars, (
                f"Truncated output ({len(result)} chars) exceeds "
                f"max_chars ({max_chars}) for max_tokens={max_tokens}"
            )
            # Should still have truncation indicator
            assert "[... truncated" in result

    def test_edge_case_small_overage(self):
        """Edge case: body just slightly over limit shouldn't grow."""
        max_tokens = 100  # 400 chars
        # Body is just 10 chars over limit
        body = "X" * 410

        result = _truncate_body(body, max_tokens=max_tokens)

        # Result should be truncated AND shorter than original
        assert len(result) <= len(body), "Truncation made output longer!"
        # Should also stay within the limit
        assert len(result) <= 400, f"Result ({len(result)}) exceeds 400 chars"


def test_truncation_in_pr_content():
    """Test that truncation is applied to review comments in PR content."""
    # Create a mock response with a very long review comment
    long_body = "Long comment: " + ("X" * 5000)  # Exceeds 1000 tokens

    mock_response = {
        "pr_view": "Test PR #123\nOpen\n@testuser\n\nTest",
        "pr_comments": "",
        "pr_details": {"number": 123, "title": "Test PR"},
        "review_comments": [
            {
                "id": 1009,
                "user": {"login": "verbose-bot"},
                "body": long_body,
                "path": "test.py",
                "line": 10,
                "diff_hunk": "",
            }
        ],
        "graphql_threads": {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": False,
                                    "comments": {"nodes": [{"databaseId": 1009}]},
                                }
                            ]
                        }
                    }
                }
            }
        },
    }

    with patch("subprocess.run", side_effect=mock_subprocess_run(mock_response)):
        content = get_github_pr_content("https://github.com/owner/repo/pull/123")

    assert content is not None
    # Should have truncation indicator
    assert "[... truncated" in content
    # Should still have the comment metadata
    assert "@verbose-bot" in content
    # Full body should not be present
    assert long_body not in content


def test_suggestion_preserved_from_truncated_comment(monkeypatch):
    """Test that code suggestions are extracted from original body before truncation.

    When a long comment containing a suggestion is truncated, the suggestion
    should still be extracted from the original body, not lost in the truncation.
    """
    from gptme.util import gh

    # Create a long comment with a suggestion in the middle (where truncation would remove it)
    padding = "x" * 3000  # Enough to trigger truncation (>4000 chars)
    suggestion_body = f"""Some initial analysis text.

{padding}

Here's a suggested fix:
```suggestion
fixed_code = True
```

{padding}

More analysis at the end."""

    mock_review_comments = [
        {
            "user": {"login": "reviewer"},
            "body": suggestion_body,
            "path": "test.py",
            "id": 12345,
            "line": 10,
        }
    ]

    def mock_run(cmd, **kwargs):
        class MockResult:
            returncode = 0
            stdout = ""

        if "api" in cmd and "/pulls/" in str(cmd) and "/comments" in str(cmd):
            MockResult.stdout = json.dumps(mock_review_comments)
        elif "pr" in cmd and "view" in cmd and "--json" in cmd:
            MockResult.stdout = json.dumps({"head": {"sha": "abc123"}})
        elif "pr" in cmd and "view" in cmd:
            MockResult.stdout = "PR Title\nPR Body"
        elif "api" in cmd and "graphql" in cmd:
            MockResult.stdout = json.dumps(
                {
                    "data": {
                        "repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}
                    }
                }
            )
        elif "api" in cmd and "check-runs" in cmd:
            MockResult.stdout = json.dumps({"check_runs": []})

        return MockResult()

    import json

    monkeypatch.setattr("subprocess.run", mock_run)

    result = gh.get_github_pr_content("https://github.com/test/repo/pull/1")
    assert result is not None

    # Verify the body was truncated (indicator present)
    assert "[... truncated" in result

    # Most importantly: verify the suggestion was STILL extracted
    # even though it was in the middle of the truncated content
    assert "Suggested change:" in result
    assert "fixed_code = True" in result
