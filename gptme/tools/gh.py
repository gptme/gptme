"""GitHub integration tool.

Use native handlers only when they help the assistant succeed more reliably
than a raw ``gh`` command in the shell tool.  The native path is worth it when
it collapses several API calls into one response, keeps CI state structured and
actionable, or adds merge safety guards that are easy to miss in ad-hoc CLI use.

Native operations that materially help:

- ``issue view`` — combines issue body and comments in one call
- ``pr view``    — combines PR body, comments, review-thread resolution, CI,
                    and mergeability in one call
- ``pr status``  — structured check-run summary with actionable run IDs
- ``pr checks``  — polls CI until completion with live progress updates
- ``pr merge``   — squash default, ``--match-head-commit`` guard, auto-merge
- ``run view``   — extracts and structures failed log sections from CI runs

Adding a new native wrapper
---------------------------
Before wrapping a ``gh`` subcommand, ask: "Will this help the assistant do
better than a single ``gh`` command in the shell tool?"  If not, don't add it
— the pass-through already covers it without bloating instructions.

Good candidates combine multiple API calls into one response, add safety
guards, or poll/wait for completion.
"""

import json
import logging
import shlex
import shutil
import subprocess
import time
from collections.abc import Generator

from ..message import Message
from ..util.gh import (
    get_github_issue_content,
    get_github_pr_content,
    get_github_run_logs,
    merge_github_pr,
    parse_github_ref,
    parse_github_url,
)
from . import Parameter, ToolSpec, ToolUse

logger = logging.getLogger(__name__)


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
    max_wait = 30 * 60  # 30 minutes
    start_time = time.time()

    while True:
        if time.time() - start_time > max_wait:
            yield Message(
                "system",
                f"\n⏱️ Timed out after {max_wait // 60} minutes waiting for checks to complete.\n",
            )
            return

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


def _format_check_results(check_runs: list, head_sha: str, pr_number: int) -> str:
    """Format check run results into a human-readable summary."""
    status_counts: dict[str, int] = {
        "success": 0,
        "failure": 0,
        "cancelled": 0,
        "skipped": 0,
        "in_progress": 0,
        "queued": 0,
        "pending": 0,
    }

    failed_runs: list[tuple[str, str]] = []
    in_progress_runs: list[str] = []

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
            if state in ("in_progress", "queued", "pending"):
                in_progress_runs.append(run_name)

        if state in status_counts:
            status_counts[state] += 1

    total = len(check_runs)
    in_progress = (
        status_counts["in_progress"]
        + status_counts["queued"]
        + status_counts["pending"]
    )

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

    return output


def _extract_url(
    args: list[str] | None, kwargs: dict[str, str] | None, arg_offset: int = 2
) -> str | None:
    """Extract a GitHub URL from args or kwargs."""
    if args and len(args) > arg_offset:
        return args[arg_offset]
    if kwargs:
        return kwargs.get("url", None)
    return None


def _resolve_ref(
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    default_type: str,
    entity_name: str = "reference",
) -> tuple[dict[str, str] | None, Message | None]:
    """Extract and parse a GitHub reference from args/kwargs.

    Returns (github_info, error_message). If github_info is None,
    the caller should yield the error message and return.
    """
    ref = _extract_url(args, kwargs)
    if not ref:
        return None, Message("system", f"Error: No {entity_name} provided")

    github_info = parse_github_ref(ref, default_type=default_type)
    if not github_info:
        return None, Message(
            "system",
            f"Error: Could not parse GitHub reference: {ref}\n\n"
            "Accepted formats: URL, owner/repo#N, #N, or N",
        )

    return github_info, None


