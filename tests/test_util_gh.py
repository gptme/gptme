"""Tests for GitHub utility functions."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from gptme.util.gh import (
    _extract_failure_sections,
    _get_github_actions_status,
    _get_repo_from_git_remote,
    comment_on_github,
    create_github_issue,
    get_github_issue_content,
    get_github_issue_list,
    get_github_pr_content,
    get_github_pr_diff,
    get_github_pr_list,
    get_github_run_logs,
    merge_github_pr,
    parse_github_ref,
    parse_github_url,
    search_github_issues,
    search_github_prs,
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


# --- Tests for parse_github_ref ---


def test_parse_github_ref_full_url():
    """Full URLs delegate to parse_github_url."""
    result = parse_github_ref("https://github.com/gptme/gptme/pull/42")
    assert result == {
        "owner": "gptme",
        "repo": "gptme",
        "type": "pull",
        "number": "42",
    }


def test_parse_github_ref_short_ref():
    """owner/repo#N format."""
    result = parse_github_ref("gptme/gptme-contrib#580")
    assert result == {
        "owner": "gptme",
        "repo": "gptme-contrib",
        "type": "issues",
        "number": "580",
    }


def test_parse_github_ref_short_ref_default_type():
    """owner/repo#N respects default_type."""
    result = parse_github_ref("gptme/gptme#100", default_type="pull")
    assert result == {
        "owner": "gptme",
        "repo": "gptme",
        "type": "pull",
        "number": "100",
    }


def _mock_git_remote(url: str):
    """Helper to mock subprocess.run for git remote get-url."""
    import subprocess

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=url + "\n", stderr="")
        raise subprocess.CalledProcessError(1, cmd)

    return fake_run


def test_parse_github_ref_hash_number():
    """#N format with mocked git remote."""
    with patch(
        "gptme.util.gh.subprocess.run",
        side_effect=_mock_git_remote("git@github.com:gptme/gptme.git"),
    ):
        result = parse_github_ref("#42")
    assert result == {
        "owner": "gptme",
        "repo": "gptme",
        "type": "issues",
        "number": "42",
    }


def test_parse_github_ref_bare_number():
    """Bare number format with mocked git remote."""
    with patch(
        "gptme.util.gh.subprocess.run",
        side_effect=_mock_git_remote("https://github.com/owner/repo.git"),
    ):
        result = parse_github_ref("123", default_type="pull")
    assert result == {
        "owner": "owner",
        "repo": "repo",
        "type": "pull",
        "number": "123",
    }


def test_parse_github_ref_bare_number_no_remote():
    """Bare number without git remote returns None."""
    import subprocess

    def fail_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    with patch("gptme.util.gh.subprocess.run", side_effect=fail_run):
        result = parse_github_ref("42")
    assert result is None


def test_parse_github_ref_invalid():
    """Invalid refs return None."""
    assert parse_github_ref("not-a-ref") is None
    assert parse_github_ref("") is None
    assert parse_github_ref("just/words") is None


# --- Tests for _get_repo_from_git_remote ---


def test_get_repo_from_git_remote_ssh():
    """SSH remote URL."""
    with patch(
        "gptme.util.gh.subprocess.run",
        side_effect=_mock_git_remote("git@github.com:gptme/gptme.git"),
    ):
        result = _get_repo_from_git_remote()
    assert result == ("gptme", "gptme")


def test_get_repo_from_git_remote_https():
    """HTTPS remote URL."""
    with patch(
        "gptme.util.gh.subprocess.run",
        side_effect=_mock_git_remote("https://github.com/owner/repo.git"),
    ):
        result = _get_repo_from_git_remote()
    assert result == ("owner", "repo")


def test_get_repo_from_git_remote_https_no_dotgit():
    """HTTPS remote URL without .git suffix."""
    with patch(
        "gptme.util.gh.subprocess.run",
        side_effect=_mock_git_remote("https://github.com/owner/repo"),
    ):
        result = _get_repo_from_git_remote()
    assert result == ("owner", "repo")


def test_get_repo_from_git_remote_no_remote():
    """No remote returns None."""
    import subprocess

    def fail_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    with patch("gptme.util.gh.subprocess.run", side_effect=fail_run):
        result = _get_repo_from_git_remote()
    assert result is None


def test_get_repo_from_git_remote_non_github():
    """Non-GitHub remote returns None."""
    with patch(
        "gptme.util.gh.subprocess.run",
        side_effect=_mock_git_remote("https://gitlab.com/owner/repo.git"),
    ):
        result = _get_repo_from_git_remote()
    assert result is None


