"""Unit tests for the gh tool (gptme/tools/gh.py).

Tests _get_pr_check_runs, _wait_for_checks, _format_check_results,
_extract_pr_url, _handle_pr_status, and execute_gh with mocked subprocess.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

from gptme.tools.gh import (
    _extract_pr_url,
    _format_check_results,
    _get_pr_check_runs,
    _handle_pr_status,
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


# --- _extract_pr_url ---


class TestExtractPrUrl:
    def test_from_args(self):
        url = _extract_pr_url(["pr", "status", "https://github.com/o/r/pull/1"], None)
        assert url == "https://github.com/o/r/pull/1"

    def test_from_kwargs(self):
        url = _extract_pr_url(None, {"url": "https://github.com/o/r/pull/2"})
        assert url == "https://github.com/o/r/pull/2"

    def test_from_args_with_offset(self):
        url = _extract_pr_url(
            ["checks", "https://github.com/o/r/pull/3"], None, arg_offset=1
        )
        assert url == "https://github.com/o/r/pull/3"

    def test_no_url_none_args(self):
        assert _extract_pr_url(None, None) is None

    def test_no_url_short_args(self):
        assert _extract_pr_url(["pr", "status"], None) is None

    def test_no_url_empty_kwargs(self):
        assert _extract_pr_url(None, {}) is None


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
        assert "No PR URL" in messages[0].content

    def test_invalid_url(self):
        messages = list(
            _handle_pr_status(["pr", "status", "https://invalid.com"], None)
        )
        assert len(messages) == 1
        assert "Invalid GitHub URL" in messages[0].content

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
        assert "No PR URL" in messages[0].content

    def test_pr_view_no_url(self):
        messages = list(execute_gh(None, ["pr", "view"], None))
        assert len(messages) == 1
        assert "No PR URL" in messages[0].content

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
        assert "Invalid GitHub URL" in messages[0].content

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
        assert "Invalid GitHub URL" in messages[0].content
