"""Unit tests for the gh tool (gptme/tools/gh.py).

Tests _get_pr_check_runs, _wait_for_checks, _format_check_results,
_extract_url, _handle_pr_status, and execute_gh with mocked subprocess.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

from gptme.tools.gh import (
    _extract_url,
    _format_check_results,
    _get_pr_check_runs,
    _handle_comment,
    _handle_issue_create,
    _handle_pr_merge,
    _handle_pr_status,
    _parse_flags,
    _parse_list_flags,
    _resolve_ref,
    _resolve_repo_for_list,
    _wait_for_checks,
    execute_gh,
)

# --- Fixtures ---


def _make_check_run(
    name: str,
    status: str = "completed",
    conclusion: str | None = "success",
    run_id: int = 1,
    html_url: str = "",
) -> dict:
    """Helper to create a check run dict."""
    run: dict = {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "id": run_id,
    }
    if html_url:
        run["html_url"] = html_url
    return run


def _mock_subprocess_run(stdout: str, returncode: int = 0):
    """Create a mock for subprocess.run that returns given stdout."""
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    return result


# --- _extract_url ---


class TestExtractUrl:
    def test_from_args(self):
        url = _extract_url(["pr", "status", "https://github.com/o/r/pull/1"], None)
        assert url == "https://github.com/o/r/pull/1"

    def test_from_kwargs(self):
        url = _extract_url(None, {"url": "https://github.com/o/r/pull/2"})
        assert url == "https://github.com/o/r/pull/2"

    def test_from_args_with_offset(self):
        url = _extract_url(
            ["checks", "https://github.com/o/r/pull/3"], None, arg_offset=1
        )
        assert url == "https://github.com/o/r/pull/3"

    def test_no_url_none_args(self):
        assert _extract_url(None, None) is None

    def test_no_url_short_args(self):
        assert _extract_url(["pr", "status"], None) is None

    def test_no_url_empty_kwargs(self):
        assert _extract_url(None, {}) is None


# --- _get_pr_check_runs ---


class TestGetPrCheckRuns:
    @patch("gptme.tools.gh.subprocess.run")
    def test_success(self, mock_run):
        pr_data = {"head": {"sha": "abc1234def"}}
        check_data = {
            "check_runs": [
                _make_check_run("build", "completed", "success"),
                _make_check_run("test", "completed", "failure"),
            ]
        }
        mock_run.side_effect = [
            _mock_subprocess_run(json.dumps(pr_data)),
            _mock_subprocess_run(json.dumps(check_data)),
        ]

        sha, runs, error = _get_pr_check_runs("owner", "repo", 42)
        assert sha == "abc1234def"
        assert runs is not None
        assert len(runs) == 2
        assert error is None

    @patch("gptme.tools.gh.subprocess.run")
    def test_missing_head_sha(self, mock_run):
        pr_data: dict = {"head": {}}  # No sha field
        mock_run.return_value = _mock_subprocess_run(json.dumps(pr_data))

        sha, runs, error = _get_pr_check_runs("owner", "repo", 1)
        assert sha is None
        assert runs is None
        assert error == "Could not get HEAD commit SHA"

    @patch("gptme.tools.gh.subprocess.run")
    def test_api_error(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "gh")

        sha, runs, error = _get_pr_check_runs("owner", "repo", 1)
        assert sha is None
        assert runs is None
        assert error is not None
        assert "Failed to fetch check status" in error

    @patch("gptme.tools.gh.subprocess.run")
    def test_json_decode_error(self, mock_run):
        mock_run.return_value = _mock_subprocess_run("not json")

        sha, runs, error = _get_pr_check_runs("owner", "repo", 1)
        assert sha is None
        assert runs is None
        assert error is not None
        assert "Failed to parse check data" in error

    @patch("gptme.tools.gh.subprocess.run")
    def test_empty_check_runs(self, mock_run):
        pr_data: dict = {"head": {"sha": "abc123"}}
        check_data: dict = {"check_runs": []}
        mock_run.side_effect = [
            _mock_subprocess_run(json.dumps(pr_data)),
            _mock_subprocess_run(json.dumps(check_data)),
        ]

        sha, runs, error = _get_pr_check_runs("owner", "repo", 1)
        assert sha == "abc123"
        assert runs == []
        assert error is None


# --- _format_check_results ---


class TestFormatCheckResults:
    def test_all_passed(self):
        runs = [
            _make_check_run("build", "completed", "success"),
            _make_check_run("test", "completed", "success"),
            _make_check_run("lint", "completed", "success"),
        ]
        result = _format_check_results(runs, "abc1234def5678", 42)
        assert "PR #42" in result
        assert "abc1234" in result
        assert "✅ 3 passed" in result
        assert "❌" not in result

    def test_with_failures(self):
        runs = [
            _make_check_run("build", "completed", "success"),
            _make_check_run(
                "test",
                "completed",
                "failure",
                run_id=99,
                html_url="https://github.com/o/r/actions/runs/12345/jobs/99",
            ),
        ]
        result = _format_check_results(runs, "abc1234", 10)
        assert "✅ 1 passed" in result
        assert "❌ 1 failed" in result
        assert "test" in result
        assert "12345" in result  # run ID from URL

    def test_in_progress(self):
        runs = [
            _make_check_run("build", "completed", "success"),
            _make_check_run("test", "in_progress", None),
            _make_check_run("lint", "queued", None),
        ]
        result = _format_check_results(runs, "abc1234", 5)
        assert "✅ 1 passed" in result
        assert "🔄 2 in progress" in result
        assert "test" in result  # in_progress run name shown

    def test_cancelled_and_skipped(self):
        runs = [
            _make_check_run("build", "completed", "success"),
            _make_check_run("optional", "completed", "cancelled"),
            _make_check_run("skip-me", "completed", "skipped"),
        ]
        result = _format_check_results(runs, "abc1234", 7)
        assert "✅ 1 passed" in result
        assert "🚫 1 cancelled" in result
        assert "⏭️ 1 skipped" in result

    def test_empty_check_runs(self):
        result = _format_check_results([], "abc1234", 1)
        assert "Total: 0" in result

    def test_unknown_conclusion_maps_to_success(self):
        """Conclusions not in the known set default to 'success'."""
        runs = [_make_check_run("weird", "completed", "neutral")]
        result = _format_check_results(runs, "abc1234", 1)
        assert "✅ 1 passed" in result

    def test_pending_status(self):
        runs = [_make_check_run("deploy", "pending", None)]
        result = _format_check_results(runs, "abc1234", 3)
        assert "🔄 1 in progress" in result


# --- _wait_for_checks ---


class TestWaitForChecks:
    @patch("gptme.tools.gh.time.sleep")
    @patch("gptme.tools.gh.subprocess.run")
    @patch("gptme.tools.gh._get_pr_check_runs")
    def test_all_pass_immediately(self, mock_get_pr, mock_run, mock_sleep):
        """All checks already completed successfully."""
        check_runs = [
            _make_check_run("build", "completed", "success"),
            _make_check_run("test", "completed", "success"),
        ]
        mock_get_pr.return_value = ("abc1234", check_runs, None)
        # The poll loop also calls subprocess.run directly
        mock_run.return_value = _mock_subprocess_run(
            json.dumps({"check_runs": check_runs})
        )

        url = "https://github.com/owner/repo/pull/1"
        messages = list(_wait_for_checks("owner", "repo", url))

        # Should have: waiting message, status update, final success
        assert any("Waiting for checks" in m.content for m in messages)
        assert any("All checks passed" in m.content for m in messages)
        mock_sleep.assert_not_called()

    @patch("gptme.tools.gh.time.sleep")
    @patch("gptme.tools.gh.subprocess.run")
    @patch("gptme.tools.gh._get_pr_check_runs")
    def test_failure_detected(self, mock_get_pr, mock_run, mock_sleep):
        """Checks complete with failures."""
        check_runs = [
            _make_check_run("build", "completed", "success"),
            _make_check_run(
                "test",
                "completed",
                "failure",
                run_id=555,
                html_url="https://github.com/o/r/actions/runs/777/jobs/555",
            ),
        ]
        mock_get_pr.return_value = ("abc1234", check_runs, None)
        mock_run.return_value = _mock_subprocess_run(
            json.dumps({"check_runs": check_runs})
        )

        url = "https://github.com/owner/repo/pull/2"
        messages = list(_wait_for_checks("owner", "repo", url))

        assert any("Checks failed" in m.content for m in messages)
        assert any("test" in m.content for m in messages)
        assert any("777" in m.content for m in messages)  # run ID from URL

    @patch("gptme.tools.gh.time.sleep")
    @patch("gptme.tools.gh.subprocess.run")
    @patch("gptme.tools.gh._get_pr_check_runs")
    def test_in_progress_then_complete(self, mock_get_pr, mock_run, mock_sleep):
        """Checks transition from in_progress to completed."""
        initial_runs = [
            _make_check_run("build", "completed", "success"),
            _make_check_run("test", "in_progress", None),
        ]
        final_runs = [
            _make_check_run("build", "completed", "success"),
            _make_check_run("test", "completed", "success"),
        ]
        mock_get_pr.return_value = ("abc1234", initial_runs, None)
        mock_run.side_effect = [
            _mock_subprocess_run(json.dumps({"check_runs": initial_runs})),
            _mock_subprocess_run(json.dumps({"check_runs": final_runs})),
        ]

        url = "https://github.com/owner/repo/pull/3"
        messages = list(_wait_for_checks("owner", "repo", url))

        assert any("in progress" in m.content for m in messages)
        assert any("All checks passed" in m.content for m in messages)
        mock_sleep.assert_called_once_with(10)

    @patch("gptme.tools.gh.time.sleep")
    @patch("gptme.tools.gh.subprocess.run")
    def test_with_commit_sha(self, mock_run, mock_sleep):
        """Using explicit commit SHA bypasses PR lookup."""
        check_runs = [_make_check_run("build", "completed", "success")]
        mock_run.return_value = _mock_subprocess_run(
            json.dumps({"check_runs": check_runs})
        )

        url = "https://github.com/owner/repo/pull/4"
        messages = list(_wait_for_checks("owner", "repo", url, commit_sha="deadbeef"))

        assert any(
            "Waiting for checks on commit deadbee" in m.content for m in messages
        )
        assert any("All checks passed" in m.content for m in messages)
        mock_sleep.assert_not_called()

    @patch("gptme.tools.gh._get_pr_check_runs")
    def test_error_from_get_pr(self, mock_get_pr):
        """Error fetching PR check runs."""
        mock_get_pr.return_value = (None, None, "API rate limited")

        url = "https://github.com/owner/repo/pull/5"
        messages = list(_wait_for_checks("owner", "repo", url))

        assert len(messages) == 1
        assert "API rate limited" in messages[0].content

    def test_invalid_url(self):
        """Invalid GitHub URL returns error."""
        messages = list(_wait_for_checks("owner", "repo", "https://invalid.com"))

        assert len(messages) == 1
        assert "Could not parse PR number" in messages[0].content

    @patch("gptme.tools.gh._get_pr_check_runs")
    def test_no_checks_found(self, mock_get_pr):
        """PR has no check runs."""
        mock_get_pr.return_value = ("abc1234", [], None)

        url = "https://github.com/owner/repo/pull/6"
        messages = list(_wait_for_checks("owner", "repo", url))

        assert any("No checks found" in m.content for m in messages)

    @patch("gptme.tools.gh.time.sleep")
    @patch("gptme.tools.gh.subprocess.run")
    @patch("gptme.tools.gh._get_pr_check_runs")
    def test_cancelled_checks(self, mock_get_pr, mock_run, mock_sleep):
        """All checks complete but some cancelled."""
        check_runs = [
            _make_check_run("build", "completed", "success"),
            _make_check_run("deploy", "completed", "cancelled"),
        ]
        mock_get_pr.return_value = ("abc1234", check_runs, None)
        mock_run.return_value = _mock_subprocess_run(
            json.dumps({"check_runs": check_runs})
        )

        url = "https://github.com/owner/repo/pull/7"
        messages = list(_wait_for_checks("owner", "repo", url))

        assert any("cancelled" in m.content.lower() for m in messages)

    @patch("gptme.tools.gh.time.sleep")
    @patch("gptme.tools.gh.subprocess.run")
    @patch("gptme.tools.gh._get_pr_check_runs")
    def test_poll_api_error(self, mock_get_pr, mock_run, mock_sleep):
        """API error during polling loop."""
        initial_runs = [_make_check_run("build", "in_progress", None)]
        mock_get_pr.return_value = ("abc1234", initial_runs, None)
        mock_run.side_effect = subprocess.CalledProcessError(1, "gh")

        url = "https://github.com/owner/repo/pull/8"
        messages = list(_wait_for_checks("owner", "repo", url))

        assert any("Error fetching checks" in m.content for m in messages)
        mock_sleep.assert_not_called()


# --- _handle_pr_status ---


class TestHandlePrStatus:
    @patch("gptme.tools.gh._get_pr_check_runs")
    def test_success(self, mock_get_pr):
        runs = [_make_check_run("build", "completed", "success")]
        mock_get_pr.return_value = ("abc1234", runs, None)

        messages = list(
            _handle_pr_status(
                ["pr", "status", "https://github.com/owner/repo/pull/1"], None
            )
        )
        assert len(messages) == 1
        assert "PR #1" in messages[0].content
        assert "✅" in messages[0].content

    def test_no_url(self):
        messages = list(_handle_pr_status(["pr", "status"], None))
        assert len(messages) == 1
        assert "No PR reference provided" in messages[0].content

    def test_invalid_url(self):
        messages = list(
            _handle_pr_status(["pr", "status", "https://invalid.com"], None)
        )
        assert len(messages) == 1
        assert "Could not parse GitHub reference" in messages[0].content

    @patch("gptme.tools.gh._get_pr_check_runs")
    def test_error(self, mock_get_pr):
        mock_get_pr.return_value = (None, None, "Network error")

        messages = list(
            _handle_pr_status(
                ["pr", "status", "https://github.com/owner/repo/pull/1"], None
            )
        )
        assert len(messages) == 1
        assert "Network error" in messages[0].content

    @patch("gptme.tools.gh._get_pr_check_runs")
    def test_no_checks(self, mock_get_pr):
        mock_get_pr.return_value = ("abc1234", [], None)

        messages = list(
            _handle_pr_status(
                ["pr", "status", "https://github.com/owner/repo/pull/1"], None
            )
        )
        assert len(messages) == 1
        assert "No checks found" in messages[0].content

    @patch("gptme.tools.gh.subprocess.run")
    def test_with_commit_sha(self, mock_run):
        """Status check with explicit commit SHA."""
        check_runs = [_make_check_run("build", "completed", "success")]
        mock_run.return_value = _mock_subprocess_run(
            json.dumps({"check_runs": check_runs})
        )

        messages = list(
            _handle_pr_status(
                [
                    "pr",
                    "status",
                    "https://github.com/owner/repo/pull/1",
                    "deadbeef",
                ],
                None,
            )
        )
        assert len(messages) == 1
        assert "PR #1" in messages[0].content

    @patch("gptme.tools.gh.subprocess.run")
    def test_commit_sha_api_error(self, mock_run):
        """API error when checking specific commit SHA."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "gh")

        messages = list(
            _handle_pr_status(
                [
                    "pr",
                    "status",
                    "https://github.com/owner/repo/pull/1",
                    "deadbeef",
                ],
                None,
            )
        )
        assert len(messages) == 1
        assert "Failed to fetch checks" in messages[0].content


