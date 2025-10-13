import json
import shutil
import subprocess
import time
from collections.abc import Generator

from ..message import Message
from ..util.gh import get_github_pr_content, parse_github_url
from . import ConfirmFunc, Parameter, ToolSpec, ToolUse


def has_gh_tool() -> bool:
    return shutil.which("gh") is not None


def _get_pr_check_runs(
    owner: str, repo: str, pr_number: int
) -> tuple[str | None, list | None, str | None]:
    """Get check runs for a PR.

    Returns:
        Tuple of (head_sha, check_runs, error_message)
    """
    try:
        # Get PR details to extract HEAD commit SHA
        pr_details_result = subprocess.run(
            ["gh", "api", f"/repos/{owner}/{repo}/pulls/{pr_number}"],
            capture_output=True,
            text=True,
            check=True,
        )
        pr_details = json.loads(pr_details_result.stdout)
        head_sha = pr_details.get("head", {}).get("sha")

        if not head_sha:
            return None, None, "Could not get HEAD commit SHA"

        # Get check runs for the commit
        check_runs_result = subprocess.run(
            ["gh", "api", f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs"],
            capture_output=True,
            text=True,
            check=True,
        )

        check_runs_data = json.loads(check_runs_result.stdout)
        check_runs = check_runs_data.get("check_runs", [])

        return head_sha, check_runs, None

    except subprocess.CalledProcessError as e:
        return None, None, f"Failed to fetch check status: {e}"
    except (json.JSONDecodeError, KeyError) as e:
        return None, None, f"Failed to parse check data: {e}"


def _wait_for_checks(
    owner: str, repo: str, url: str, commit_sha: str | None = None
) -> Generator[Message, None, None]:
    """Wait for all GitHub Actions checks to complete on a PR or commit."""
    import logging

    logger = logging.getLogger(__name__)

    # Use provided commit SHA or get from PR
    pr_number: int | None
    if commit_sha:
        head_sha: str | None = commit_sha
        pr_number = None  # Not needed when checking specific commit
    else:
        # Get PR details to extract HEAD commit SHA
        pr_info = parse_github_url(url)
        if not pr_info:
            yield Message("system", "Error: Could not parse PR number from URL")
            return

        pr_number = int(pr_info["number"])

        head_sha, check_runs_initial, error = _get_pr_check_runs(owner, repo, pr_number)
        if error:
            yield Message("system", f"Error: {error}")
            return

        if not check_runs_initial:
            yield Message("system", "No checks found for this PR")
            return

    assert head_sha is not None  # Ensured by earlier error check
    yield Message("system", f"Waiting for checks on commit {head_sha[:7]}...\n")

    if pr_number:
        logger.info(f"Polling PR #{pr_number} checks (commit {head_sha[:7]})...")
    else:
        logger.info(f"Polling checks for commit {head_sha[:7]}...")

    previous_status = None
    poll_interval = 10  # seconds

    while True:
        # Get check runs for the original commit directly
        try:
            check_runs_result = subprocess.run(
                ["gh", "api", f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs"],
                capture_output=True,
                text=True,
                check=True,
            )
            check_runs_data = json.loads(check_runs_result.stdout)
            check_runs = check_runs_data.get("check_runs", [])
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
            yield Message("system", f"Error fetching checks: {e}")
            return

        if not check_runs:
            yield Message("system", f"No checks found for commit {head_sha[:7]}")
            return

        # Group by status and conclusion, track failed runs
        status_counts = {
            "success": 0,
            "failure": 0,
            "cancelled": 0,
            "skipped": 0,
            "in_progress": 0,
            "queued": 0,
            "pending": 0,
        }

        failed_runs = []

        for run in check_runs:
            status = run.get("status", "unknown")
            conclusion = run.get("conclusion")

            if status == "completed":
                # Map conclusion to known states, default to success for unmapped
                state = conclusion if conclusion in status_counts else "success"

                # Track failed runs with their IDs
                if state == "failure":
                    run_id = run.get("id")
                    run_name = run.get("name", "Unknown")
                    html_url = run.get("html_url", "")
                    # Extract run ID from URL if available, fallback to id field
                    if html_url and "/runs/" in html_url:
                        actual_run_id = html_url.split("/runs/")[-1].split("/")[0]
                        failed_runs.append((run_name, actual_run_id))
                    elif run_id:
                        # Fallback to using the id field
                        failed_runs.append((run_name, str(run_id)))
            else:
                state = status

            if state in status_counts:
                status_counts[state] += 1

        # Create status summary
        current_status = {
            "total": len(check_runs),
            "in_progress": status_counts["in_progress"]
            + status_counts["queued"]
            + status_counts["pending"],
            "success": status_counts["success"],
            "failure": status_counts["failure"],
            "cancelled": status_counts["cancelled"],
            "skipped": status_counts["skipped"],
        }

        # Show update if status changed
        if current_status != previous_status:
            status_parts = []
            if current_status["success"] > 0:
                status_parts.append(f"✅ {current_status['success']} passed")
            if current_status["failure"] > 0:
                status_parts.append(f"❌ {current_status['failure']} failed")
            if current_status["cancelled"] > 0:
                status_parts.append(f"🚫 {current_status['cancelled']} cancelled")
            if current_status["skipped"] > 0:
                status_parts.append(f"⏭️ {current_status['skipped']} skipped")
            if current_status["in_progress"] > 0:
                status_parts.append(f"🔄 {current_status['in_progress']} in progress")

            yield Message(
                "system",
                f"[{time.strftime('%H:%M:%S')}] {', '.join(status_parts)}\n",
            )
            previous_status = current_status

        # Check if all checks are done
        if current_status["in_progress"] == 0:
            # All checks complete
            if current_status["failure"] > 0:
                failure_msg = f"\n❌ Checks failed: {current_status['failure']} failed, {current_status['success']} passed\n"

                if failed_runs:
                    failure_msg += "\nFailed runs:\n"
                    for name, run_id in failed_runs:
                        failure_msg += f"  - {name} (run {run_id})\n"
                    failure_msg += (
                        "\nView logs with: gh run view <run_id> --log-failed\n"
                    )

                yield Message("system", failure_msg)
            elif current_status["cancelled"] > 0:
                yield Message(
                    "system",
                    f"\n🚫 Checks cancelled: {current_status['cancelled']} cancelled, {current_status['success']} passed\n",
                )
            else:
                yield Message(
                    "system",
                    f"\n✅ All checks passed! ({current_status['success']} checks)\n",
                )
            return

        # Wait before next poll
        time.sleep(poll_interval)


def execute_gh(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Execute GitHub operations."""
    if args and len(args) >= 2 and args[0] == "pr" and args[1] == "status":
        # Quick status check without waiting
        if len(args) > 2:
            url = args[2]
        elif kwargs:
            url = kwargs.get("url", "")
        else:
            yield Message("system", "Error: No PR URL provided")
            return

        # Optional commit SHA
        commit_sha = args[3] if len(args) > 3 else None

        github_info = parse_github_url(url)
        if not github_info:
            yield Message(
                "system",
                f"Error: Invalid GitHub URL: {url}\n\nExpected format: https://github.com/owner/repo/pull/number",
            )
            return

        pr_number = int(github_info["number"])
        owner = github_info["owner"]
        repo = github_info["repo"]

        # Use provided commit or fetch from PR
        head_sha: str | None
        check_runs: list | None
        error: str | None

        if commit_sha:
            head_sha = commit_sha
            try:
                check_runs_result = subprocess.run(
                    [
                        "gh",
                        "api",
                        f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                check_runs_data = json.loads(check_runs_result.stdout)
                check_runs = check_runs_data.get("check_runs", [])
                error = None
            except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
                error = f"Failed to fetch checks: {e}"
                head_sha, check_runs = None, None
        else:
            head_sha, check_runs, error = _get_pr_check_runs(owner, repo, pr_number)

        if error:
            yield Message("system", f"Error: {error}")
            return

        if not check_runs:
            yield Message(
                "system",
                f"No checks found for commit {head_sha[:7] if head_sha else 'unknown'}",
            )
            return

        try:
            # Categorize checks
            status_counts = {
                "success": 0,
                "failure": 0,
                "cancelled": 0,
                "skipped": 0,
                "in_progress": 0,
                "queued": 0,
                "pending": 0,
            }

            failed_runs = []
            in_progress_runs = []

            for run in check_runs:
                status = run.get("status", "unknown")
                conclusion = run.get("conclusion")
                run_name = run.get("name", "Unknown")

                if status == "completed":
                    state = conclusion if conclusion in status_counts else "success"

                    if state == "failure":
                        html_url = run.get("html_url", "")
                        if html_url and "/runs/" in html_url:
                            actual_run_id = html_url.split("/runs/")[-1].split("/")[0]
                            failed_runs.append((run_name, actual_run_id))
                else:
                    state = status
                    if state in ["in_progress", "queued", "pending"]:
                        in_progress_runs.append(run_name)

                if state in status_counts:
                    status_counts[state] += 1

            # Format output
            total = len(check_runs)
            in_progress = (
                status_counts["in_progress"]
                + status_counts["queued"]
                + status_counts["pending"]
            )

            assert head_sha is not None  # Ensured by earlier error check
            output = f"PR #{pr_number} checks ({head_sha[:7]}):\n"
            output += f"Total: {total} checks\n"

            if status_counts["success"] > 0:
                output += f"✅ {status_counts['success']} passed\n"
            if status_counts["failure"] > 0:
                output += f"❌ {status_counts['failure']} failed\n"
            if status_counts["cancelled"] > 0:
                output += f"🚫 {status_counts['cancelled']} cancelled\n"
            if status_counts["skipped"] > 0:
                output += f"⏭️ {status_counts['skipped']} skipped\n"
            if in_progress > 0:
                output += f"🔄 {in_progress} in progress\n"

            if failed_runs:
                output += "\nFailed runs:\n"
                for name, run_id in failed_runs:
                    output += f"  - {name} (run {run_id})\n"
                output += "\nView logs: gh run view <run_id> --log-failed\n"

            if in_progress_runs:
                output += f"\nIn progress: {', '.join(in_progress_runs[:3])}"
                if len(in_progress_runs) > 3:
                    output += f" and {len(in_progress_runs) - 3} more"
                output += "\n"

            yield Message("system", output)

        except (json.JSONDecodeError, KeyError) as e:
            yield Message("system", f"Error: Failed to parse check data: {e}")

    elif args and len(args) >= 2 and args[0] == "pr" and args[1] == "checks":
        # Get PR URL from args or kwargs
        if len(args) > 2:
            url = args[2]
        elif kwargs:
            url = kwargs.get("url", "")
        else:
            yield Message("system", "Error: No PR URL provided")
            return

        # Optional commit SHA
        commit_sha = args[3] if len(args) > 3 else None

        # Wait for checks to complete
        github_info = parse_github_url(url)
        if not github_info:
            yield Message(
                "system",
                f"Error: Invalid GitHub URL: {url}\n\nExpected format: https://github.com/owner/repo/pull/number",
            )
            return

        yield from _wait_for_checks(
            github_info["owner"], github_info["repo"], url, commit_sha=commit_sha
        )

    elif args and len(args) >= 2 and args[0] == "pr" and args[1] == "view":
        # Get PR URL from args or kwargs
        if len(args) > 2:
            url = args[2]
        elif kwargs:
            url = kwargs.get("url", "")
        else:
            yield Message("system", "Error: No PR URL provided")
            return

        # Fetch PR content
        content = get_github_pr_content(url)
        if content:
            yield Message("system", content)
        else:
            # Try to provide helpful error message
            github_info = parse_github_url(url)
            if not github_info:
                yield Message(
                    "system",
                    f"Error: Invalid GitHub URL: {url}\n\nExpected format: https://github.com/owner/repo/pull/number",
                )
            else:
                yield Message(
                    "system",
                    "Error: Failed to fetch PR content. Make sure 'gh' CLI is installed and authenticated.",
                )
    else:
        yield Message(
            "system",
            "Error: Unknown gh command. Available: gh pr view <url>, gh pr status <url>, gh pr checks <url>",
        )


instructions = """Interact with GitHub via the GitHub CLI (gh).

For reading PRs with full context (review comments, code context, suggestions), use:
```gh pr view <pr_url>
```

To get a quick status check of CI checks (with run IDs for failed checks):
```gh pr status <pr_url> [commit_sha]
```

To wait for all CI checks to complete:
```gh pr checks <pr_url> [commit_sha]
```

The optional commit_sha allows checking a specific commit instead of the PR head.
This is useful for checking previous commits without waiting for new builds.

For other operations, use the `shell` tool with the `gh` command."""


def examples(tool_format):
    return f"""
> User: read PR with full context including review comments
> Assistant:
{ToolUse("gh", ["pr", "view", "https://github.com/owner/repo/pull/123"], None).to_output(tool_format)}

> User: check CI status for this PR
> Assistant:
{ToolUse("gh", ["pr", "status", "https://github.com/owner/repo/pull/123"], None).to_output(tool_format)}
> System: PR #123 checks (abc1234):
> System: Total: 6 checks
> System: ✅ 4 passed
> System: ❌ 2 failed
> System:
> System: Failed runs:
> System:   - build (run 12345678)
> System:   - test (run 12345679)
> System:
> System: View logs: gh run view <run_id> --log-failed

> User: check status of specific commit abc1234
> Assistant:
{ToolUse("gh", ["pr", "status", "https://github.com/owner/repo/pull/123", "abc1234"], None).to_output(tool_format)}

> User: show me the failed build logs
> Assistant:
{ToolUse("shell", [], "gh run view 12345678 --log-failed").to_output(tool_format)}

> User: wait for CI checks to complete on a PR
> Assistant:
{ToolUse("gh", ["pr", "checks", "https://github.com/owner/repo/pull/123"], None).to_output(tool_format)}
> System: Waiting for checks on commit abc1234...
> System: [12:34:56] ✅ 4 passed, ❌ 2 failed, 🔄 3 in progress
> System: ...
> System: ❌ Checks failed: 2 failed, 4 passed

> User: create a public repo from the current directory, and push. Note that --confirm and -y are deprecated, and no longer needed.
> Assistant:
{ToolUse("shell", [], '''
REPO=$(basename $(pwd))
gh repo create $REPO --public --source . --push
'''.strip()).to_output(tool_format)}

> User: show issues
> Assistant:
{ToolUse("shell", [], "gh issue list --repo $REPO").to_output(tool_format)}

> User: read issue with comments
> Assistant:
{ToolUse("shell", [], "gh issue view $ISSUE --repo $REPO --comments").to_output(tool_format)}

> User: show recent workflows
> Assistant:
{ToolUse("shell", [], "gh run list --repo $REPO --limit 5").to_output(tool_format)}

> User: show workflow
> Assistant:
{ToolUse("shell", [], "gh run view $RUN --repo $REPO --log").to_output(tool_format)}

> User: wait for workflow to finish
> Assistant:
{ToolUse("shell", [], "gh run watch $RUN --repo $REPO").to_output(tool_format)}
"""


tool: ToolSpec = ToolSpec(
    name="gh",
    available=has_gh_tool(),
    desc="Interact with GitHub",
    instructions=instructions,
    examples=examples,
    execute=execute_gh,
    block_types=["gh"],
    parameters=[
        Parameter(
            name="url",
            type="string",
            description="GitHub PR URL (e.g., https://github.com/owner/repo/pull/123)",
            required=True,
        ),
    ],
)