def _handle_pr_status(
    args: list[str] | None, kwargs: dict[str, str] | None
) -> Generator[Message, None, None]:
    """Handle `gh pr status <ref> [commit_sha]` command."""
    info, err = _resolve_ref(args, kwargs, "pull", "PR reference")
    if err:
        yield err
        return

    assert info is not None
    commit_sha = args[3] if args and len(args) > 3 else None
    pr_number = int(info["number"])
    owner = info["owner"]
    repo = info["repo"]

    if commit_sha:
        try:
            result = subprocess.run(
                ["gh", "api", f"/repos/{owner}/{repo}/commits/{commit_sha}/check-runs"],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(result.stdout)
            head_sha = commit_sha
            check_runs = data.get("check_runs", [])
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
        assert head_sha is not None
        yield Message("system", _format_check_results(check_runs, head_sha, pr_number))
    except (json.JSONDecodeError, KeyError) as e:
        yield Message("system", f"Error: Failed to parse check data: {e}")


def _handle_pr_merge(
    args: list[str] | None, kwargs: dict[str, str] | None
) -> Generator[Message, None, None]:
    """Handle `gh pr merge <ref> [--squash|--rebase|--merge] [--auto] [--delete-branch] [--match-head-commit SHA]`."""
    info, err = _resolve_ref(args, kwargs, "pull", "PR reference")
    if err:
        yield err
        return

    assert info is not None
    pr_number = info["number"]
    owner = info["owner"]
    repo = info["repo"]

    # Parse flags from remaining args (index 3+)
    # Boolean flags (no value): --squash, --rebase, --merge, --auto, --delete-branch
    # Value flags: --match-head-commit SHA
    method = "squash"  # Default
    auto = False
    delete_branch = False
    match_head: str | None = None

    if args:
        i = 3
        while i < len(args):
            arg = args[i]
            if arg in ("--squash", "--rebase", "--merge"):
                method = arg[2:]
            elif arg == "--auto":
                auto = True
            elif arg == "--delete-branch":
                delete_branch = True
            elif arg == "--match-head-commit":
                if i + 1 < len(args):
                    match_head = args[i + 1]
                    i += 1
                else:
                    yield Message(
                        "system", "Error: --match-head-commit requires a SHA value"
                    )
                    return
            i += 1

    result = merge_github_pr(
        owner,
        repo,
        pr_number,
        method=method,
        auto=auto,
        delete_branch=delete_branch,
        match_head_commit=match_head,
    )

    if result["success"]:
        output = str(result["message"])
        if "sha" in result:
            output += f"\nMerge commit: {result['sha']}"
        yield Message("system", output)
    else:
        yield Message("system", f"Error: {result['message']}")


def _passthrough_gh(
    args: list[str] | None, code: str | None
) -> Generator[Message, None, None]:
    """Pass unrecognized commands through to the gh CLI."""
    if args:
        cmd = ["gh"] + list(args)
    elif code:
        cmd_str = code.strip().removeprefix("gh ")
        try:
            cmd = ["gh"] + shlex.split(cmd_str)
        except ValueError as e:
            yield Message("system", f"Error parsing command: {e}")
            return
    else:
        yield Message("system", "Error: No command provided")
        return

    logger.info("gh pass-through: %s", shlex.join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False
        )
        output = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            details = []
            if stderr:
                details.append(f"stderr:\n{stderr}")
            if output:
                details.append(f"stdout:\n{output}")
            msg = f"Error (exit {result.returncode})"
            if details:
                msg += "\n\n" + "\n\n".join(details)
            yield Message("system", msg)
        elif output:
            yield Message("system", output)
        else:
            yield Message("system", "(no output)")
    except subprocess.TimeoutExpired:
        yield Message("system", "Error: Command timed out after 60 seconds")
    except FileNotFoundError:
        yield Message(
            "system",
            "Error: gh CLI not found. Install from https://cli.github.com/",
        )


def execute_gh(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Execute GitHub operations.

    Native handlers for high-value operations (issue view, pr view/status/checks/merge,
    run view).  Everything else passes through to the gh CLI unchanged.
    """
    if args and len(args) >= 2 and args[0] == "pr" and args[1] == "merge":
        yield from _handle_pr_merge(args, kwargs)

    elif args and len(args) >= 2 and args[0] == "pr" and args[1] == "status":
        yield from _handle_pr_status(args, kwargs)

    elif args and len(args) >= 2 and args[0] == "pr" and args[1] == "checks":
        info, err = _resolve_ref(args, kwargs, "pull", "PR reference")
        if err:
            yield err
            return

        assert info is not None
        commit_sha = args[3] if args and len(args) > 3 else None
        url = f"https://github.com/{info['owner']}/{info['repo']}/pull/{info['number']}"
        yield from _wait_for_checks(
            info["owner"], info["repo"], url, commit_sha=commit_sha
        )

    elif args and len(args) >= 2 and args[0] == "pr" and args[1] == "view":
        info, err = _resolve_ref(args, kwargs, "pull", "PR reference")
        if err:
            yield err
            return

        assert info is not None
        if info["type"] != "pull":
            yield Message(
                "system",
                f"Error: Reference is not a GitHub PR (got {info['type']}). Use `gh issue view` for issues.",
            )
            return

        url = f"https://github.com/{info['owner']}/{info['repo']}/pull/{info['number']}"
        content = get_github_pr_content(url)
        if content:
            yield Message("system", content)
        else:
            yield Message(
                "system",
                "Error: Failed to fetch PR content. Make sure 'gh' CLI is installed and authenticated.",
            )

    elif args and len(args) >= 2 and args[0] == "issue" and args[1] == "view":
        info, err = _resolve_ref(args, kwargs, "issue", "issue reference")
        if err:
            yield err
            return

        assert info is not None
        if info["type"] == "pull":
            yield Message(
                "system",
                f"Error: Reference is not a GitHub issue (got {info['type']}). Use `gh pr view` for pull requests.",
            )
            return

        content = get_github_issue_content(info["owner"], info["repo"], info["number"])
        if content:
            yield Message("system", content)
        else:
            yield Message(
                "system",
                "Error: Failed to fetch issue content. Make sure 'gh' CLI is installed and authenticated.",
            )

    elif args and len(args) >= 2 and args[0] == "run" and args[1] == "view":
        if len(args) < 3:
            yield Message(
                "system", "Error: No run ID provided. Usage: gh run view <run-id>"
            )
            return

        run_id = args[2]
        if not run_id.isdigit():
            yield Message(
                "system",
                f"Error: Invalid run ID '{run_id}'. Run IDs are numeric (e.g. 12345678).",
            )
            return

        content = get_github_run_logs(run_id)
        if content:
            yield Message("system", content)
        else:
            yield Message(
                "system",
                f"Error: Failed to fetch run {run_id}. Make sure 'gh' CLI is installed and authenticated.",
            )

    else:
        # Pass through to gh CLI for all other commands
        yield from _passthrough_gh(args, code)


instructions = """Use this tool when GitHub work needs fewer round-trips, structured CI data,
or safer merges than a raw `gh` shell command.

Refs: full URLs, `owner/repo#N`, `#N`, or bare `N` in a git repo.

Native paths help the agent finish GitHub tasks with less hallucination risk:
- `gh issue view <ref>` gets issue body and comments in one result
- `gh pr view <ref>` gets PR body, comments, review threads, CI, and mergeability in one result
- `gh pr status <ref> [commit_sha]` returns structured CI state with run IDs
- `gh pr checks <ref> [commit_sha]` waits for checks to settle
- `gh pr merge <ref> ...` adds squash-by-default and optional head-commit protection
- `gh run view <run-id>` extracts failed-job logs

All other valid `gh` subcommands pass through unchanged."""


def examples(tool_format):
    return f"""
> User: read PR #123 on owner/repo
> Assistant:
{ToolUse("gh", ["pr", "view", "owner/repo#123"], None).to_output(tool_format)}

> User: check CI status for this PR
> Assistant:
{
        ToolUse(
            "gh", ["pr", "status", "https://github.com/owner/repo/pull/123"], None
        ).to_output(tool_format)
    }
> System:
PR #123 checks (abc1234):
Total: 6 checks
✅ 4 passed
❌ 2 failed

Failed runs:
  - build (run 12345678)
  - test (run 12345679)

View logs: gh run view <run_id> --log-failed

> User: show me the failed build logs
> Assistant:
{ToolUse("gh", ["run", "view", "12345678"], None).to_output(tool_format)}

> User: wait for CI checks to complete on a PR
> Assistant:
{
        ToolUse(
            "gh", ["pr", "checks", "https://github.com/owner/repo/pull/123"], None
        ).to_output(tool_format)
    }

> User: merge PR #123 on owner/repo
> Assistant:
{ToolUse("gh", ["pr", "merge", "owner/repo#123"], None).to_output(tool_format)}

> User: auto-merge PR when checks pass, and delete the branch
> Assistant:
{
        ToolUse(
            "gh",
            ["pr", "merge", "owner/repo#123", "--squash", "--auto", "--delete-branch"],
            None,
        ).to_output(tool_format)
    }

> User: read issue #42 on owner/repo
> Assistant:
{ToolUse("gh", ["issue", "view", "owner/repo#42"], None).to_output(tool_format)}

> User: show issues (pass-through to gh CLI)
> Assistant:
{ToolUse("gh", ["issue", "list", "--repo", "owner/repo"], None).to_output(tool_format)}

> User: post a multi-line comment on issue 42
> Assistant:
{
        ToolUse(
            "shell",
            [],
            '''gh issue comment 42 --repo owner/repo --body-file - << 'EOF'
## Summary

Work is complete. Here are the details:
- Fixed the bug
- Added tests

See PR #123 for the implementation.
EOF''',
        ).to_output(tool_format)
    }
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
            description="GitHub reference: URL, owner/repo#N, #N, or bare number",
            required=True,
        ),
    ],
)