# --- execute_gh ---


class TestExecuteGh:
    @patch("gptme.tools.gh._handle_pr_status")
    def test_dispatch_status(self, mock_status):
        """gh pr status dispatches to _handle_pr_status."""
        mock_status.return_value = iter([])
        list(execute_gh(None, ["pr", "status", "url"], None))
        mock_status.assert_called_once()

    @patch("gptme.tools.gh._wait_for_checks")
    def test_dispatch_checks(self, mock_wait):
        """gh pr checks dispatches to _wait_for_checks."""
        mock_wait.return_value = iter([])
        list(
            execute_gh(
                None,
                ["pr", "checks", "https://github.com/owner/repo/pull/1"],
                None,
            )
        )
        mock_wait.assert_called_once()

    def test_unknown_command(self):
        messages = list(execute_gh(None, ["unknown", "command"], None))
        assert len(messages) == 1
        assert "Unknown gh command" in messages[0].content

    def test_pr_checks_no_url(self):
        messages = list(execute_gh(None, ["pr", "checks"], None))
        assert len(messages) == 1
        assert "No PR reference provided" in messages[0].content

    def test_pr_view_no_url(self):
        messages = list(execute_gh(None, ["pr", "view"], None))
        assert len(messages) == 1
        assert "No PR reference provided" in messages[0].content

    @patch("gptme.tools.gh.get_github_pr_content")
    def test_pr_view_success(self, mock_content):
        """gh pr view returns content."""
        mock_content.return_value = "PR content here"
        messages = list(
            execute_gh(
                None,
                ["pr", "view", "https://github.com/owner/repo/pull/1"],
                None,
            )
        )
        assert len(messages) == 1
        assert messages[0].content == "PR content here"

    @patch("gptme.tools.gh.get_github_pr_content")
    def test_pr_view_invalid_url(self, mock_content):
        """gh pr view with invalid URL."""
        mock_content.return_value = None
        messages = list(execute_gh(None, ["pr", "view", "https://invalid.com"], None))
        assert len(messages) == 1
        assert "Could not parse GitHub reference" in messages[0].content

    @patch("gptme.tools.gh.get_github_pr_content")
    def test_pr_view_fetch_failure(self, mock_content):
        """gh pr view when fetch fails but URL is valid."""
        mock_content.return_value = None
        messages = list(
            execute_gh(
                None,
                ["pr", "view", "https://github.com/owner/repo/pull/1"],
                None,
            )
        )
        assert len(messages) == 1
        assert "Failed to fetch PR content" in messages[0].content

    def test_pr_checks_invalid_url(self):
        """gh pr checks with invalid URL."""
        messages = list(execute_gh(None, ["pr", "checks", "https://invalid.com"], None))
        assert len(messages) == 1
        assert "Could not parse GitHub reference" in messages[0].content

    # --- gh issue view ---

    def test_issue_view_no_url(self):
        """gh issue view with no URL."""
        messages = list(execute_gh(None, ["issue", "view"], None))
        assert len(messages) == 1
        assert "No issue reference provided" in messages[0].content

    @patch("gptme.tools.gh.get_github_issue_content")
    def test_issue_view_success(self, mock_content):
        """gh issue view returns content."""
        mock_content.return_value = "Issue content here"
        messages = list(
            execute_gh(
                None,
                ["issue", "view", "https://github.com/owner/repo/issues/42"],
                None,
            )
        )
        assert len(messages) == 1
        assert messages[0].content == "Issue content here"
        mock_content.assert_called_once_with("owner", "repo", "42")

    def test_issue_view_invalid_url(self):
        """gh issue view with invalid URL."""
        messages = list(
            execute_gh(None, ["issue", "view", "https://invalid.com"], None)
        )
        assert len(messages) == 1
        assert "Could not parse GitHub reference" in messages[0].content

    def test_issue_view_pr_url_rejected(self):
        """gh issue view with a PR URL gives helpful error."""
        messages = list(
            execute_gh(
                None,
                ["issue", "view", "https://github.com/owner/repo/pull/123"],
                None,
            )
        )
        assert len(messages) == 1
        assert "not a GitHub issue URL" in messages[0].content
        assert "gh pr view" in messages[0].content

    @patch("gptme.tools.gh.get_github_issue_content")
    def test_issue_view_fetch_failure(self, mock_content):
        """gh issue view when fetch fails."""
        mock_content.return_value = None
        messages = list(
            execute_gh(
                None,
                ["issue", "view", "https://github.com/owner/repo/issues/42"],
                None,
            )
        )
        assert len(messages) == 1
        assert "Failed to fetch issue content" in messages[0].content

    def test_unknown_command_lists_issue_view(self):
        """Error message for unknown command includes gh issue view."""
        messages = list(execute_gh(None, ["unknown", "command"], None))
        assert len(messages) == 1
        assert "gh issue view" in messages[0].content

    # --- gh pr diff ---

    def test_pr_diff_no_url(self):
        """gh pr diff with no URL."""
        messages = list(execute_gh(None, ["pr", "diff"], None))
        assert len(messages) == 1
        assert "No PR" in messages[0].content

    def test_pr_diff_invalid_url(self):
        """gh pr diff with invalid URL."""
        messages = list(execute_gh(None, ["pr", "diff", "https://invalid.com"], None))
        assert len(messages) == 1
        assert "Could not parse" in messages[0].content

    def test_pr_diff_issue_url(self):
        """gh pr diff with an issue URL returns a clear error."""
        messages = list(
            execute_gh(
                None,
                ["pr", "diff", "https://github.com/owner/repo/issues/1"],
                None,
            )
        )
        assert len(messages) == 1
        assert "not a GitHub PR" in messages[0].content

    @patch("gptme.tools.gh.get_github_pr_diff")
    def test_pr_diff_success(self, mock_diff):
        """gh pr diff returns diff content."""
        mock_diff.return_value = "PR #1 diff:\n\n file.py | 5 +++++\n\n+new code"
        messages = list(
            execute_gh(
                None,
                ["pr", "diff", "https://github.com/owner/repo/pull/1"],
                None,
            )
        )
        assert len(messages) == 1
        assert "PR #1 diff" in messages[0].content
        mock_diff.assert_called_once_with("owner", "repo", "1")

    @patch("gptme.tools.gh.get_github_pr_diff")
    def test_pr_diff_fetch_failure(self, mock_diff):
        """gh pr diff when fetch fails."""
        mock_diff.return_value = None
        messages = list(
            execute_gh(
                None,
                ["pr", "diff", "https://github.com/owner/repo/pull/1"],
                None,
            )
        )
        assert len(messages) == 1
        assert "Failed to fetch PR diff" in messages[0].content

    def test_unknown_command_lists_pr_diff(self):
        """Error message for unknown command includes gh pr diff."""
        messages = list(execute_gh(None, ["unknown", "command"], None))
        assert len(messages) == 1
        assert "gh pr diff" in messages[0].content

    # --- gh run view ---

    def test_run_view_no_id(self):
        """gh run view with no run ID."""
        messages = list(execute_gh(None, ["run", "view"], None))
        assert len(messages) == 1
        assert "No run ID provided" in messages[0].content

    def test_run_view_invalid_id(self):
        """gh run view with non-numeric ID."""
        messages = list(execute_gh(None, ["run", "view", "not-a-number"], None))
        assert len(messages) == 1
        assert "Invalid run ID" in messages[0].content

    @patch("gptme.tools.gh.get_github_run_logs")
    def test_run_view_success(self, mock_logs):
        """gh run view returns formatted logs."""
        mock_logs.return_value = "## Run 12345: Test\n\n❌ test: failure"
        messages = list(execute_gh(None, ["run", "view", "12345"], None))
        assert len(messages) == 1
        assert "Run 12345" in messages[0].content
        mock_logs.assert_called_once_with("12345")

    @patch("gptme.tools.gh.get_github_run_logs")
    def test_run_view_fetch_failure(self, mock_logs):
        """gh run view when fetch fails."""
        mock_logs.return_value = None
        messages = list(execute_gh(None, ["run", "view", "12345"], None))
        assert len(messages) == 1
        assert "Failed to fetch run" in messages[0].content

    def test_unknown_command_lists_run_view(self):
        """Error message for unknown command includes gh run view."""
        messages = list(execute_gh(None, ["unknown", "command"], None))
        assert len(messages) == 1
        assert "gh run view" in messages[0].content


