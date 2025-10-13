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


def _wait_for_checks(owner: str, repo: str, url: str) -> Generator[Message, None, None]:
    """Wait for all GitHub Actions checks to complete on a PR."""
    import logging

    logger = logging.getLogger(__name__)

    # Get PR details to extract HEAD commit SHA
    pr_number = parse_github_url(url)
    if not pr_number:
        yield Message("system", "Error: Could not parse PR number from URL")
        return

    try:
        pr_details_result = subprocess.run(
            ["gh", "api", f"/repos/{owner}/{repo}/pulls/{pr_number['number']}"],
            capture_output=True,
            text=True,
            check=True,
        )
        pr_details = json.loads(pr_details_result.stdout)
        head_sha = pr_details.get("head", {}).get("sha")

        if not head_sha:
            yield Message("system", "Error: Could not get HEAD commit SHA")
            return

        yield Message("system", f"Waiting for checks on commit {head_sha[:7]}...\n")

        previous_status = None
        poll_interval = 10  # seconds

        while True:
            # Get check runs for the commit
            check_runs_result = subprocess.run(
                ["gh", "api", f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs"],
                capture_output=True,
                text=True,
                check=False,
            )

            if check_runs_result.returncode != 0:
                yield Message("system", "Error: Could not fetch check runs")
                return

            check_runs_data = json.loads(check_runs_result.stdout)
            check_runs = check_runs_data.get("check_runs", [])

            if not check_runs:
                yield Message("system", "No checks found for this PR")
                return

            # Group by status and conclusion
            status_counts = {
                "success": 0,
                "failure": 0,
                "cancelled": 0,
                "skipped": 0,
                "in_progress": 0,
                "queued": 0,
                "pending": 0,
            }

            for run in check_runs:
                status = run.get("status", "unknown")
                conclusion = run.get("conclusion")

                if status == "completed":
                    state = conclusion or "completed"
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
                    status_parts.append(
                        f"🔄 {current_status['in_progress']} in progress"
                    )

                yield Message(
                    "system",
                    f"[{time.strftime('%H:%M:%S')}] {', '.join(status_parts)}\n",
                )
                previous_status = current_status

            # Check if all checks are done
            if current_status["in_progress"] == 0:
                # All checks complete
                if current_status["failure"] > 0:
                    yield Message(
                        "system",
                        f"\n❌ Checks failed: {current_status['failure']} failed, {current_status['success']} passed\n",
                    )
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

    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to wait for checks: {e}")
        yield Message("system", f"Error: Failed to fetch check status: {e}")
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Failed to parse check data: {e}")
        yield Message("system", f"Error: Failed to parse check data: {e}")


def execute_gh(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Execute GitHub operations."""
    if args and len(args) >= 2 and args[0] == "pr" and args[1] == "checks":
        # Get PR URL from args or kwargs
        if len(args) > 2:
            url = args[2]
        elif kwargs:
            url = kwargs.get("url", "")
        else:
            yield Message("system", "Error: No PR URL provided")
            return

        # Wait for checks to complete
        github_info = parse_github_url(url)
        if not github_info:
            yield Message(
                "system",
                f"Error: Invalid GitHub URL: {url}\n\nExpected format: https://github.com/owner/repo/pull/number",
            )
            return

        yield from _wait_for_checks(github_info["owner"], github_info["repo"], url)

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
            "Error: Unknown gh command. Available: gh pr view <url>, gh pr checks <url>",
        )


instructions = """Interact with GitHub via the GitHub CLI (gh).

For reading PRs with full context (review comments, code context, suggestions), use:
```gh pr view <pr_url>
```

To wait for all CI checks to complete on a PR:
```gh pr checks <pr_url>
```

For other operations, use the `shell` tool with the `gh` command."""


def examples(tool_format):
    return f"""
> User: read PR with full context including review comments
> Assistant:
{ToolUse("gh", ["pr", "view", "https://github.com/owner/repo/pull/123"], None).to_output(tool_format)}

> User: wait for CI checks to complete on a PR
> Assistant:
{ToolUse("gh", ["pr", "checks", "https://github.com/owner/repo/pull/123"], None).to_output(tool_format)}

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
            type="str",
            description="GitHub PR URL (e.g., https://github.com/owner/repo/pull/123)",
            required=True,
        ),
    ],
)