@pytest.mark.slow
def test_get_github_pr_content_real():
    """Test fetching real PR content with review comments.

    Uses PR #687 from gptme/gptme which has:
    - Review comments with code context
    - Code suggestions
    - All review threads resolved (GraphQL isResolved=true)

    Note: PR #687's review threads were resolved at some point after it was merged.
    The REST API shows resolved_at=null but GraphQL reports isResolved=true,
    so our function correctly filters them out. We don't assert on "Unresolved"
    section presence since all threads are resolved.
    """
    content = get_github_pr_content("https://github.com/gptme/gptme/pull/687")

    if content is None:
        pytest.skip("gh CLI not available or request failed")
    assert content is not None  # help mypy narrow type after skip

    # Should have basic PR info
    assert "feat: implement basic lesson system" in content
    assert "TimeToBuildBob" in content

    # All review threads on PR #687 are now resolved,
    # so the unresolved section should NOT appear
    assert "Review Comments (Unresolved)" not in content

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
    assert content is not None  # help mypy narrow type after skip

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
    assert gh_tool is not None and gh_tool.execute is not None

    # Test with a real PR
    result = gh_tool.execute(
        None,
        ["pr", "view", "https://github.com/gptme/gptme/pull/687"],
        None,
    )
    # Handle both Generator and Message return types
    from collections.abc import Generator as GenType

    results = list(result) if isinstance(result, GenType) else [result]

    assert len(results) == 1
    assert results[0].role == "system"

    content = results[0].content

    # Skip if gh CLI is not authenticated (common in CI)
    if (
        "Failed to fetch PR content" in content
        or "Make sure 'gh' CLI is installed" in content
    ):
        pytest.skip("gh CLI not authenticated")

    assert "feat: implement basic lesson system" in content
    assert "TimeToBuildBob" in content


@pytest.mark.slow
def test_get_github_pr_content_with_unresolved():
    """Test that unresolved review comments are included.

    Uses PR #271 from gptme/gptme which has 4 unresolved review threads
    from ErikBjare (open since Nov 2024, stable test target).
    """
    content = get_github_pr_content("https://github.com/gptme/gptme/pull/271")

    if content is None:
        pytest.skip("gh CLI not available or request failed")
    assert content is not None  # help mypy narrow type after skip

    # Should have basic PR info
    assert "gptme-util" in content or "prompts" in content

    # PR #271 has unresolved review threads — verify they show up
    assert "Review Comments (Unresolved)" in content
    # Should contain ErikBjare's review comments with file references
    assert "ErikBjare" in content


@pytest.mark.slow
def test_gh_tool_read_pr_invalid_url():
    """Test the gh tool with an invalid URL."""
    from gptme.tools import get_tool, init_tools

    init_tools(["gh"])

    gh_tool = get_tool("gh")
    if gh_tool is None or gh_tool.execute is None:
        pytest.skip("gh tool not available")
    assert gh_tool is not None and gh_tool.execute is not None

    # Test with invalid URL
    result = gh_tool.execute(
        None,
        ["pr", "view", "https://invalid-url.com"],
        None,
    )
    from collections.abc import Generator as GenType

    results = list(result) if isinstance(result, GenType) else [result]

    assert len(results) == 1
    assert results[0].role == "system"
    assert "Error" in results[0].content
    assert "Could not parse GitHub reference" in results[0].content


# --- get_github_issue_list ---