# --- _parse_list_flags ---


class TestParseListFlags:
    def test_empty_args(self):
        """No flags returns empty dict."""
        assert _parse_list_flags(["issue", "list"]) == {}

    def test_repo_flag(self):
        flags = _parse_list_flags(["issue", "list", "--repo", "owner/repo"])
        assert flags == {"repo": "owner/repo"}

    def test_multiple_flags(self):
        flags = _parse_list_flags(
            ["issue", "list", "--repo", "o/r", "--state", "closed", "--limit", "10"]
        )
        assert flags == {"repo": "o/r", "state": "closed", "limit": "10"}

    def test_label_flag(self):
        flags = _parse_list_flags(["issue", "list", "--label", "bug,enhancement"])
        assert flags == {"label": "bug,enhancement"}


# --- gh issue list ---


class TestIssueList:
    @patch("gptme.tools.gh._get_repo_from_git_remote")
    @patch("gptme.tools.gh.get_github_issue_list")
    def test_issue_list_with_repo(self, mock_list, mock_remote):
        """gh issue list --repo owner/repo returns structured output."""
        mock_list.return_value = "Issues in owner/repo (open):\n\n  #1 Test issue"
        messages = list(
            execute_gh(None, ["issue", "list", "--repo", "owner/repo"], None)
        )
        assert len(messages) == 1
        assert "Issues in owner/repo" in messages[0].content
        mock_list.assert_called_once_with(
            "owner", "repo", state="open", labels=None, limit=20
        )
        mock_remote.assert_not_called()

    @patch("gptme.tools.gh._get_repo_from_git_remote")
    @patch("gptme.tools.gh.get_github_issue_list")
    def test_issue_list_auto_detect_repo(self, mock_list, mock_remote):
        """gh issue list without --repo uses git remote."""
        mock_remote.return_value = ("auto-owner", "auto-repo")
        mock_list.return_value = "Issues in auto-owner/auto-repo (open):\n\n  #1 Issue"
        messages = list(execute_gh(None, ["issue", "list"], None))
        assert len(messages) == 1
        mock_remote.assert_called_once()
        mock_list.assert_called_once_with(
            "auto-owner", "auto-repo", state="open", labels=None, limit=20
        )

    @patch("gptme.tools.gh._get_repo_from_git_remote")
    def test_issue_list_no_repo_detected(self, mock_remote):
        """gh issue list without --repo and no git remote fails gracefully."""
        mock_remote.return_value = None
        messages = list(execute_gh(None, ["issue", "list"], None))
        assert len(messages) == 1
        assert "Could not determine repository" in messages[0].content

    @patch("gptme.tools.gh._get_repo_from_git_remote")
    @patch("gptme.tools.gh.get_github_issue_list")
    def test_issue_list_with_filters(self, mock_list, mock_remote):
        """gh issue list with --state, --label, --limit flags."""
        mock_list.return_value = "Issues in o/r (closed):\n\n  #5 Closed issue"
        messages = list(
            execute_gh(
                None,
                [
                    "issue",
                    "list",
                    "--repo",
                    "o/r",
                    "--state",
                    "closed",
                    "--label",
                    "bug",
                    "--limit",
                    "5",
                ],
                None,
            )
        )
        assert len(messages) == 1
        mock_list.assert_called_once_with(
            "o", "r", state="closed", labels=["bug"], limit=5
        )

    @patch("gptme.tools.gh._get_repo_from_git_remote")
    @patch("gptme.tools.gh.get_github_issue_list")
    def test_issue_list_fetch_failure(self, mock_list, mock_remote):
        """gh issue list when fetch fails."""
        mock_list.return_value = None
        messages = list(
            execute_gh(None, ["issue", "list", "--repo", "owner/repo"], None)
        )
        assert len(messages) == 1
        assert "Failed to list issues" in messages[0].content


