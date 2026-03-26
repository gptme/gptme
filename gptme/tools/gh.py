import json
import shutil
import subprocess
import time
from collections.abc import Generator

from ..message import Message
from ..util.gh import (
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
from . import Parameter, ToolSpec, ToolUse


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


def _parse_list_flags(args: list[str], start: int = 2) -> dict[str, str]:
    """Parse --flag value pairs from args starting at given index."""
    flags: dict[str, str] = {}
    i = start
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            key = args[i][2:]  # Strip --
            flags[key] = args[i + 1]
            i += 2
        else:
            i += 1
    return flags


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


def _resolve_repo_for_list(
    args: list[str],
) -> tuple[str | None, str | None, dict[str, str], Message | None]:
    """Parse repo and flags for list commands.

    Returns (owner, repo, flags, error_message). If owner is None,
    the caller should yield the error message and return.
    """
    flags = _parse_list_flags(args)
    owner: str | None = None
    repo: str | None = None
    repo_flag = flags.get("repo")
    if repo_flag and "/" in repo_flag:
        owner, repo = repo_flag.split("/", 1)
    else:
        repo_info = _get_repo_from_git_remote()
        if repo_info:
            owner, repo = repo_info

    if not owner or not repo:
        return (
            None,
            None,
            flags,
            Message(
                "system",
                "Error: Could not determine repository. Use --repo owner/repo or run from a git repo.",
            ),
        )

    return owner, repo, flags, None


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


def _parse_flags(args: list[str], start: int = 2) -> tuple[list[str], dict[str, str]]:
    """Parse positional args and --flag value pairs from args starting at index."""
    positional: list[str] = []
    flags: dict[str, str] = {}
    i = start
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            flag_name = arg[2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                flags[flag_name] = args[i + 1]
                i += 2
            else:
                # Boolean flag
                flags[flag_name] = "true"
                i += 1
        else:
            positional.append(arg)
            i += 1
    return positional, flags


def _handle_issue_create(
    args: list[str],
) -> Generator[Message, None, None]:
    """Handle `gh issue create --repo owner/repo --title "..." --body "..." [--label ...]`."""
    _, flags = _parse_flags(args, start=2)

    # Resolve repo
    repo_flag = flags.get("repo", "")
    owner, repo = "", ""
    if repo_flag:
        if "/" not in repo_flag:
            yield Message(
                "system",
                "Error: --repo must be in owner/repo format.",
            )
            return
        owner, repo = repo_flag.split("/", 1)
    else:
        repo_info = _get_repo_from_git_remote()
        if repo_info:
            owner, repo = repo_info

    if not owner or not repo:
        yield Message(
            "system",
            "Error: Could not determine repository. Use --repo owner/repo or run from a git repo.",
        )
        return

    title = flags.get("title", "")
    if not title:
        yield Message(
            "system",
            'Error: --title is required. Usage: gh issue create --repo owner/repo --title "Title" [--body "Body"] [--label label1,label2] [--assignee user]',
        )
        return

    body = flags.get("body", "")
    labels = flags["label"].split(",") if flags.get("label") else None
    assignees = flags["assignee"].split(",") if flags.get("assignee") else None

    result = create_github_issue(
        owner, repo, title, body, labels=labels, assignees=assignees
    )
    if result["success"]:
        yield Message("system", f"✓ {result['message']}\n{result['url']}")
    else:
        yield Message("system", f"Error: {result['message']}")


def _handle_comment(
    args: list[str],
) -> Generator[Message, None, None]:
    """Handle `gh issue comment <ref> --body "..."` and `gh pr comment <ref> --body "..."`."""
    kind = args[0]  # "issue" or "pr"
    ref_type = "issues" if kind == "issue" else "pull"

    # Need at least: gh issue/pr comment <ref> --body "..."
    if len(args) < 3 or args[2].startswith("--"):
        yield Message(
            "system",
            f'Error: Missing reference. Usage: gh {kind} comment <owner/repo#N> --body "Comment text"',
        )
        return

    # The reference is the next positional arg after "comment"
    ref_str = args[2]

    # Parse remaining args for --body and other flags
    _, flags = _parse_flags(args, start=3)

    body = flags.get("body", "")
    if not body:
        yield Message(
            "system",
            f'Error: --body is required. Usage: gh {kind} comment <owner/repo#N> --body "Comment text"',
        )
        return

    # Parse the reference to get owner/repo/number
    info = parse_github_ref(ref_str, default_type=ref_type)
    if not info:
        yield Message(
            "system",
            f"Error: Could not parse reference '{ref_str}'. Use owner/repo#N, #N, or a full URL.",
        )
        return

    result = comment_on_github(
        info["owner"], info["repo"], int(info["number"]), body, kind=kind
    )
    if result["success"]:
        msg = str(result["message"])
        url = result.get("url", "")
        if url:
            msg += f"\n{url}"
        yield Message("system", f"✓ {msg}")
    else:
        yield Message("system", f"Error: {result['message']}")


def execute_gh(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Execute GitHub operations."""
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

    elif args and len(args) >= 2 and args[0] == "pr" and args[1] == "diff":
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

        content = get_github_pr_diff(info["owner"], info["repo"], info["number"])
        if content:
            yield Message("system", content)
        else:
            yield Message(
                "system",
                "Error: Failed to fetch PR diff. Make sure 'gh' CLI is installed and authenticated.",
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

    elif args and len(args) >= 2 and args[0] == "issue" and args[1] == "list":
        owner, repo, flags, err = _resolve_repo_for_list(args)
        if err:
            yield err
            return

        assert owner is not None and repo is not None
        state = flags.get("state", "open")
        limit_str = flags.get("limit", "20")
        limit = int(limit_str) if limit_str.isdecimal() else 20
        labels = flags.get("label", "").split(",") if flags.get("label") else None

        content = get_github_issue_list(
            owner, repo, state=state, labels=labels, limit=limit
        )
        if content:
            yield Message("system", content)
        else:
            yield Message(
                "system",
                f"Error: Failed to list issues for {owner}/{repo}.",
            )

    elif args and len(args) >= 2 and args[0] == "pr" and args[1] == "list":
        owner, repo, flags, err = _resolve_repo_for_list(args)
        if err:
            yield err
            return

        assert owner is not None and repo is not None
        state = flags.get("state", "open")
        limit_str = flags.get("limit", "20")
        limit = int(limit_str) if limit_str.isdecimal() else 20

        content = get_github_pr_list(owner, repo, state=state, limit=limit)
        if content:
            yield Message("system", content)
        else:
            yield Message(
                "system",
                f"Error: Failed to list pull requests for {owner}/{repo}.",
            )

    elif args and len(args) >= 2 and args[0] == "issue" and args[1] == "view":
        info, err = _resolve_ref(args, kwargs, "issues", "issue reference")
        if err:
            yield err
            return

        assert info is not None
        if info["type"] != "issues":
            yield Message(
                "system",
                f"Error: URL is not a GitHub issue URL (got {info['type']}). Use `gh pr view` for pull requests.",
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

    elif (
        args and len(args) >= 2 and args[0] == "search" and args[1] in ("issues", "prs")
    ):
        search_type = args[1]
        # Parse query and search_flags from remaining args
        query_parts: list[str] = []
        search_flags: dict[str, str] = {}
        i = 2
        while i < len(args):
            arg = args[i]
            if arg.startswith("--"):
                if i + 1 >= len(args) or args[i + 1].startswith("--"):
                    yield Message(
                        "system",
                        f"Error: Flag {arg} requires a value. Usage: gh search {search_type} <query> [--repo owner/repo] [--state open|closed] [--author user] [--label name] [--limit N]",
                    )
                    return
                flag_name = arg[2:]
                search_flags[flag_name] = args[i + 1]
                i += 2
                continue

            query_parts.append(arg)
            i += 1

        query = " ".join(query_parts)
        if not query:
            yield Message(
                "system",
                f"Error: No search query provided. Usage: gh search {search_type} <query> [--repo owner/repo] [--state open|closed] [--author user] [--label name] [--limit N]",
            )
            return

        limit_str = search_flags.get("limit", "20")
        if not limit_str.isdecimal():
            yield Message(
                "system",
                f"Error: --limit requires a positive integer, got {limit_str!r}.",
            )
            return
        limit = int(limit_str)

        if search_type == "prs" and search_flags.get("assignee"):
            yield Message(
                "system",
                "Error: --assignee is not supported for PR search (GitHub API limitation). Use --author to filter by PR author.",
            )
            return

        if search_type == "issues":
            content = search_github_issues(
                query,
                repo=search_flags.get("repo"),
                state=search_flags.get("state"),
                author=search_flags.get("author"),
                assignee=search_flags.get("assignee"),
                label=search_flags.get("label"),
                limit=limit,
            )
        else:
            content = search_github_prs(
                query,
                repo=search_flags.get("repo"),
                state=search_flags.get("state"),
                author=search_flags.get("author"),
                label=search_flags.get("label"),
                limit=limit,
            )

        if content:
            yield Message("system", content)
        else:
            yield Message(
                "system",
                f"Error: Failed to search {search_type}. Make sure 'gh' CLI is installed and authenticated.",
            )

    elif args and len(args) >= 2 and args[0] == "issue" and args[1] == "create":
        yield from _handle_issue_create(args)

    elif (
        args and len(args) >= 2 and args[0] in ("issue", "pr") and args[1] == "comment"
    ):
        yield from _handle_comment(args)

    else:
        yield Message(
            "system",
            "Error: Unknown gh command. Available: gh issue create, gh issue list, gh issue view, gh issue comment, gh pr list, gh pr view, gh pr diff, gh pr merge, gh pr comment, gh pr status, gh pr checks, gh run view, gh search issues, gh search prs\n\nReferences can be URLs, owner/repo#N, #N, or bare numbers.",
        )


instructions = """Interact with GitHub via the GitHub CLI (gh).

Refs: full URLs, `owner/repo#N`, `#N`, or bare `N` (when in a git repo).

Create/read issues and PRs:
```gh issue create --repo owner/repo --title "Title" --body "Details"
gh issue view owner/repo#42
gh pr view owner/repo#123
```

List issues/PRs:
```gh issue list --repo owner/repo --state open --limit 20
gh pr list --repo owner/repo --state open --limit 20
```

Search issues/PRs across repos:
```gh search issues "query" --repo owner/repo --state open
gh search prs "query" --author username --state open
```

Comment on issues/PRs:
```gh issue comment owner/repo#42 --body "Comment"
gh pr comment owner/repo#123 --body "LGTM"
```

Inspect code changes:
```gh pr diff owner/repo#123
```

Merge a pull request (default: squash):
```gh pr merge owner/repo#123 --squash --auto --delete-branch
```

CI status:
```gh pr checks <ref>
gh run view <run-id>
```

For other operations, use the `shell` tool with `gh`."""


def examples(tool_format):
    return f"""
> User: read issue #42 on owner/repo
> Assistant:
{ToolUse("gh", ["issue", "view", "owner/repo#42"], None).to_output(tool_format)}

> User: read PR #123 on owner/repo
> Assistant:
{ToolUse("gh", ["pr", "view", "owner/repo#123"], None).to_output(tool_format)}

> User: show the code changes in this PR
> Assistant:
{ToolUse("gh", ["pr", "diff", "owner/repo#123"], None).to_output(tool_format)}

> User: check CI status for this PR
> Assistant:
{
        ToolUse(
            "gh", ["pr", "status", "https://github.com/owner/repo/pull/123"], None
        ).to_output(tool_format)
    }
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
{
        ToolUse(
            "gh",
            ["pr", "status", "https://github.com/owner/repo/pull/123", "abc1234"],
            None,
        ).to_output(tool_format)
    }

> User: show me the failed build logs
> Assistant:
{ToolUse("gh", ["run", "view", "12345678"], None).to_output(tool_format)}
> System: ## Run 12345678: Fix auth flow
> System:
> System: **Workflow**: CI
> System: **Branch**: fix/auth
> System: **Status**: completed (failure)
> System:
> System: ### Jobs
> System:   ✅ lint: success
> System:   ❌ test: failure
> System:
> System: ### Failed Job Logs
> System:
> System: #### test
> System:   ❌ Failed step: Run tests
> System:
> System: [extracted error sections with context]

> User: wait for CI checks to complete on a PR
> Assistant:
{
        ToolUse(
            "gh", ["pr", "checks", "https://github.com/owner/repo/pull/123"], None
        ).to_output(tool_format)
    }
> System: Waiting for checks on commit abc1234...
> System: [12:34:56] ✅ 4 passed, ❌ 2 failed, 🔄 3 in progress
> System: ...
> System: ❌ Checks failed: 2 failed, 4 passed

> User: merge PR #123 on owner/repo
> Assistant:
{ToolUse("gh", ["pr", "merge", "owner/repo#123"], None).to_output(tool_format)}
> System: ✓ Squashed and merged pull request #123
> System: Merge commit: abc1234def5678

> User: auto-merge PR when checks pass, and delete the branch
> Assistant:
{
        ToolUse(
            "gh",
            ["pr", "merge", "owner/repo#123", "--squash", "--auto", "--delete-branch"],
            None,
        ).to_output(tool_format)
    }
> System: ✓ Pull request #123 will be automatically merged via squash when all checks pass

> User: create a public repo from the current directory, and push. Note that --confirm and -y are deprecated, and no longer needed.
> Assistant:
{
        ToolUse(
            "shell",
            [],
            '''
REPO=$(basename $(pwd))
gh repo create $REPO --public --source . --push
'''.strip(),
        ).to_output(tool_format)
    }

> User: create an issue to track this bug
> Assistant:
{
        ToolUse(
            "gh",
            [
                "issue",
                "create",
                "--repo",
                "owner/repo",
                "--title",
                "Fix login timeout",
                "--body",
                "Login times out after 30s on slow connections.",
                "--label",
                "bug",
            ],
            None,
        ).to_output(tool_format)
    }
> System: ✓ Created issue #42: Fix login timeout
> System: https://github.com/owner/repo/issues/42

> User: comment on issue 42 that the fix is ready
> Assistant:
{
        ToolUse(
            "gh",
            [
                "issue",
                "comment",
                "owner/repo#42",
                "--body",
                "Fix implemented in PR #50. Ready for review.",
            ],
            None,
        ).to_output(tool_format)
    }
> System: ✓ Commented on issue #42

> User: leave a review comment on PR 123
> Assistant:
{
        ToolUse(
            "gh",
            [
                "pr",
                "comment",
                "owner/repo#123",
                "--body",
                "LGTM! Tests pass and code looks clean.",
            ],
            None,
        ).to_output(tool_format)
    }
> System: ✓ Commented on pr #123

> User: show issues
> Assistant:
{ToolUse("gh", ["issue", "list", "--repo", "owner/repo"], None).to_output(tool_format)}

> User: show open PRs
> Assistant:
{ToolUse("gh", ["pr", "list", "--repo", "owner/repo"], None).to_output(tool_format)}

> User: search for authentication issues in owner/repo
> Assistant:
{
        ToolUse(
            "gh", ["search", "issues", "authentication", "--repo", "owner/repo"], None
        ).to_output(tool_format)
    }

> User: find my open PRs
> Assistant:
{
        ToolUse(
            "gh", ["search", "prs", "fix", "--author", "@me", "--state", "open"], None
        ).to_output(tool_format)
    }

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
            description="GitHub reference: URL, owner/repo#N, #N, or bare number",
            required=True,
        ),
    ],
)