class TestGetGithubIssueList:
    @patch("gptme.util.gh.shutil.which", return_value=None)
    def test_no_gh_returns_none(self, _mock_which):
        assert get_github_issue_list("owner", "repo") is None

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_empty_results(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(stdout="[]", returncode=0)
        result = get_github_issue_list("owner", "repo")
        assert result is not None
        assert "No open issues" in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_issues_returned(self, mock_run, _mock_which):
        issues = [
            {
                "number": 42,
                "title": "Fix the widget",
                "state": "OPEN",
                "labels": [{"name": "bug"}],
                "author": {"login": "alice"},
                "assignees": [{"login": "bob"}],
                "updatedAt": "2026-03-20T10:00:00Z",
            },
            {
                "number": 7,
                "title": "Add feature",
                "state": "OPEN",
                "labels": [],
                "author": {"login": "carol"},
                "assignees": [],
                "updatedAt": "2026-03-19T08:00:00Z",
            },
        ]
        mock_run.return_value = MagicMock(stdout=json.dumps(issues), returncode=0)
        result = get_github_issue_list("owner", "repo")
        assert result is not None
        assert "#42 Fix the widget" in result
        assert "#7 Add feature" in result
        assert "[bug]" in result
        assert "@alice" in result
        assert "@bob" in result
        assert "Showing 2" in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_labels_passed_to_cli(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(stdout="[]", returncode=0)
        get_github_issue_list("owner", "repo", labels=["bug", "enhancement"])
        cmd = mock_run.call_args[0][0]
        assert "--label" in cmd
        # Both labels should be separate --label flags
        label_indices = [i for i, v in enumerate(cmd) if v == "--label"]
        assert len(label_indices) == 2

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_subprocess_failure(self, mock_run, _mock_which):
        import subprocess as sp

        mock_run.side_effect = sp.CalledProcessError(1, "gh")
        result = get_github_issue_list("owner", "repo")
        assert result is None


# --- get_github_pr_list ---


class TestGetGithubPrList:
    @patch("gptme.util.gh.shutil.which", return_value=None)
    def test_no_gh_returns_none(self, _mock_which):
        assert get_github_pr_list("owner", "repo") is None

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_empty_results(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(stdout="[]", returncode=0)
        result = get_github_pr_list("owner", "repo")
        assert result is not None
        assert "No open pull requests" in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_prs_returned(self, mock_run, _mock_which):
        prs = [
            {
                "number": 10,
                "title": "Add new feature",
                "state": "OPEN",
                "author": {"login": "alice"},
                "headRefName": "feat/new-feature",
                "isDraft": False,
                "reviewDecision": "APPROVED",
                "updatedAt": "2026-03-25T12:00:00Z",
            },
            {
                "number": 11,
                "title": "WIP: draft work",
                "state": "OPEN",
                "author": {"login": "bob"},
                "headRefName": "wip/draft",
                "isDraft": True,
                "reviewDecision": "",
                "updatedAt": "2026-03-24T08:00:00Z",
            },
        ]
        mock_run.return_value = MagicMock(stdout=json.dumps(prs), returncode=0)
        result = get_github_pr_list("owner", "repo")
        assert result is not None
        assert "#10 Add new feature" in result
        assert "✅approved" in result
        assert "#11 WIP: draft work" in result
        assert "DRAFT" in result
        assert "Showing 2" in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_state_passed_to_cli(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(stdout="[]", returncode=0)
        get_github_pr_list("owner", "repo", state="merged")
        cmd = mock_run.call_args[0][0]
        state_idx = cmd.index("--state")
        assert cmd[state_idx + 1] == "merged"

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_subprocess_failure(self, mock_run, _mock_which):
        import subprocess as sp

        mock_run.side_effect = sp.CalledProcessError(1, "gh")
        result = get_github_pr_list("owner", "repo")
        assert result is None


# --- _extract_failure_sections ---


class TestExtractFailureSections:
    def test_empty_input(self):
        assert _extract_failure_sections("") == ""

    def test_no_errors_short(self):
        """Short log with no error patterns returns as-is."""
        text = "line 1\nline 2\nline 3"
        assert _extract_failure_sections(text) == text

    def test_no_errors_long_returns_tail(self):
        """Long log with no error patterns returns tail."""
        lines = [f"line {i}" for i in range(200)]
        text = "\n".join(lines)
        result = _extract_failure_sections(text)
        assert "lines omitted" in result
        assert "line 199" in result

    def test_extracts_error_with_context(self):
        """Extracts error line with surrounding context."""
        lines = [
            "setup done",
            "running test 1",
            "running test 2",
            "running test 3",
            "ERROR: test 3 failed",
            "cleaning up",
            "done",
        ]
        text = "\n".join(lines)
        result = _extract_failure_sections(text)
        assert "ERROR: test 3 failed" in result
        assert "running test 1" in result  # context before
        assert "done" in result  # context after

    def test_extracts_traceback(self):
        """Picks up Python traceback patterns."""
        lines = [
            "ok line 1",
            "ok line 2",
            "ok line 3",
            "ok line 4",
            "ok line 5",
            "Traceback (most recent call last):",
            '  File "test.py", line 10',
            "    assert x == 1",
            "AssertionError: 0 != 1",
            "ok line 6",
        ]
        text = "\n".join(lines)
        result = _extract_failure_sections(text)
        assert "Traceback" in result
        assert "AssertionError" in result

    def test_extracts_exit_code(self):
        """Picks up non-zero exit code patterns."""
        text = "running build\ncompiling\nProcess completed with exit code 1\ndone"
        result = _extract_failure_sections(text)
        assert "exit code 1" in result

    def test_multiple_errors_merged(self):
        """Multiple errors are all extracted."""
        lines = ["ok"] * 10
        lines[3] = "ERROR: first failure"
        lines[7] = "FAILED: second failure"
        text = "\n".join(lines)
        result = _extract_failure_sections(text)
        assert "first failure" in result
        assert "second failure" in result

    def test_gap_indicators(self):
        """Gaps between error sections show line count."""
        lines = ["ok"] * 30
        lines[5] = "ERROR: early"
        lines[25] = "ERROR: late"
        text = "\n".join(lines)
        result = _extract_failure_sections(text)
        assert "lines omitted" in result

    def test_leading_gap_indicator(self):
        """Leading omitted lines get an omission marker."""
        lines = ["ok"] * 20
        lines[10] = "ERROR: late"
        text = "\n".join(lines)
        result = _extract_failure_sections(text)
        assert result.startswith("[... 7 lines omitted ...]\n")
        assert "ERROR: late" in result


# --- get_github_run_logs ---


class TestGetGithubRunLogs:
    def _make_run_json(self, conclusion="failure", jobs=None):
        """Create mock run JSON response."""
        data = {
            "databaseId": 12345,
            "displayTitle": "Test PR",
            "event": "pull_request",
            "headBranch": "fix/bug",
            "conclusion": conclusion,
            "status": "completed",
            "workflowName": "CI",
            "createdAt": "2026-03-26T00:00:00Z",
            "updatedAt": "2026-03-26T00:05:00Z",
            "url": "https://github.com/owner/repo/actions/runs/12345",
            "jobs": jobs or [],
        }
        return json.dumps(data)

    def _make_job(self, name, conclusion="success", job_id=100, steps=None):
        return {
            "name": name,
            "conclusion": conclusion,
            "status": "completed",
            "databaseId": job_id,
            "steps": steps or [],
        }

    @patch("gptme.util.gh.shutil.which", return_value=None)
    def test_no_gh_returns_none(self, _mock_which):
        assert get_github_run_logs("12345") is None

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_success_run_no_failures(self, mock_run, _mock_which):
        """Successful run shows all jobs passed."""
        jobs = [self._make_job("lint"), self._make_job("test")]
        mock_run.return_value = MagicMock(
            stdout=self._make_run_json("success", jobs),
            returncode=0,
        )
        result = get_github_run_logs("12345")
        assert result is not None
        assert "Run 12345" in result
        assert "All jobs passed" in result
        assert "✅" in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_failed_run_with_logs(self, mock_run, _mock_which):
        """Failed run extracts failure logs."""
        failed_step = {"name": "Run tests", "conclusion": "failure"}
        jobs = [
            self._make_job("lint"),
            self._make_job("test", "failure", 200, [failed_step]),
        ]
        run_json = self._make_run_json("failure", jobs)
        log_output = "test\tRun tests\tERROR: assertion failed\n"

        mock_run.side_effect = [
            MagicMock(stdout=run_json, returncode=0),  # gh run view --json
            MagicMock(stdout=log_output, returncode=0),  # gh run view --log-failed
        ]

        result = get_github_run_logs("12345")
        assert result is not None
        assert "❌ test: failure" in result
        assert "Failed step: Run tests" in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_multiple_failed_jobs_fetch_logs_once(self, mock_run, _mock_which):
        """Shared failed-job logs are fetched once and reused per job."""
        jobs = [
            self._make_job(
                "lint",
                "failure",
                200,
                [{"name": "ruff", "conclusion": "failure"}],
            ),
            self._make_job(
                "test",
                "failure",
                201,
                [{"name": "pytest", "conclusion": "failure"}],
            ),
        ]
        run_json = self._make_run_json("failure", jobs)
        log_output = (
            "lint\truff\tERROR: lint failed\ntest\tpytest\tERROR: assertion failed\n"
        )

        mock_run.side_effect = [
            MagicMock(stdout=run_json, returncode=0),  # gh run view --json
            MagicMock(stdout=log_output, returncode=0),  # gh run view --log-failed
        ]

        result = get_github_run_logs("12345")

        assert result is not None
        assert "#### lint" in result
        assert "#### test" in result
        assert "lint failed" in result
        assert "assertion failed" in result
        assert mock_run.call_count == 2

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_failed_run_log_fetch_fails(self, mock_run, _mock_which):
        """When log fetch fails, shows fallback message."""
        jobs = [self._make_job("test", "failure", 200)]
        run_json = self._make_run_json("failure", jobs)

        mock_run.side_effect = [
            MagicMock(stdout=run_json, returncode=0),  # gh run view --json
            MagicMock(stdout="", returncode=1),  # gh run view --log-failed
            MagicMock(stdout="", returncode=1),  # gh api fallback
        ]

        result = get_github_run_logs("12345")
        assert result is not None
        assert "Could not fetch logs" in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_no_jobs_available(self, mock_run, _mock_which):
        """Run with empty jobs list."""
        mock_run.return_value = MagicMock(
            stdout=self._make_run_json("failure", []),
            returncode=0,
        )
        result = get_github_run_logs("12345")
        assert result is not None
        assert "No job data" in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_run_header_fields(self, mock_run, _mock_which):
        """Verify header contains expected metadata."""
        jobs = [self._make_job("lint")]
        mock_run.return_value = MagicMock(
            stdout=self._make_run_json("success", jobs),
            returncode=0,
        )
        result = get_github_run_logs("12345")
        assert result is not None
        assert "**Workflow**: CI" in result
        assert "**Branch**: fix/bug" in result
        assert "completed (success)" in result


# --- search_github_issues ---


class TestSearchGithubIssues:
    @patch("gptme.util.gh.shutil.which", return_value=None)
    def test_no_gh_returns_none(self, _mock_which):
        assert search_github_issues("query") is None

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_empty_results(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(stdout="[]", returncode=0)
        result = search_github_issues("nonexistent query")
        assert result is not None
        assert "No issues found" in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_issues_returned(self, mock_run, _mock_which):
        issues = [
            {
                "number": 42,
                "title": "Fix authentication bug",
                "state": "OPEN",
                "repository": {"nameWithOwner": "owner/repo"},
                "author": {"login": "alice"},
                "labels": [{"name": "bug"}],
                "updatedAt": "2026-03-20T10:00:00Z",
                "url": "https://github.com/owner/repo/issues/42",
            },
            {
                "number": 99,
                "title": "Auth token expired",
                "state": "CLOSED",
                "repository": {"nameWithOwner": "org/other"},
                "author": {"login": "bob"},
                "labels": [],
                "updatedAt": "2026-03-18T08:00:00Z",
                "url": "https://github.com/org/other/issues/99",
            },
        ]
        mock_run.return_value = MagicMock(stdout=json.dumps(issues), returncode=0)
        result = search_github_issues("auth")
        assert result is not None
        assert "owner/repo#42 Fix authentication bug" in result
        assert "org/other#99 Auth token expired" in result
        assert "OPEN" in result
        assert "CLOSED" in result
        assert "[bug]" in result
        assert "@alice" in result
        assert "Showing 2" in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_flags_passed_to_cli(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(stdout="[]", returncode=0)
        search_github_issues(
            "query", repo="owner/repo", state="open", author="alice", label="bug"
        )
        cmd = mock_run.call_args[0][0]
        assert "--repo" in cmd
        assert "owner/repo" in cmd
        assert "--state" in cmd
        assert "open" in cmd
        assert "--author" in cmd
        assert "alice" in cmd
        assert "--label" in cmd
        assert "bug" in cmd
        json_idx = cmd.index("--json")
        assert (
            cmd[json_idx + 1] == "number,title,state,repository,author,labels,updatedAt"
        )

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_assignee_flag(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(stdout="[]", returncode=0)
        search_github_issues("query", assignee="bob")
        cmd = mock_run.call_args[0][0]
        assert "--assignee" in cmd
        assert "bob" in cmd

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_subprocess_failure(self, mock_run, _mock_which):
        import subprocess as sp

        mock_run.side_effect = sp.CalledProcessError(1, "gh")
        result = search_github_issues("query")
        assert result is None

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_null_author_and_labels(self, mock_run, _mock_which):
        issues = [
            {
                "number": 7,
                "title": "Deleted user issue",
                "state": "OPEN",
                "repository": {"nameWithOwner": "owner/repo"},
                "author": None,
                "labels": None,
                "updatedAt": "2026-03-26T08:00:00Z",
                "url": "https://github.com/owner/repo/issues/7",
            },
        ]
        mock_run.return_value = MagicMock(stdout=json.dumps(issues), returncode=0)
        result = search_github_issues("deleted")
        assert result is not None
        assert "owner/repo#7 Deleted user issue" in result
        assert "@None" not in result
        assert "[]" not in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_custom_limit(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(stdout="[]", returncode=0)
        search_github_issues("query", limit=5)
        cmd = mock_run.call_args[0][0]
        limit_idx = cmd.index("--limit")
        assert cmd[limit_idx + 1] == "5"


# --- search_github_prs ---


class TestSearchGithubPrs:
    @patch("gptme.util.gh.shutil.which", return_value=None)
    def test_no_gh_returns_none(self, _mock_which):
        assert search_github_prs("query") is None

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_empty_results(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(stdout="[]", returncode=0)
        result = search_github_prs("nonexistent query")
        assert result is not None
        assert "No pull requests found" in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_prs_returned(self, mock_run, _mock_which):
        prs = [
            {
                "number": 123,
                "title": "feat: add search command",
                "state": "OPEN",
                "repository": {"nameWithOwner": "gptme/gptme"},
                "author": {"login": "bob"},
                "labels": [{"name": "enhancement"}],
                "updatedAt": "2026-03-26T12:00:00Z",
                "url": "https://github.com/gptme/gptme/pull/123",
            },
        ]
        mock_run.return_value = MagicMock(stdout=json.dumps(prs), returncode=0)
        result = search_github_prs("search")
        assert result is not None
        assert "gptme/gptme#123 feat: add search command" in result
        assert "OPEN" in result
        assert "[enhancement]" in result
        assert "@bob" in result
        assert "Showing 1" in result

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_flags_passed_to_cli(self, mock_run, _mock_which):
        mock_run.return_value = MagicMock(stdout="[]", returncode=0)
        search_github_prs("query", repo="owner/repo", state="merged", author="alice")
        cmd = mock_run.call_args[0][0]
        assert cmd[1] == "search"
        assert cmd[2] == "prs"
        assert "--repo" in cmd
        assert "--state" in cmd
        assert "merged" in cmd
        assert "--author" in cmd
        json_idx = cmd.index("--json")
        assert (
            cmd[json_idx + 1] == "number,title,state,repository,author,labels,updatedAt"
        )

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_subprocess_failure(self, mock_run, _mock_which):
        import subprocess as sp

        mock_run.side_effect = sp.CalledProcessError(1, "gh")
        result = search_github_prs("query")
        assert result is None

    @patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh")
    @patch("gptme.util.gh.subprocess.run")
    def test_null_author_and_labels(self, mock_run, _mock_which):
        prs = [
            {
                "number": 123,
                "title": "Ghost-authored PR",
                "state": "OPEN",
                "repository": {"nameWithOwner": "gptme/gptme"},
                "author": None,
                "labels": None,
                "updatedAt": "2026-03-26T12:00:00Z",
                "url": "https://github.com/gptme/gptme/pull/123",
            },
        ]
        mock_run.return_value = MagicMock(stdout=json.dumps(prs), returncode=0)
        result = search_github_prs("ghost")
        assert result is not None
        assert "gptme/gptme#123 Ghost-authored PR" in result
        assert "@None" not in result
        assert "[]" not in result


# --- merge_github_pr ---


class TestMergeGithubPr:
    @patch("gptme.util.gh.subprocess.run")
    def test_squash_merge_success(self, mock_run):
        """Default squash merge succeeds and fetches merge SHA."""
        merge_result = MagicMock(
            stdout="✓ Squashed and merged pull request #42", returncode=0
        )
        api_result = MagicMock(stdout="abc1234def5678\n", returncode=0)
        mock_run.side_effect = [merge_result, api_result]

        result = merge_github_pr("owner", "repo", 42)
        assert result["success"] is True
        assert "42" in str(result["message"])
        assert result["sha"] == "abc1234def5678"

        # Verify squash flag was used
        merge_cmd = mock_run.call_args_list[0][0][0]
        assert "--squash" in merge_cmd
        assert "--repo" in merge_cmd
        assert "owner/repo" in merge_cmd

    @patch("gptme.util.gh.subprocess.run")
    def test_rebase_merge(self, mock_run):
        """Rebase merge uses --rebase flag."""
        merge_result = MagicMock(stdout="✓ Rebased and merged", returncode=0)
        api_result = MagicMock(stdout="sha123\n", returncode=0)
        mock_run.side_effect = [merge_result, api_result]

        result = merge_github_pr("owner", "repo", 42, method="rebase")
        assert result["success"] is True
        merge_cmd = mock_run.call_args_list[0][0][0]
        assert "--rebase" in merge_cmd
        assert "--squash" not in merge_cmd

    @patch("gptme.util.gh.subprocess.run")
    def test_merge_method(self, mock_run):
        """Regular merge uses --merge flag."""
        merge_result = MagicMock(stdout="✓ Merged", returncode=0)
        api_result = MagicMock(stdout="sha456\n", returncode=0)
        mock_run.side_effect = [merge_result, api_result]

        result = merge_github_pr("owner", "repo", 42, method="merge")
        assert result["success"] is True
        merge_cmd = mock_run.call_args_list[0][0][0]
        assert "--merge" in merge_cmd

    def test_invalid_method(self):
        """Invalid merge method returns error without calling subprocess."""
        result = merge_github_pr("owner", "repo", 42, method="fast-forward")
        assert result["success"] is False
        assert "Invalid merge method" in str(result["message"])

    @patch("gptme.util.gh.subprocess.run")
    def test_auto_merge(self, mock_run):
        """Auto-merge flag is passed correctly and skips SHA fetch."""
        merge_result = MagicMock(
            stdout="✓ Pull request #42 will be automatically merged",
            returncode=0,
        )
        mock_run.return_value = merge_result

        result = merge_github_pr("owner", "repo", 42, auto=True)
        assert result["success"] is True
        merge_cmd = mock_run.call_args[0][0]
        assert "--auto" in merge_cmd
        # Should not fetch SHA for auto-merge (only 1 subprocess call)
        assert mock_run.call_count == 1

    @patch("gptme.util.gh.subprocess.run")
    def test_delete_branch(self, mock_run):
        """Delete-branch flag is passed correctly."""
        merge_result = MagicMock(stdout="✓ Merged", returncode=0)
        api_result = MagicMock(stdout="sha789\n", returncode=0)
        mock_run.side_effect = [merge_result, api_result]

        merge_github_pr("owner", "repo", 42, delete_branch=True)
        merge_cmd = mock_run.call_args_list[0][0][0]
        assert "--delete-branch" in merge_cmd

    @patch("gptme.util.gh.subprocess.run")
    def test_match_head_commit(self, mock_run):
        """Match-head-commit safety check is passed correctly."""
        merge_result = MagicMock(stdout="✓ Merged", returncode=0)
        api_result = MagicMock(stdout="sha000\n", returncode=0)
        mock_run.side_effect = [merge_result, api_result]

        merge_github_pr("owner", "repo", 42, match_head_commit="abc123")
        merge_cmd = mock_run.call_args_list[0][0][0]
        assert "--match-head-commit" in merge_cmd
        assert "abc123" in merge_cmd

    @patch("gptme.util.gh.subprocess.run")
    def test_merge_conflict_error(self, mock_run):
        """Merge conflict produces helpful error message."""
        import subprocess as sp

        error = sp.CalledProcessError(1, "gh")
        error.stderr = "Pull request is not mergeable: merge conflict"
        mock_run.side_effect = error

        result = merge_github_pr("owner", "repo", 42)
        assert result["success"] is False
        assert "merge conflicts" in str(result["message"]).lower()

    @patch("gptme.util.gh.subprocess.run")
    def test_required_checks_error(self, mock_run):
        """Required status check failure suggests --auto."""
        import subprocess as sp

        error = sp.CalledProcessError(1, "gh")
        error.stderr = "required status check has not passed"
        mock_run.side_effect = error

        result = merge_github_pr("owner", "repo", 42)
        assert result["success"] is False
        assert "--auto" in str(result["message"])

    @patch("gptme.util.gh.subprocess.run")
    def test_head_changed_error(self, mock_run):
        """HEAD mismatch suggests --match-head-commit."""
        import subprocess as sp

        error = sp.CalledProcessError(1, "gh")
        error.stderr = "expected head sha did not match"
        mock_run.side_effect = error

        result = merge_github_pr("owner", "repo", 42)
        assert result["success"] is False
        assert "--match-head-commit" in str(result["message"])

    @patch("gptme.util.gh.subprocess.run")
    def test_draft_pr_error(self, mock_run):
        """Draft PR produces helpful error."""
        import subprocess as sp

        error = sp.CalledProcessError(1, "gh")
        error.stderr = "pull request is in draft state"
        mock_run.side_effect = error

        result = merge_github_pr("owner", "repo", 42)
        assert result["success"] is False
        assert "draft" in str(result["message"]).lower()

    @patch("gptme.util.gh.subprocess.run")
    def test_generic_error(self, mock_run):
        """Unknown errors include stderr in message."""
        import subprocess as sp

        error = sp.CalledProcessError(1, "gh")
        error.stderr = "something unexpected happened"
        mock_run.side_effect = error

        result = merge_github_pr("owner", "repo", 42)
        assert result["success"] is False
        assert "something unexpected" in str(result["message"])

    @patch("gptme.util.gh.subprocess.run")
    def test_sha_fetch_failure_nonfatal(self, mock_run):
        """Merge succeeds even if SHA fetch fails (non-critical)."""
        import subprocess as sp

        merge_result = MagicMock(stdout="✓ Merged", returncode=0)
        mock_run.side_effect = [
            merge_result,
            sp.CalledProcessError(1, "gh"),  # SHA fetch fails
        ]

        result = merge_github_pr("owner", "repo", 42)
        assert result["success"] is True
        assert "sha" not in result

    @patch("gptme.util.gh.subprocess.run")
    def test_null_sha_ignored(self, mock_run):
        """Null SHA from API is not included in result."""
        merge_result = MagicMock(stdout="✓ Merged", returncode=0)
        api_result = MagicMock(stdout="null\n", returncode=0)
        mock_run.side_effect = [merge_result, api_result]

        result = merge_github_pr("owner", "repo", 42)
        assert result["success"] is True
        assert "sha" not in result

    @patch("gptme.util.gh.subprocess.run")
    def test_string_pr_number(self, mock_run):
        """PR number can be passed as string."""
        merge_result = MagicMock(stdout="✓ Merged", returncode=0)
        api_result = MagicMock(stdout="sha123\n", returncode=0)
        mock_run.side_effect = [merge_result, api_result]

        result = merge_github_pr("owner", "repo", "42")
        assert result["success"] is True
        merge_cmd = mock_run.call_args_list[0][0][0]
        assert "42" in merge_cmd

    @patch("gptme.util.gh.subprocess.run")
    def test_url_in_result(self, mock_run):
        """Result includes PR URL."""
        merge_result = MagicMock(stdout="✓ Merged", returncode=0)
        api_result = MagicMock(stdout="sha123\n", returncode=0)
        mock_run.side_effect = [merge_result, api_result]

        result = merge_github_pr("owner", "repo", 42)
        assert result["url"] == "https://github.com/owner/repo/pull/42"


# --- create_github_issue tests ---


class TestCreateGitHubIssue:
    """Tests for create_github_issue."""

    @patch("gptme.util.gh.subprocess.run")
    def test_success_minimal(self, mock_run):
        """Successful issue creation with minimal args."""
        mock_run.return_value = MagicMock(
            stdout="https://github.com/owner/repo/issues/42\n",
            returncode=0,
        )
        result = create_github_issue("owner", "repo", "Bug report")
        assert result["success"] is True
        assert result["number"] == 42
        assert "issues/42" in str(result["url"])
        cmd = mock_run.call_args[0][0]
        assert "issue" in cmd
        assert "create" in cmd
        assert "--title" in cmd
        assert "Bug report" in cmd

    @patch("gptme.util.gh.subprocess.run")
    def test_success_with_body_labels_assignees(self, mock_run):
        """All optional fields passed to gh CLI."""
        mock_run.return_value = MagicMock(
            stdout="https://github.com/o/r/issues/1\n", returncode=0
        )
        result = create_github_issue(
            "o", "r", "Title", body="Body", labels=["bug", "p1"], assignees=["alice"]
        )
        assert result["success"] is True
        cmd = mock_run.call_args[0][0]
        assert "--body" in cmd
        idx = cmd.index("--body")
        assert cmd[idx + 1] == "Body"
        assert "--label" in cmd
        idx = cmd.index("--label")
        assert cmd[idx + 1] == "bug,p1"
        assert "--assignee" in cmd
        idx = cmd.index("--assignee")
        assert cmd[idx + 1] == "alice"

    @patch("gptme.util.gh.subprocess.run")
    def test_no_body(self, mock_run):
        """Empty body still passes --body '' to avoid non-TTY interactive prompt."""
        mock_run.return_value = MagicMock(
            stdout="https://github.com/o/r/issues/1\n", returncode=0
        )
        create_github_issue("o", "r", "Title", body="")
        cmd = mock_run.call_args[0][0]
        assert "--body" in cmd
        assert cmd[cmd.index("--body") + 1] == ""

    @patch("gptme.util.gh.subprocess.run")
    def test_failure(self, mock_run):
        """CalledProcessError returns failure dict."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "gh", stderr="Not Found"
        )
        result = create_github_issue("o", "r", "Title")
        assert result["success"] is False
        assert "Not Found" in str(result["message"])

    @patch("gptme.util.gh.subprocess.run")
    def test_non_numeric_url(self, mock_run):
        """Handles unexpected URL format gracefully."""
        mock_run.return_value = MagicMock(stdout="unexpected output\n", returncode=0)
        result = create_github_issue("o", "r", "Title")
        assert result["success"] is True
        assert result["number"] == 0  # Can't parse number


# --- comment_on_github tests ---


class TestCommentOnGitHub:
    """Tests for comment_on_github."""

    @patch("gptme.util.gh.subprocess.run")
    def test_issue_comment_success(self, mock_run):
        """Successful issue comment."""
        mock_run.return_value = MagicMock(
            stdout="https://github.com/o/r/issues/42#issuecomment-123\n",
            returncode=0,
        )
        result = comment_on_github("o", "r", 42, "Hello", kind="issue")
        assert result["success"] is True
        assert "issue #42" in str(result["message"])
        cmd = mock_run.call_args[0][0]
        assert cmd[1] == "issue"
        assert cmd[2] == "comment"
        assert cmd[3] == "42"

    @patch("gptme.util.gh.subprocess.run")
    def test_pr_comment_success(self, mock_run):
        """Successful PR comment."""
        mock_run.return_value = MagicMock(
            stdout="https://github.com/o/r/pull/10#issuecomment-456\n",
            returncode=0,
        )
        result = comment_on_github("o", "r", 10, "LGTM", kind="pr")
        assert result["success"] is True
        cmd = mock_run.call_args[0][0]
        assert cmd[1] == "pr"

    @patch("gptme.util.gh.subprocess.run")
    def test_comment_body_passed(self, mock_run):
        """Comment body is passed correctly."""
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        comment_on_github("o", "r", 1, "Multi\nline\nbody", kind="issue")
        cmd = mock_run.call_args[0][0]
        idx = cmd.index("--body")
        assert cmd[idx + 1] == "Multi\nline\nbody"

    @patch("gptme.util.gh.subprocess.run")
    def test_comment_failure(self, mock_run):
        """CalledProcessError returns failure dict."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "gh", stderr="Forbidden"
        )
        result = comment_on_github("o", "r", 42, "text", kind="issue")
        assert result["success"] is False
        assert "Forbidden" in str(result["message"])

    @patch("gptme.util.gh.subprocess.run")
    def test_repo_flag(self, mock_run):
        """--repo flag is constructed correctly."""
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        comment_on_github("owner", "repo", 5, "Hi", kind="pr")
        cmd = mock_run.call_args[0][0]
        idx = cmd.index("--repo")
        assert cmd[idx + 1] == "owner/repo"


# ── Timeout handling ──────────────────────────────────────────────────


class TestSubprocessTimeouts:
    """Verify all subprocess calls pass a timeout and handle TimeoutExpired."""

    @patch("gptme.util.gh.subprocess.run")
    def test_get_github_issue_list_timeout(self, mock_run):
        """TimeoutExpired on issue list returns None gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = get_github_issue_list("owner", "repo")
        assert result is None

    @patch("gptme.util.gh.subprocess.run")
    def test_get_github_pr_list_timeout(self, mock_run):
        """TimeoutExpired on PR list returns None gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = get_github_pr_list("owner", "repo")
        assert result is None

    @patch("gptme.util.gh.subprocess.run")
    def test_get_github_pr_content_timeout(self, mock_run):
        """TimeoutExpired on PR content returns None gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = get_github_pr_content("https://github.com/owner/repo/pull/1")
        assert result is None

    @patch("gptme.util.gh.subprocess.run")
    def test_get_github_run_logs_timeout(self, mock_run):
        """TimeoutExpired on run logs returns None gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = get_github_run_logs("12345")
        assert result is None

    @patch("gptme.util.gh.subprocess.run")
    def test_search_github_issues_timeout(self, mock_run):
        """TimeoutExpired on issue search returns None gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = search_github_issues("test query")
        assert result is None

    @patch("gptme.util.gh.subprocess.run")
    def test_search_github_prs_timeout(self, mock_run):
        """TimeoutExpired on PR search returns None gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = search_github_prs("test query")
        assert result is None

    @patch("gptme.util.gh.subprocess.run")
    def test_create_github_issue_timeout(self, mock_run):
        """TimeoutExpired on issue creation returns failure dict."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = create_github_issue("owner", "repo", "title", "body")
        assert result["success"] is False

    @patch("gptme.util.gh.subprocess.run")
    def test_comment_on_github_timeout(self, mock_run):
        """TimeoutExpired on commenting returns failure dict."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = comment_on_github("owner", "repo", 1, "body")
        assert result["success"] is False

    @patch("gptme.util.gh.subprocess.run")
    def test_merge_github_pr_timeout(self, mock_run):
        """TimeoutExpired on merge returns failure dict."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = merge_github_pr("owner", "repo", 1)
        assert result["success"] is False

    @patch("gptme.util.gh.subprocess.run")
    def test_get_repo_from_git_remote_timeout(self, mock_run):
        """TimeoutExpired on git remote returns None gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)
        result = _get_repo_from_git_remote()
        assert result is None

    @patch("gptme.util.gh.subprocess.run")
    def test_get_github_pr_diff_timeout(self, mock_run):
        """TimeoutExpired on PR diff returns None gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=60)
        result = get_github_pr_diff("owner", "repo", "1")
        assert result is None

    @patch("gptme.util.gh.subprocess.run")
    def test_get_github_issue_content_timeout(self, mock_run):
        """TimeoutExpired on issue content returns None gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=60)
        result = get_github_issue_content("owner", "repo", "1")
        assert result is None

    @patch("gptme.util.gh.subprocess.run")
    def test_get_github_actions_status_timeout(self, mock_run):
        """TimeoutExpired on actions status returns None gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = _get_github_actions_status("owner", "repo", "abc123")
        assert result is None

    @patch("gptme.util.gh.subprocess.run")
    def test_merge_github_pr_timeout_message(self, mock_run):
        """TimeoutExpired on merge returns a clean timeout message (not raw CLI invocation)."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = merge_github_pr("owner", "repo", 1)
        assert result["success"] is False
        msg = str(result["message"])
        assert "timed out" in msg
        assert "gh pr merge" not in msg

    @patch("gptme.util.gh.subprocess.run")
    def test_create_github_issue_timeout_message(self, mock_run):
        """TimeoutExpired on issue creation returns a clean timeout message."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = create_github_issue("owner", "repo", "title", "body")
        assert result["success"] is False
        msg = str(result["message"])
        assert "timed out" in msg
        assert "gh issue create" not in msg

    @patch("gptme.util.gh.subprocess.run")
    def test_comment_on_github_timeout_message(self, mock_run):
        """TimeoutExpired on commenting returns a clean timeout message."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = comment_on_github("owner", "repo", 1, "body")
        assert result["success"] is False
        msg = str(result["message"])
        assert "timed out" in msg
        assert "gh issue comment" not in msg