# --- gh pr list ---


class TestPrList:
    @patch("gptme.tools.gh._get_repo_from_git_remote")
    @patch("gptme.tools.gh.get_github_pr_list")
    def test_pr_list_with_repo(self, mock_list, mock_remote):
        """gh pr list --repo owner/repo returns structured output."""
        mock_list.return_value = "Pull requests in owner/repo (open):\n\n  #10 PR"
        messages = list(execute_gh(None, ["pr", "list", "--repo", "owner/repo"], None))
        assert len(messages) == 1
        assert "Pull requests in owner/repo" in messages[0].content
        mock_list.assert_called_once_with("owner", "repo", state="open", limit=20)
        mock_remote.assert_not_called()

    @patch("gptme.tools.gh._get_repo_from_git_remote")
    @patch("gptme.tools.gh.get_github_pr_list")
    def test_pr_list_auto_detect_repo(self, mock_list, mock_remote):
        """gh pr list without --repo uses git remote."""
        mock_remote.return_value = ("auto-owner", "auto-repo")
        mock_list.return_value = (
            "Pull requests in auto-owner/auto-repo (open):\n\n  #1 PR"
        )
        messages = list(execute_gh(None, ["pr", "list"], None))
        assert len(messages) == 1
        mock_remote.assert_called_once()

    @patch("gptme.tools.gh._get_repo_from_git_remote")
    def test_pr_list_no_repo_detected(self, mock_remote):
        """gh pr list without --repo and no git remote fails gracefully."""
        mock_remote.return_value = None
        messages = list(execute_gh(None, ["pr", "list"], None))
        assert len(messages) == 1
        assert "Could not determine repository" in messages[0].content

    @patch("gptme.tools.gh._get_repo_from_git_remote")
    @patch("gptme.tools.gh.get_github_pr_list")
    def test_pr_list_with_state_and_limit(self, mock_list, mock_remote):
        """gh pr list with --state and --limit flags."""
        mock_list.return_value = "Pull requests in o/r (merged):\n\n  #2 Merged PR"
        messages = list(
            execute_gh(
                None,
                ["pr", "list", "--repo", "o/r", "--state", "merged", "--limit", "10"],
                None,
            )
        )
        assert len(messages) == 1
        mock_list.assert_called_once_with("o", "r", state="merged", limit=10)

    @patch("gptme.tools.gh._get_repo_from_git_remote")
    @patch("gptme.tools.gh.get_github_pr_list")
    def test_pr_list_fetch_failure(self, mock_list, mock_remote):
        """gh pr list when fetch fails."""
        mock_list.return_value = None
        messages = list(execute_gh(None, ["pr", "list", "--repo", "owner/repo"], None))
        assert len(messages) == 1
        assert "Failed to list pull requests" in messages[0].content

    def test_unknown_command_lists_issue_list(self):
        """Error message for unknown command includes gh issue list."""
        messages = list(execute_gh(None, ["unknown", "command"], None))
        assert len(messages) == 1
        assert "gh issue list" in messages[0].content

    def test_unknown_command_lists_pr_list(self):
        """Error message for unknown command includes gh pr list."""
        messages = list(execute_gh(None, ["unknown", "command"], None))
        assert len(messages) == 1
        assert "gh pr list" in messages[0].content


# --- _resolve_ref ---


class TestResolveRef:
    def test_success_with_url(self):
        """Valid GitHub URL resolves to github_info."""
        info, err = _resolve_ref(
            ["pr", "view", "https://github.com/owner/repo/pull/42"],
            None,
            "pull",
        )
        assert err is None
        assert info is not None
        assert info["owner"] == "owner"
        assert info["repo"] == "repo"
        assert info["number"] == "42"

    def test_success_with_short_ref(self):
        """Short reference owner/repo#N resolves correctly."""
        info, err = _resolve_ref(
            ["issue", "view", "owner/repo#99"],
            None,
            "issues",
        )
        assert err is None
        assert info is not None
        assert info["owner"] == "owner"
        assert info["repo"] == "repo"

    def test_no_ref_provided(self):
        """Missing ref returns error with custom entity name."""
        info, err = _resolve_ref(["pr", "view"], None, "pull", "PR reference")
        assert info is None
        assert err is not None
        assert "No PR reference provided" in err.content

    def test_no_ref_default_entity(self):
        """Missing ref uses default entity name."""
        info, err = _resolve_ref(["pr", "view"], None, "pull")
        assert info is None
        assert err is not None
        assert "No reference provided" in err.content

    def test_invalid_ref(self):
        """Unparseable ref returns error."""
        info, err = _resolve_ref(["pr", "view", "https://invalid.com"], None, "pull")
        assert info is None
        assert err is not None
        assert "Could not parse GitHub reference" in err.content

    def test_from_kwargs(self):
        """Ref can be provided via kwargs."""
        info, err = _resolve_ref(
            None,
            {"url": "https://github.com/owner/repo/pull/1"},
            "pull",
        )
        assert err is None
        assert info is not None
        assert info["owner"] == "owner"


# --- _resolve_repo_for_list ---


class TestResolveRepoForList:
    def test_repo_from_flag(self):
        """--repo flag provides owner/repo."""
        owner, repo, flags, err = _resolve_repo_for_list(
            ["issue", "list", "--repo", "owner/repo"]
        )
        assert err is None
        assert owner == "owner"
        assert repo == "repo"
        assert flags["repo"] == "owner/repo"

    @patch("gptme.tools.gh._get_repo_from_git_remote")
    def test_repo_from_git_remote(self, mock_remote):
        """Without --repo, falls back to git remote."""
        mock_remote.return_value = ("remote-owner", "remote-repo")
        owner, repo, flags, err = _resolve_repo_for_list(["issue", "list"])
        assert err is None
        assert owner == "remote-owner"
        assert repo == "remote-repo"

    @patch("gptme.tools.gh._get_repo_from_git_remote")
    def test_no_repo_detected(self, mock_remote):
        """No --repo and no git remote returns error."""
        mock_remote.return_value = None
        owner, repo, flags, err = _resolve_repo_for_list(["issue", "list"])
        assert owner is None
        assert repo is None
        assert err is not None
        assert "Could not determine repository" in err.content

    def test_flags_preserved(self):
        """Other flags are parsed and returned."""
        owner, repo, flags, err = _resolve_repo_for_list(
            ["pr", "list", "--repo", "o/r", "--state", "closed", "--limit", "5"]
        )
        assert err is None
        assert flags["state"] == "closed"
        assert flags["limit"] == "5"


# --- gh search dispatch ---


class TestSearchDispatch:
    @patch("gptme.tools.gh.search_github_issues")
    def test_search_issues_dispatches(self, mock_search):
        """gh search issues dispatches to search_github_issues."""
        mock_search.return_value = "Search results"
        messages = list(execute_gh(None, ["search", "issues", "auth", "bug"], None))
        assert len(messages) == 1
        assert messages[0].content == "Search results"
        mock_search.assert_called_once_with(
            "auth bug",
            repo=None,
            state=None,
            author=None,
            assignee=None,
            label=None,
            limit=20,
        )

    @patch("gptme.tools.gh.search_github_prs")
    def test_search_prs_dispatches(self, mock_search):
        """gh search prs dispatches to search_github_prs."""
        mock_search.return_value = "PR results"
        messages = list(execute_gh(None, ["search", "prs", "feature"], None))
        assert len(messages) == 1
        assert messages[0].content == "PR results"
        mock_search.assert_called_once_with(
            "feature",
            repo=None,
            state=None,
            author=None,
            label=None,
            limit=20,
        )

    @patch("gptme.tools.gh.search_github_issues")
    def test_search_issues_with_flags(self, mock_search):
        """gh search issues passes flags correctly."""
        mock_search.return_value = "results"
        messages = list(
            execute_gh(
                None,
                [
                    "search",
                    "issues",
                    "bug",
                    "--repo",
                    "owner/repo",
                    "--state",
                    "open",
                    "--author",
                    "alice",
                    "--label",
                    "critical",
                    "--limit",
                    "5",
                ],
                None,
            )
        )
        assert len(messages) == 1
        mock_search.assert_called_once_with(
            "bug",
            repo="owner/repo",
            state="open",
            author="alice",
            assignee=None,
            label="critical",
            limit=5,
        )

    @patch("gptme.tools.gh.search_github_prs")
    def test_search_prs_with_flags(self, mock_search):
        """gh search prs passes flags correctly."""
        mock_search.return_value = "results"
        messages = list(
            execute_gh(
                None,
                ["search", "prs", "fix", "--author", "bob", "--state", "merged"],
                None,
            )
        )
        assert len(messages) == 1
        mock_search.assert_called_once_with(
            "fix",
            repo=None,
            state="merged",
            author="bob",
            label=None,
            limit=20,
        )

    def test_search_no_query(self):
        """gh search issues without query shows error."""
        messages = list(execute_gh(None, ["search", "issues"], None))
        assert len(messages) == 1
        assert "No search query" in messages[0].content

    def test_search_trailing_flag_errors(self):
        """gh search rejects flags without values."""
        messages = list(execute_gh(None, ["search", "issues", "auth", "--state"], None))
        assert len(messages) == 1
        assert "Flag --state requires a value" in messages[0].content

    def test_search_flag_followed_by_flag_errors(self):
        """gh search rejects flags whose next token is another flag."""
        messages = list(
            execute_gh(
                None,
                ["search", "issues", "auth", "--state", "--author", "bob"],
                None,
            )
        )
        assert len(messages) == 1
        assert "Flag --state requires a value" in messages[0].content

    @patch("gptme.tools.gh.search_github_issues")
    def test_search_flag_without_value(self, mock_search):
        """Dangling search flags return an explicit error."""
        messages = list(execute_gh(None, ["search", "issues", "auth", "--state"], None))
        assert len(messages) == 1
        assert "Flag --state requires a value" in messages[0].content
        mock_search.assert_not_called()

    def test_search_invalid_limit(self):
        """gh search rejects non-integer --limit values explicitly."""
        for bad_limit in ["abc", "-5", "1.5"]:
            messages = list(
                execute_gh(
                    None,
                    ["search", "issues", "auth", "--limit", bad_limit],
                    None,
                )
            )
            assert len(messages) == 1, f"Expected 1 message for limit={bad_limit!r}"
            assert "--limit requires a positive integer" in messages[0].content

    @patch("gptme.tools.gh.search_github_issues")
    def test_search_failure(self, mock_search):
        """gh search returns error on failure."""
        mock_search.return_value = None
        messages = list(execute_gh(None, ["search", "issues", "query"], None))
        assert len(messages) == 1
        assert "Failed to search" in messages[0].content

    @patch("gptme.tools.gh.search_github_issues")
    def test_search_issues_multi_word_query(self, mock_search):
        """Multi-word queries are joined correctly."""
        mock_search.return_value = "results"
        list(execute_gh(None, ["search", "issues", "fix", "auth", "bug"], None))
        mock_search.assert_called_once()
        assert mock_search.call_args[0][0] == "fix auth bug"

    def test_search_prs_rejects_assignee(self):
        """gh search prs rejects --assignee with a clear error (not silently ignored)."""
        messages = list(
            execute_gh(
                None,
                ["search", "prs", "auth", "--assignee", "alice"],
                None,
            )
        )
        assert len(messages) == 1
        assert "--assignee is not supported for PR search" in messages[0].content
        assert "--author" in messages[0].content

    @patch("gptme.tools.gh.search_github_issues")
    def test_search_issues_flags_between_words(self, mock_search):
        """Flags interspersed with query words are parsed correctly."""
        mock_search.return_value = "results"
        list(
            execute_gh(
                None,
                ["search", "issues", "auth", "--repo", "o/r", "bug"],
                None,
            )
        )
        mock_search.assert_called_once()
        assert mock_search.call_args[0][0] == "auth bug"
        assert mock_search.call_args[1]["repo"] == "o/r"

    # --- gh pr merge ---

    @patch("gptme.tools.gh._handle_pr_merge")
    def test_dispatch_merge(self, mock_merge):
        """gh pr merge dispatches to _handle_pr_merge."""
        mock_merge.return_value = iter([])
        list(
            execute_gh(
                None,
                ["pr", "merge", "https://github.com/owner/repo/pull/1"],
                None,
            )
        )
        mock_merge.assert_called_once()

    def test_pr_merge_no_ref(self):
        """gh pr merge with no reference gives error."""
        messages = list(execute_gh(None, ["pr", "merge"], None))
        assert len(messages) == 1
        assert "No PR reference provided" in messages[0].content

    def test_pr_merge_invalid_ref(self):
        """gh pr merge with invalid reference gives parse error."""
        messages = list(execute_gh(None, ["pr", "merge", "https://invalid.com"], None))
        assert len(messages) == 1
        assert "Could not parse GitHub reference" in messages[0].content

    @patch("gptme.tools.gh.merge_github_pr")
    def test_pr_merge_success(self, mock_merge):
        """gh pr merge returns success with SHA."""
        mock_merge.return_value = {
            "success": True,
            "message": "✓ Squashed and merged pull request #42",
            "url": "https://github.com/owner/repo/pull/42",
            "sha": "abc1234",
        }
        messages = list(
            execute_gh(
                None,
                ["pr", "merge", "https://github.com/owner/repo/pull/42"],
                None,
            )
        )
        assert len(messages) == 1
        assert "merged" in messages[0].content.lower()
        assert "abc1234" in messages[0].content

    @patch("gptme.tools.gh.merge_github_pr")
    def test_pr_merge_failure(self, mock_merge):
        """gh pr merge returns error on failure."""
        mock_merge.return_value = {
            "success": False,
            "message": "Cannot merge PR #42: merge conflicts exist.",
        }
        messages = list(
            execute_gh(
                None,
                ["pr", "merge", "https://github.com/owner/repo/pull/42"],
                None,
            )
        )
        assert len(messages) == 1
        assert "Error:" in messages[0].content
        assert "merge conflicts" in messages[0].content

    @patch("gptme.tools.gh.merge_github_pr")
    def test_pr_merge_squash_default(self, mock_merge):
        """gh pr merge defaults to squash."""
        mock_merge.return_value = {"success": True, "message": "Merged"}
        list(
            execute_gh(
                None,
                ["pr", "merge", "https://github.com/owner/repo/pull/42"],
                None,
            )
        )
        assert mock_merge.call_args[1]["method"] == "squash"

    @patch("gptme.tools.gh.merge_github_pr")
    def test_pr_merge_rebase_flag(self, mock_merge):
        """gh pr merge --rebase passes rebase method."""
        mock_merge.return_value = {"success": True, "message": "Merged"}
        list(
            execute_gh(
                None,
                ["pr", "merge", "https://github.com/owner/repo/pull/42", "--rebase"],
                None,
            )
        )
        assert mock_merge.call_args[1]["method"] == "rebase"

    @patch("gptme.tools.gh.merge_github_pr")
    def test_pr_merge_auto_flag(self, mock_merge):
        """gh pr merge --auto enables auto-merge."""
        mock_merge.return_value = {"success": True, "message": "Auto-merge enabled"}
        list(
            execute_gh(
                None,
                [
                    "pr",
                    "merge",
                    "https://github.com/owner/repo/pull/42",
                    "--squash",
                    "--auto",
                ],
                None,
            )
        )
        assert mock_merge.call_args[1]["auto"] is True

    @patch("gptme.tools.gh.merge_github_pr")
    def test_pr_merge_delete_branch(self, mock_merge):
        """gh pr merge --delete-branch passes flag."""
        mock_merge.return_value = {"success": True, "message": "Merged"}
        list(
            execute_gh(
                None,
                [
                    "pr",
                    "merge",
                    "https://github.com/owner/repo/pull/42",
                    "--delete-branch",
                ],
                None,
            )
        )
        assert mock_merge.call_args[1]["delete_branch"] is True

    @patch("gptme.tools.gh.merge_github_pr")
    def test_pr_merge_match_head_commit(self, mock_merge):
        """gh pr merge --match-head-commit passes SHA."""
        mock_merge.return_value = {"success": True, "message": "Merged"}
        list(
            execute_gh(
                None,
                [
                    "pr",
                    "merge",
                    "https://github.com/owner/repo/pull/42",
                    "--match-head-commit",
                    "abc123",
                ],
                None,
            )
        )
        assert mock_merge.call_args[1]["match_head_commit"] == "abc123"

    @patch("gptme.tools.gh.merge_github_pr")
    def test_pr_merge_short_ref(self, mock_merge):
        """gh pr merge works with owner/repo#N reference."""
        mock_merge.return_value = {"success": True, "message": "Merged"}
        list(execute_gh(None, ["pr", "merge", "owner/repo#42"], None))
        mock_merge.assert_called_once()
        assert mock_merge.call_args[0] == ("owner", "repo", "42")

    def test_unknown_command_lists_pr_merge(self):
        """Error message for unknown commands includes gh pr merge."""
        messages = list(execute_gh(None, ["foo", "bar"], None))
        assert "gh pr merge" in messages[0].content


# --- _handle_pr_merge unit tests ---


class TestHandlePrMerge:
    @patch("gptme.tools.gh.merge_github_pr")
    def test_success_with_sha(self, mock_merge):
        """Success output includes merge SHA."""
        mock_merge.return_value = {
            "success": True,
            "message": "✓ Merged",
            "url": "https://github.com/o/r/pull/1",
            "sha": "deadbeef",
        }
        messages = list(
            _handle_pr_merge(["pr", "merge", "https://github.com/o/r/pull/1"], None)
        )
        assert len(messages) == 1
        assert "deadbeef" in messages[0].content

    @patch("gptme.tools.gh.merge_github_pr")
    def test_success_without_sha(self, mock_merge):
        """Success output works without SHA (auto-merge case)."""
        mock_merge.return_value = {
            "success": True,
            "message": "✓ Auto-merge enabled",
            "url": "https://github.com/o/r/pull/1",
        }
        messages = list(
            _handle_pr_merge(
                ["pr", "merge", "https://github.com/o/r/pull/1", "--auto"], None
            )
        )
        assert len(messages) == 1
        assert "Auto-merge" in messages[0].content
        assert "Merge commit" not in messages[0].content

    @patch("gptme.tools.gh.merge_github_pr")
    def test_failure(self, mock_merge):
        """Failure output starts with Error:."""
        mock_merge.return_value = {
            "success": False,
            "message": "Cannot merge: conflicts",
        }
        messages = list(
            _handle_pr_merge(["pr", "merge", "https://github.com/o/r/pull/1"], None)
        )
        assert len(messages) == 1
        assert messages[0].content.startswith("Error:")

    def test_no_ref(self):
        """Missing reference gives error."""
        messages = list(_handle_pr_merge(["pr", "merge"], None))
        assert len(messages) == 1
        assert "No PR reference provided" in messages[0].content

    @patch("gptme.tools.gh.merge_github_pr")
    def test_all_flags_combined(self, mock_merge):
        """All flags can be combined."""
        mock_merge.return_value = {"success": True, "message": "Merged"}
        list(
            _handle_pr_merge(
                [
                    "pr",
                    "merge",
                    "o/r#42",
                    "--rebase",
                    "--auto",
                    "--delete-branch",
                    "--match-head-commit",
                    "sha123",
                ],
                None,
            )
        )
        assert mock_merge.call_args[1]["method"] == "rebase"
        assert mock_merge.call_args[1]["auto"] is True
        assert mock_merge.call_args[1]["delete_branch"] is True
        assert mock_merge.call_args[1]["match_head_commit"] == "sha123"


# --- _parse_flags tests ---


class TestParseFlags:
    """Tests for _parse_flags helper."""

    def test_empty_args(self):
        positional, flags = _parse_flags(["issue", "create"], start=2)
        assert positional == []
        assert flags == {}

    def test_flags_only(self):
        positional, flags = _parse_flags(
            ["issue", "create", "--title", "Bug", "--body", "Details"], start=2
        )
        assert positional == []
        assert flags == {"title": "Bug", "body": "Details"}

    def test_mixed_positional_and_flags(self):
        positional, flags = _parse_flags(
            ["issue", "comment", "owner/repo#42", "--body", "Hello"], start=2
        )
        assert positional == ["owner/repo#42"]
        assert flags == {"body": "Hello"}

    def test_boolean_flag(self):
        positional, flags = _parse_flags(
            ["pr", "merge", "ref", "--auto", "--squash"], start=2
        )
        assert "ref" in positional
        assert flags["auto"] == "true"
        assert flags["squash"] == "true"


# --- _handle_issue_create tests ---


class TestHandleIssueCreate:
    """Tests for _handle_issue_create."""

    @patch("gptme.tools.gh.create_github_issue")
    @patch("gptme.tools.gh._get_repo_from_git_remote", return_value=None)
    def test_no_repo(self, mock_remote, mock_create):
        """Error when no repo can be determined."""
        messages = list(_handle_issue_create(["issue", "create", "--title", "Bug"]))
        assert len(messages) == 1
        assert "Could not determine repository" in messages[0].content
        mock_create.assert_not_called()

    @patch("gptme.tools.gh.create_github_issue")
    def test_no_title(self, mock_create):
        """Error when --title is missing."""
        messages = list(
            _handle_issue_create(["issue", "create", "--repo", "owner/repo"])
        )
        assert len(messages) == 1
        assert "--title is required" in messages[0].content
        mock_create.assert_not_called()

    @patch("gptme.tools.gh.create_github_issue")
    def test_success_with_repo_flag(self, mock_create):
        """Successful issue creation with --repo flag."""
        mock_create.return_value = {
            "success": True,
            "number": 42,
            "url": "https://github.com/owner/repo/issues/42",
            "message": "Created issue #42: Fix bug",
        }
        messages = list(
            _handle_issue_create(
                [
                    "issue",
                    "create",
                    "--repo",
                    "owner/repo",
                    "--title",
                    "Fix bug",
                    "--body",
                    "Description",
                ]
            )
        )
        assert len(messages) == 1
        assert "✓" in messages[0].content
        assert "Created issue #42" in messages[0].content
        mock_create.assert_called_once_with(
            "owner", "repo", "Fix bug", "Description", labels=None, assignees=None
        )

    @patch("gptme.tools.gh.create_github_issue")
    def test_with_labels_and_assignees(self, mock_create):
        """Labels and assignees are parsed correctly."""
        mock_create.return_value = {
            "success": True,
            "number": 1,
            "url": "https://github.com/o/r/issues/1",
            "message": "Created issue #1: Test",
        }
        list(
            _handle_issue_create(
                [
                    "issue",
                    "create",
                    "--repo",
                    "o/r",
                    "--title",
                    "Test",
                    "--label",
                    "bug,urgent",
                    "--assignee",
                    "alice,bob",
                ]
            )
        )
        mock_create.assert_called_once_with(
            "o", "r", "Test", "", labels=["bug", "urgent"], assignees=["alice", "bob"]
        )

    @patch("gptme.tools.gh.create_github_issue")
    @patch("gptme.tools.gh._get_repo_from_git_remote", return_value=("auto", "repo"))
    def test_auto_detect_repo(self, mock_remote, mock_create):
        """Repo auto-detected from git remote when --repo not given."""
        mock_create.return_value = {
            "success": True,
            "number": 5,
            "url": "https://github.com/auto/repo/issues/5",
            "message": "Created issue #5: Auto",
        }
        messages = list(_handle_issue_create(["issue", "create", "--title", "Auto"]))
        assert "✓" in messages[0].content
        mock_create.assert_called_once_with(
            "auto", "repo", "Auto", "", labels=None, assignees=None
        )

    @patch("gptme.tools.gh.create_github_issue")
    def test_failure(self, mock_create):
        """Error message on failure."""
        mock_create.return_value = {
            "success": False,
            "number": 0,
            "url": "",
            "message": "Permission denied",
        }
        messages = list(
            _handle_issue_create(["issue", "create", "--repo", "o/r", "--title", "X"])
        )
        assert "Error:" in messages[0].content
        assert "Permission denied" in messages[0].content


# --- _handle_comment tests ---


class TestHandleComment:
    """Tests for _handle_comment (issue and PR commenting)."""

    @patch("gptme.tools.gh.comment_on_github")
    def test_issue_comment_success(self, mock_comment):
        """Successful issue comment."""
        mock_comment.return_value = {
            "success": True,
            "url": "https://github.com/o/r/issues/42#issuecomment-123",
            "message": "Commented on issue #42",
        }
        messages = list(
            _handle_comment(["issue", "comment", "o/r#42", "--body", "Fixed in PR #50"])
        )
        assert len(messages) == 1
        assert "✓" in messages[0].content
        assert "Commented on issue #42" in messages[0].content
        mock_comment.assert_called_once_with(
            "o", "r", 42, "Fixed in PR #50", kind="issue"
        )

    @patch("gptme.tools.gh.comment_on_github")
    def test_pr_comment_success(self, mock_comment):
        """Successful PR comment."""
        mock_comment.return_value = {
            "success": True,
            "url": "https://github.com/o/r/pull/10#issuecomment-456",
            "message": "Commented on pr #10",
        }
        messages = list(_handle_comment(["pr", "comment", "o/r#10", "--body", "LGTM"]))
        assert len(messages) == 1
        assert "✓" in messages[0].content
        mock_comment.assert_called_once_with("o", "r", 10, "LGTM", kind="pr")

    def test_missing_ref(self):
        """Error when reference is missing."""
        messages = list(_handle_comment(["issue", "comment"]))
        assert "Missing reference" in messages[0].content

    def test_missing_body(self):
        """Error when --body is missing."""
        messages = list(_handle_comment(["issue", "comment", "o/r#42"]))
        assert "--body is required" in messages[0].content

    def test_invalid_ref(self):
        """Error when reference cannot be parsed."""
        messages = list(
            _handle_comment(["issue", "comment", "invalid", "--body", "text"])
        )
        assert "Could not parse reference" in messages[0].content

    @patch("gptme.tools.gh.comment_on_github")
    def test_comment_failure(self, mock_comment):
        """Error message on failure."""
        mock_comment.return_value = {
            "success": False,
            "url": "",
            "message": "Failed to comment on issue #42: Not Found",
        }
        messages = list(
            _handle_comment(["issue", "comment", "o/r#42", "--body", "text"])
        )
        assert "Error:" in messages[0].content
        assert "Not Found" in messages[0].content

    @patch("gptme.tools.gh.comment_on_github")
    def test_comment_with_url(self, mock_comment):
        """URL reference works for commenting."""
        mock_comment.return_value = {
            "success": True,
            "url": "",
            "message": "Commented on issue #42",
        }
        list(
            _handle_comment(
                [
                    "issue",
                    "comment",
                    "https://github.com/owner/repo/issues/42",
                    "--body",
                    "Hi",
                ]
            )
        )
        mock_comment.assert_called_once_with("owner", "repo", 42, "Hi", kind="issue")


# --- execute_gh integration tests for new commands ---


class TestExecuteGhNewCommands:
    """Integration tests for issue create and comment via execute_gh."""

    @patch("gptme.tools.gh.create_github_issue")
    def test_execute_issue_create(self, mock_create):
        """execute_gh dispatches gh issue create correctly."""
        mock_create.return_value = {
            "success": True,
            "number": 99,
            "url": "https://github.com/o/r/issues/99",
            "message": "Created issue #99: Test",
        }
        messages = list(
            execute_gh(
                None,
                ["issue", "create", "--repo", "o/r", "--title", "Test", "--body", "B"],
                None,
            )
        )
        assert any("✓" in m.content for m in messages)

    @patch("gptme.tools.gh.comment_on_github")
    def test_execute_issue_comment(self, mock_comment):
        """execute_gh dispatches gh issue comment correctly."""
        mock_comment.return_value = {
            "success": True,
            "url": "",
            "message": "Commented on issue #1",
        }
        messages = list(
            execute_gh(
                None,
                ["issue", "comment", "o/r#1", "--body", "Done"],
                None,
            )
        )
        assert any("✓" in m.content for m in messages)

    @patch("gptme.tools.gh.comment_on_github")
    def test_execute_pr_comment(self, mock_comment):
        """execute_gh dispatches gh pr comment correctly."""
        mock_comment.return_value = {
            "success": True,
            "url": "",
            "message": "Commented on pr #5",
        }
        messages = list(
            execute_gh(
                None,
                ["pr", "comment", "o/r#5", "--body", "Reviewed"],
                None,
            )
        )
        assert any("✓" in m.content for m in messages)
