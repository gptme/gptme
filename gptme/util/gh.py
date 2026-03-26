"""
Fetches GitHub issue or PR content using the `gh` CLI tool, including essential info like comments and reviews.

Originally prototyped as scripts/gh-pr-view-with-pr-comments.py (since removed)
"""

import json
import logging
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path

logger = logging.getLogger(__name__)

# Default threshold for truncating long comment bodies
# Based on empirical sampling: human comments typically <500 tokens,
# verbose bot comments (Greptile, Ellipsis) can reach 900+ tokens
DEFAULT_TRUNCATE_TOKENS = 1000


def _truncate_body(body: str, max_tokens: int = DEFAULT_TRUNCATE_TOKENS) -> str:
    """
    Truncate comment body if it exceeds token threshold.

    Preserves beginning and end of content, truncating the middle.
    Uses chars/4 as token estimate (conservative for most content).

    Args:
        body: Comment body text
        max_tokens: Maximum tokens to allow (default: 1000)

    Returns:
        Original body if within limit, or truncated with indicator
    """
    if not body:
        return body

    # Estimate tokens (chars/4 is conservative estimate)
    max_chars = max_tokens * 4
    if len(body) <= max_chars:
        return body

    # Reserve space for indicator message (format: "\n\n[... truncated X chars (Y tokens) ...]\n\n")
    # Max indicator length: ~70 chars (accounts for large numbers)
    indicator_overhead = 70

    # Calculate how much content we can keep within budget
    available_for_content = max_chars - indicator_overhead
    if available_for_content <= 0:
        # Edge case: max_chars too small for meaningful truncation
        return body[:max_chars]

    keep_chars = available_for_content // 2
    truncated_chars = len(body) - (keep_chars * 2)

    return (
        f"{body[:keep_chars]}\n\n"
        f"[... truncated {truncated_chars} chars ({truncated_chars // 4} tokens) ...]\n\n"
        f"{body[-keep_chars:]}"
    )


def _get_github_actions_status(owner: str, repo: str, sha: str) -> str | None:
    """Get GitHub Actions status for a commit SHA."""
    try:
        # Get check runs for the commit
        check_runs_result = subprocess.run(
            ["gh", "api", f"/repos/{owner}/{repo}/commits/{sha}/check-runs"],
            capture_output=True,
            text=True,
            check=False,
        )

        if check_runs_result.returncode != 0:
            return None

        check_runs_data = json.loads(check_runs_result.stdout)
        check_runs = check_runs_data.get("check_runs", [])

        if not check_runs:
            return None

        # Group by status and conclusion
        status_summary: dict[str, list[str]] = {}
        for run in check_runs:
            name = run.get("name", "Unknown")
            status = run.get("status", "unknown")
            conclusion = run.get("conclusion")

            # Determine overall state
            if status == "completed":
                state = conclusion or "completed"
            else:
                state = status

            if state not in status_summary:
                status_summary[state] = []
            status_summary[state].append(name)

        # Format the summary
        lines = []

        # Order by importance: failure, cancelled, action_required, neutral, success, etc.
        priority_order = [
            "failure",
            "cancelled",
            "action_required",
            "timed_out",
            "neutral",
            "success",
            "skipped",
            "in_progress",
            "queued",
            "pending",
        ]

        for state in priority_order:
            if state in status_summary:
                emoji = {
                    "success": "✅",
                    "failure": "❌",
                    "cancelled": "🚫",
                    "action_required": "⚠️",
                    "timed_out": "⏰",
                    "neutral": "⚪",
                    "skipped": "⏭️",
                    "in_progress": "🔄",
                    "queued": "⏳",
                    "pending": "⏳",
                }.get(state, "❓")

                checks = status_summary[state]
                if len(checks) == 1:
                    lines.append(f"{emoji} {checks[0]}: {state}")
                else:
                    lines.append(
                        f"{emoji} {len(checks)} checks {state}: {', '.join(checks)}"
                    )

        # Add any remaining states not in priority order
        for state, checks in status_summary.items():
            if state not in priority_order:
                if len(checks) == 1:
                    lines.append(f"❓ {checks[0]}: {state}")
                else:
                    lines.append(
                        f"❓ {len(checks)} checks {state}: {', '.join(checks)}"
                    )

        return "\n".join(lines) if lines else None

    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
        logger.debug(f"Failed to get GitHub Actions status: {e}")
        return None


def _get_repo_from_git_remote(
    workspace: Path | None = None,
) -> tuple[str, str] | None:
    """Detect owner/repo from the git remote 'origin' URL.

    Returns:
        Tuple of (owner, repo) or None if detection fails.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(workspace) if workspace else None,
        )
        remote_url = result.stdout.strip()

        # Handle SSH format: git@github.com:owner/repo.git
        if remote_url.startswith("git@github.com:"):
            path = remote_url[len("git@github.com:") :]
            path = path.removesuffix(".git")
            parts = path.split("/")
            if len(parts) == 2:
                return parts[0], parts[1]

        # Handle HTTPS format: https://github.com/owner/repo.git
        parsed = urllib.parse.urlparse(remote_url)
        if parsed.netloc == "github.com":
            path_parts = parsed.path.strip("/").removesuffix(".git").split("/")
            if len(path_parts) == 2:
                return path_parts[0], path_parts[1]
    except (subprocess.CalledProcessError, OSError, ValueError, AttributeError):
        pass
    return None


def parse_github_ref(
    ref: str,
    workspace: Path | None = None,
    default_type: str = "issues",
) -> dict[str, str] | None:
    """Parse a GitHub reference into owner, repo, type, and number.

    Supports multiple formats:
    - Full URL: https://github.com/owner/repo/issues/42
    - Short ref: owner/repo#42
    - Bare number with repo context: #42 or 42 (requires workspace with git remote)

    Args:
        ref: The GitHub reference string to parse.
        workspace: Optional workspace path for inferring owner/repo from git remote.
        default_type: Default type when not inferable ('issues' or 'pull').

    Returns:
        Dict with 'owner', 'repo', 'type' ('issues' or 'pull'), 'number'
        or None if the reference could not be parsed.
    """
    ref = ref.strip()

    # Try full URL first
    result = parse_github_url(ref)
    if result:
        return result

    # Try owner/repo#number format
    match = re.match(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+)$", ref)
    if match:
        return {
            "owner": match.group(1),
            "repo": match.group(2),
            "type": default_type,
            "number": match.group(3),
        }

    # Try #number or bare number format (needs workspace context)
    match = re.match(r"^#?(\d+)$", ref)
    if match:
        repo_info = _get_repo_from_git_remote(workspace)
        if repo_info:
            return {
                "owner": repo_info[0],
                "repo": repo_info[1],
                "type": default_type,
                "number": match.group(1),
            }

    return None


def parse_github_url(url: str) -> dict[str, str] | None:
    """
    Parse GitHub issue/PR URLs and return owner, repo, type, and number.

    Returns:
        Dict with 'owner', 'repo', 'type' ('issues' or 'pull'), 'number'
        or None if not a GitHub issue/PR URL
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc != "github.com":
            return None

        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) >= 4 and path_parts[2] in ["issues", "pull"]:
            return {
                "owner": path_parts[0],
                "repo": path_parts[1],
                "type": path_parts[2],
                "number": path_parts[3],
            }
    except (ValueError, AttributeError):
        pass
    return None


def transform_github_url(url: str) -> str:
    """
    Transform GitHub blob URLs to raw URLs to get file content without UI.

    Transforms:
    https://github.com/{owner}/{repo}/blob/{branch}/{path}
    to:
    https://github.com/{owner}/{repo}/raw/refs/heads/{branch}/{path}
    """
    if "/blob/" in url and "github.com" in url:
        return url.replace("/blob/", "/raw/refs/heads/")
    return url


DEFAULT_DIFF_MAX_TOKENS = 4000


def get_github_pr_diff(
    owner: str, repo: str, number: str, max_tokens: int = DEFAULT_DIFF_MAX_TOKENS
) -> str | None:
    """Get GitHub PR diff with stat summary, truncated to fit token budget.

    Returns a formatted string with:
    1. A diffstat summary (always shown in full)
    2. The full unified diff, truncated if it exceeds the token budget

    Args:
        owner: Repository owner
        repo: Repository name
        number: PR number
        max_tokens: Maximum tokens for the diff body (default: 4000)
    """
    if not shutil.which("gh"):
        logger.debug("gh CLI not available for GitHub PR diff")
        return None

    repo_ref = f"{owner}/{repo}"

    try:
        # Get stat summary (compact, always shown in full)
        stat_result = subprocess.run(
            ["gh", "pr", "diff", number, "--repo", repo_ref, "--stat"],
            capture_output=True,
            text=True,
            check=True,
        )

        # Get full unified diff
        diff_result = subprocess.run(
            ["gh", "pr", "diff", number, "--repo", repo_ref],
            capture_output=True,
            text=True,
            check=True,
        )

        stat_text = stat_result.stdout.strip()
        diff_text = diff_result.stdout

        if not diff_text.strip():
            return f"PR #{number} diff:\n\nNo changes."

        # Build output: stat header + diff body
        output = f"PR #{number} diff:\n\n"
        if stat_text:
            output += f"{stat_text}\n\n"

        # Truncate diff if needed (token estimate: chars/4)
        max_chars = max_tokens * 4
        if len(diff_text) <= max_chars:
            output += diff_text
        else:
            # Truncate at last newline boundary to avoid mid-line cuts
            cut = diff_text.rfind("\n", 0, max_chars)
            cut = cut + 1 if cut != -1 else max_chars
            truncated_chars = len(diff_text) - cut
            truncated_tokens = truncated_chars // 4
            output += diff_text[:cut]
            output += (
                f"\n\n[... diff truncated: {truncated_chars} chars "
                f"(~{truncated_tokens} tokens) omitted — "
                f"use shell `gh pr diff {number} --repo {repo_ref}` for full diff]\n"
            )

        return output
    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to get PR diff: {e}")
        return None


def get_github_issue_list(
    owner: str,
    repo: str,
    state: str = "open",
    labels: list[str] | None = None,
    limit: int = 20,
) -> str | None:
    """List GitHub issues with structured, token-efficient output.

    Args:
        owner: Repository owner
        repo: Repository name
        state: Filter by state: open, closed, all (default: open)
        labels: Optional list of label names to filter by
        limit: Maximum number of issues to return (default: 20)

    Returns:
        Formatted string with issue listing, or None on error
    """
    if not shutil.which("gh"):
        logger.debug("gh CLI not available for issue listing")
        return None

    cmd = [
        "gh",
        "issue",
        "list",
        "--repo",
        f"{owner}/{repo}",
        "--state",
        state,
        "--limit",
        str(limit),
        "--json",
        "number,title,state,labels,author,updatedAt,assignees",
    ]

    if labels:
        for label in labels:
            cmd.extend(["--label", label])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        issues = json.loads(result.stdout)

        if not issues:
            state_desc = "matching" if state == "all" else state
            return f"No {state_desc} issues found in {owner}/{repo}."

        output = f"Issues in {owner}/{repo} ({state}):\n\n"
        for issue in issues:
            number = issue.get("number", "?")
            title = issue.get("title", "")
            labels_list = [label.get("name", "") for label in issue.get("labels", [])]
            author = issue.get("author", {}).get("login", "")
            assignees = [a.get("login", "") for a in issue.get("assignees", [])]
            updated = issue.get("updatedAt", "")[:10]

            line = f"  #{number} {title}"
            meta_parts = []
            if labels_list:
                meta_parts.append(f"[{', '.join(labels_list)}]")
            if author:
                meta_parts.append(f"by @{author}")
            if assignees:
                meta_parts.append(f"assigned:{','.join(f'@{a}' for a in assignees)}")
            if updated:
                meta_parts.append(f"updated:{updated}")

            if meta_parts:
                line += f"  ({' | '.join(meta_parts)})"

            output += line + "\n"

        state_label = "issues" if state == "all" else f"{state} issues"
        output += f"\nShowing {len(issues)} {state_label}."
        return output

    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to list issues: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse issue list JSON: {e}")
        return None


def get_github_pr_list(
    owner: str,
    repo: str,
    state: str = "open",
    limit: int = 20,
) -> str | None:
    """List GitHub pull requests with structured, token-efficient output.

    Args:
        owner: Repository owner
        repo: Repository name
        state: Filter by state: open, closed, merged, all (default: open)
        limit: Maximum number of PRs to return (default: 20)

    Returns:
        Formatted string with PR listing, or None on error
    """
    if not shutil.which("gh"):
        logger.debug("gh CLI not available for PR listing")
        return None

    cmd = [
        "gh",
        "pr",
        "list",
        "--repo",
        f"{owner}/{repo}",
        "--state",
        state,
        "--limit",
        str(limit),
        "--json",
        "number,title,state,author,updatedAt,headRefName,isDraft,reviewDecision",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        prs = json.loads(result.stdout)

        if not prs:
            state_desc = "matching" if state == "all" else state
            return f"No {state_desc} pull requests found in {owner}/{repo}."

        output = f"Pull requests in {owner}/{repo} ({state}):\n\n"
        for pr in prs:
            number = pr.get("number", "?")
            title = pr.get("title", "")
            author = pr.get("author", {}).get("login", "")
            branch = pr.get("headRefName", "")
            is_draft = pr.get("isDraft", False)
            review = pr.get("reviewDecision", "")
            updated = pr.get("updatedAt", "")[:10]

            line = f"  #{number} {title}"
            meta_parts = []
            if is_draft:
                meta_parts.append("DRAFT")
            if review:
                review_display = {
                    "APPROVED": "✅approved",
                    "CHANGES_REQUESTED": "❌changes requested",
                    "REVIEW_REQUIRED": "🔍review needed",
                }.get(review, review.lower())
                meta_parts.append(review_display)
            if author:
                meta_parts.append(f"by @{author}")
            if branch:
                meta_parts.append(f"branch:{branch}")
            if updated:
                meta_parts.append(f"updated:{updated}")

            if meta_parts:
                line += f"  ({' | '.join(meta_parts)})"

            output += line + "\n"

        state_label = "pull requests" if state == "all" else f"{state} pull requests"
        output += f"\nShowing {len(prs)} {state_label}."
        return output

    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to list PRs: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse PR list JSON: {e}")
        return None


def get_github_issue_content(owner: str, repo: str, number: str) -> str | None:
    """Get GitHub issue content using gh CLI."""
    if not shutil.which("gh"):
        logger.debug("gh CLI not available for GitHub issue handling")
        return None

    try:
        # Get the issue content
        issue_result = subprocess.run(
            ["gh", "issue", "view", number, "--repo", f"{owner}/{repo}"],
            capture_output=True,
            text=True,
            check=True,
        )

        # Get the comments
        comments_result = subprocess.run(
            ["gh", "issue", "view", number, "--repo", f"{owner}/{repo}", "--comments"],
            capture_output=True,
            text=True,
            check=True,
        )

        # Combine issue and comments
        content = issue_result.stdout
        if comments_result.stdout.strip():
            content += "\n\n" + comments_result.stdout

        return content
    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to get GitHub issue content: {e}")
        return None


def get_github_pr_content(url: str) -> str | None:
    """Get GitHub PR content with comments and reviews using gh CLI."""
    if not shutil.which("gh"):
        logger.debug("gh CLI not available for GitHub PR handling")
        return None

    github_info = parse_github_url(url)
    if not github_info:
        return None

    owner = github_info["owner"]
    repo = github_info["repo"]
    number = github_info["number"]

    try:
        # Get the PR content
        pr_result = subprocess.run(
            ["gh", "pr", "view", number, "--repo", f"{owner}/{repo}"],
            capture_output=True,
            text=True,
            check=True,
        )

        # Get the PR comments
        comments_result = subprocess.run(
            ["gh", "pr", "view", number, "--repo", f"{owner}/{repo}", "--comments"],
            capture_output=True,
            text=True,
            check=True,
        )

        # Get PR details to extract HEAD commit SHA
        pr_details_result = subprocess.run(
            ["gh", "api", f"/repos/{owner}/{repo}/pulls/{number}"],
            capture_output=True,
            text=True,
            check=False,
        )

        # Get review comments (inline code comments) using GitHub API
        review_comments_result = subprocess.run(
            ["gh", "api", f"/repos/{owner}/{repo}/pulls/{number}/comments"],
            capture_output=True,
            text=True,
            check=False,  # Don't fail if this doesn't work
        )

        # Get review threads to check resolution status using GraphQL
        graphql_query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) {
              reviewThreads(first: 100) {
                nodes {
                  isResolved
                  comments(first: 100) {
                    nodes {
                      databaseId
                    }
                  }
                }
              }
            }
          }
        }
        """

        review_threads_result = subprocess.run(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={graphql_query}",
                "-F",
                f"owner={owner}",
                "-F",
                f"repo={repo}",
                "-F",
                f"number={int(number)}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        # Combine all content
        content = pr_result.stdout

        if comments_result.stdout.strip():
            content += "\n\n" + comments_result.stdout

        # Format review comments if we got them, excluding resolved ones
        if (
            review_comments_result.returncode == 0
            and review_comments_result.stdout.strip()
        ):
            try:
                review_comments = json.loads(review_comments_result.stdout)

                # Get resolved thread IDs if available
                resolved_thread_ids = set()
                if (
                    review_threads_result.returncode == 0
                    and review_threads_result.stdout.strip()
                ):
                    try:
                        graphql_response = json.loads(review_threads_result.stdout)
                        review_threads_data = (
                            graphql_response.get("data", {})
                            .get("repository", {})
                            .get("pullRequest", {})
                            .get("reviewThreads", {})
                        )
                        review_threads = review_threads_data.get("nodes", [])

                        for thread in review_threads:
                            if thread.get("isResolved", False):
                                # Add all comment IDs from this resolved thread
                                thread_comments = thread.get("comments", {}).get(
                                    "nodes", []
                                )
                                for comment in thread_comments:
                                    comment_id = comment.get("databaseId")
                                    if comment_id:
                                        resolved_thread_ids.add(comment_id)
                    except (json.JSONDecodeError, KeyError):
                        logger.debug("Failed to parse review threads GraphQL response")

                # Filter out resolved comments
                unresolved_comments = [
                    comment
                    for comment in review_comments
                    if comment.get("id") not in resolved_thread_ids
                ]

                if unresolved_comments:
                    content += "\n\n## Review Comments (Unresolved)\n"
                    for comment in unresolved_comments:
                        user = comment.get("user", {}).get("login", "unknown")
                        original_body = comment.get("body", "")
                        body = _truncate_body(original_body)
                        path = comment.get("path", "")
                        comment_id = comment.get("id")
                        # Get line numbers (prefer current, fallback to original)
                        line = comment.get("line") or comment.get("original_line")
                        start_line = comment.get("start_line") or comment.get(
                            "original_start_line"
                        )
                        diff_hunk = comment.get("diff_hunk", "")

                        # Format line reference (handle multi-line comments)
                        if line and start_line and start_line != line:
                            line_ref = f"{path}:{start_line}-{line}"
                        elif line:
                            line_ref = f"{path}:{line}"
                        else:
                            # No line information available (e.g., file-level comment)
                            line_ref = path

                        # Include comment ID to enable replies via: gh api repos/.../pulls/.../comments/{id}/replies
                        id_suffix = f" (ID: {comment_id})" if comment_id else ""
                        content += f"\n**@{user}** on {line_ref}{id_suffix}:\n{body}\n"

                        # Add code context if available
                        if diff_hunk:
                            content += f"\nReferenced code in {line_ref}:\n"
                            # Get language from file extension, default to text for files without extension
                            lang = path.split(".")[-1] if "." in path else "text"
                            content += f"Context:\n```{lang}\n"
                            # Format diff_hunk to show code context (remove diff markers)
                            context_lines = []
                            for line_text in diff_hunk.split("\n"):
                                if line_text.startswith("@@"):
                                    continue
                                # Remove leading +/- but keep the content
                                if line_text.startswith(("+", "-", " ")):
                                    context_lines.append(line_text[1:])
                                else:
                                    context_lines.append(line_text)
                            content += "\n".join(context_lines)
                            content += "\n```\n"

                        # Extract and display code suggestions from ORIGINAL body
                        # (truncation may remove suggestions in the middle)
                        if "```suggestion" in original_body:
                            # Find suggestion blocks in the original body
                            lines = original_body.split("\n")
                            in_suggestion = False
                            suggestion_lines: list[str] = []

                            for line in lines:
                                if line.strip().startswith("```suggestion"):
                                    in_suggestion = True
                                    continue
                                if line.strip() == "```" and in_suggestion:
                                    if suggestion_lines:
                                        content += (
                                            "\nSuggested change:\n```"
                                            + path.split(".")[-1]
                                            + "\n"
                                        )
                                        content += "\n".join(suggestion_lines)
                                        content += "\n```\n"
                                        suggestion_lines = []
                                    in_suggestion = False
                                elif in_suggestion:
                                    suggestion_lines.append(line)
            except (json.JSONDecodeError, KeyError):
                logger.debug("Failed to parse review comments JSON")

        # Add GitHub Actions status last (chronologically latest)
        if pr_details_result.returncode == 0 and pr_details_result.stdout.strip():
            try:
                pr_details = json.loads(pr_details_result.stdout)
                head_sha = pr_details.get("head", {}).get("sha")
                if head_sha:
                    status_content = _get_github_actions_status(owner, repo, head_sha)
                    if status_content:
                        content += f"\n\n## GitHub Actions Status\n{status_content}"
            except (json.JSONDecodeError, KeyError):
                logger.debug("Failed to parse PR details JSON")

        return content
    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to get GitHub PR content: {e}")
        return None


# Default token budget for CI log output
DEFAULT_LOG_MAX_TOKENS = 4000


def _extract_failure_sections(log_text: str) -> str:
    """Extract the most relevant failure sections from CI log output.

    Looks for error patterns, test failures, and build errors,
    keeping surrounding context lines for readability.
    """
    lines = log_text.splitlines()
    if not lines:
        return log_text

    # Patterns that indicate failure-relevant lines
    error_patterns = [
        r"(?i)error[:\[\s]",
        r"(?i)failed",
        r"(?i)failure",
        r"(?i)FAILED",
        r"(?i)assert(ion)?error",
        r"(?i)traceback",
        r"(?i)exception",
        r"(?i)ModuleNotFoundError",
        r"(?i)ImportError",
        r"(?i)SyntaxError",
        r"(?i)TypeError",
        r"(?i)ValueError",
        r"(?i)KeyError",
        r"(?i)AttributeError",
        r"(?i)exit code [1-9]",
        r"(?i)Process completed with exit code [1-9]",
        r"(?i)##\[error\]",
    ]
    compiled = [re.compile(p) for p in error_patterns]

    # Find lines matching error patterns
    error_line_indices: set[int] = set()
    for i, line in enumerate(lines):
        for pattern in compiled:
            if pattern.search(line):
                error_line_indices.add(i)
                break

    if not error_line_indices:
        # No specific error patterns found — return tail of output
        tail_lines = 80
        if len(lines) <= tail_lines:
            return log_text
        return f"[... {len(lines) - tail_lines} lines omitted ...]\n" + "\n".join(
            lines[-tail_lines:]
        )

    # Collect error lines with context (3 before, 3 after)
    context_radius = 3
    selected: set[int] = set()
    for idx in error_line_indices:
        for offset in range(-context_radius, context_radius + 1):
            target = idx + offset
            if 0 <= target < len(lines):
                selected.add(target)

    # Build output with gap indicators
    sorted_indices = sorted(selected)
    result_lines: list[str] = []
    prev_idx = -1
    for idx in sorted_indices:
        if idx > prev_idx + 1:
            gap = idx - prev_idx - 1
            if gap > 0:
                result_lines.append(f"[... {gap} lines omitted ...]")
        result_lines.append(lines[idx])
        prev_idx = idx

    # If there are lines after the last selected
    remaining = len(lines) - 1 - sorted_indices[-1]
    if remaining > 0:
        result_lines.append(f"[... {remaining} lines omitted ...]")

    return "\n".join(result_lines)


def get_github_run_logs(
    run_id: str, max_tokens: int = DEFAULT_LOG_MAX_TOKENS
) -> str | None:
    """Get failed job logs from a GitHub Actions run.

    Fetches the run metadata and failed job logs, extracting
    the most relevant failure information.

    Args:
        run_id: The workflow run ID (numeric string)
        max_tokens: Maximum tokens for the output (default: 4000)

    Returns:
        Formatted string with run info and failure logs, or None on error
    """
    if not shutil.which("gh"):
        logger.debug("gh CLI not available")
        return None

    try:
        # Get run metadata
        run_result = subprocess.run(
            [
                "gh",
                "run",
                "view",
                run_id,
                "--json",
                "databaseId,displayTitle,event,headBranch,conclusion,status,"
                "workflowName,createdAt,updatedAt,url,jobs",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        run_data = json.loads(run_result.stdout)

        status = run_data.get("status", "unknown")
        conclusion = run_data.get("conclusion", "unknown")
        title = run_data.get("displayTitle", "")
        workflow = run_data.get("workflowName", "")
        branch = run_data.get("headBranch", "")
        url = run_data.get("url", "")

        # Build header
        output = f"## Run {run_id}: {title}\n\n"
        output += f"**Workflow**: {workflow}\n"
        output += f"**Branch**: {branch}\n"
        output += f"**Status**: {status} ({conclusion})\n"
        if url:
            output += f"**URL**: {url}\n"

        # Get job details
        jobs = run_data.get("jobs", [])
        if not jobs:
            output += "\nNo job data available."
            return output

        # Summarize all jobs
        output += "\n### Jobs\n"
        failed_jobs: list[dict] = []
        for job in jobs:
            name = job.get("name", "Unknown")
            job_conclusion = job.get("conclusion", "unknown")
            job_status = job.get("status", "unknown")
            emoji = {
                "success": "✅",
                "failure": "❌",
                "cancelled": "🚫",
                "skipped": "⏭️",
            }.get(job_conclusion, "❓" if job_status == "completed" else "🔄")
            output += f"  {emoji} {name}: {job_conclusion or job_status}\n"

            if job_conclusion == "failure":
                failed_jobs.append(job)

        if not failed_jobs:
            if conclusion == "success":
                output += "\nAll jobs passed."
            else:
                output += f"\nNo failed jobs found (run conclusion: {conclusion})."
            return output

        # Fetch logs for failed jobs
        output += "\n### Failed Job Logs\n"

        # Fetch all failed logs once (gh run view --log-failed returns
        # logs for ALL failed jobs in a single call)
        log_result = subprocess.run(
            ["gh", "run", "view", run_id, "--log-failed"],
            capture_output=True,
            text=True,
            check=False,
        )
        all_log_text = (
            log_result.stdout
            if log_result.returncode == 0 and log_result.stdout.strip()
            else ""
        )

        # Budget tokens across failed jobs
        tokens_per_job = max(max_tokens // max(len(failed_jobs), 1), 500)

        for job in failed_jobs:
            job_id = job.get("databaseId", "")
            job_name = job.get("name", "Unknown")

            if not job_id:
                output += f"\n#### {job_name}\nNo job ID available.\n"
                continue

            output += f"\n#### {job_name}\n"

            # Show failed steps
            steps = job.get("steps", [])
            for step in steps:
                if step.get("conclusion") == "failure":
                    step_name = step.get("name", "Unknown step")
                    output += f"  ❌ Failed step: {step_name}\n"

            if all_log_text:
                # Filter to this job's logs if multiple jobs
                # gh format: "jobname\tstepname\tlog line"
                job_lines = [
                    line
                    for line in all_log_text.splitlines()
                    if line.startswith(job_name + "\t") or len(failed_jobs) == 1
                ]

                relevant_text = "\n".join(job_lines) if job_lines else all_log_text

                # Extract failure sections
                extracted = _extract_failure_sections(relevant_text)

                # Truncate to budget
                max_chars = tokens_per_job * 4
                if len(extracted) > max_chars:
                    cut = extracted.rfind("\n", 0, max_chars)
                    cut = cut + 1 if cut != -1 else max_chars
                    truncated = len(extracted) - cut
                    extracted = (
                        extracted[:cut] + f"\n[... {truncated} chars truncated — "
                        f"use `gh run view {run_id} --log-failed` for full logs]\n"
                    )

                output += f"\n```\n{extracted}\n```\n"
            else:
                # Fallback: try per-job log via API
                api_result = subprocess.run(
                    [
                        "gh",
                        "api",
                        f"/repos/{{owner}}/{{repo}}/actions/jobs/{job_id}/logs",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if api_result.returncode == 0 and api_result.stdout.strip():
                    extracted = _extract_failure_sections(api_result.stdout)
                    max_chars = tokens_per_job * 4
                    if len(extracted) > max_chars:
                        extracted = extracted[:max_chars] + "\n[... truncated]\n"
                    output += f"\n```\n{extracted}\n```\n"
                else:
                    output += (
                        f"\nCould not fetch logs. "
                        f"Try: `gh run view {run_id} --log-failed`\n"
                    )

        return output

    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to get run info: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse run JSON: {e}")
        return None


def search_github_issues(
    query: str,
    repo: str | None = None,
    state: str | None = None,
    author: str | None = None,
    assignee: str | None = None,
    label: str | None = None,
    limit: int = 20,
) -> str | None:
    """Search GitHub issues across repositories.

    Args:
        query: Search query string (GitHub search syntax)
        repo: Optional repo filter (owner/repo)
        state: Optional state filter: open, closed
        author: Optional author filter
        assignee: Optional assignee filter
        label: Optional label filter
        limit: Maximum results (default: 20)

    Returns:
        Formatted search results string, or None on error
    """
    if not shutil.which("gh"):
        logger.debug("gh CLI not available for issue search")
        return None

    cmd = [
        "gh",
        "search",
        "issues",
        query,
        "--limit",
        str(limit),
        "--json",
        "number,title,state,repository,author,labels,updatedAt",
    ]

    if repo:
        cmd.extend(["--repo", repo])
    if state:
        cmd.extend(["--state", state])
    if author:
        cmd.extend(["--author", author])
    if assignee:
        cmd.extend(["--assignee", assignee])
    if label:
        cmd.extend(["--label", label])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        issues = json.loads(result.stdout)

        if not issues:
            return f'No issues found for query: "{query}"'

        output = f'Search results for issues: "{query}"\n\n'
        for issue in issues:
            number = issue.get("number", "?")
            title = issue.get("title", "")
            issue_state = issue.get("state", "").upper()
            repo_info = issue.get("repository", {})
            repo_name = (
                repo_info.get("nameWithOwner", "")
                if isinstance(repo_info, dict)
                else ""
            )
            author_info = issue.get("author") or {}
            author_login = (
                author_info.get("login", "") if isinstance(author_info, dict) else ""
            )
            labels = issue.get("labels") or []
            labels_list = [
                lbl.get("name", "") for lbl in labels if isinstance(lbl, dict)
            ]
            updated = issue.get("updatedAt", "")[:10]

            line = f"  {repo_name}#{number} {title}"
            meta_parts = []
            if issue_state:
                meta_parts.append(issue_state)
            if labels_list:
                meta_parts.append(f"[{', '.join(labels_list)}]")
            if author_login:
                meta_parts.append(f"by @{author_login}")
            if updated:
                meta_parts.append(f"updated:{updated}")

            if meta_parts:
                line += f"  ({' | '.join(meta_parts)})"

            output += line + "\n"

        output += f"\nShowing {len(issues)} results."
        return output

    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to search issues: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse issue search JSON: {e}")
        return None


def search_github_prs(
    query: str,
    repo: str | None = None,
    state: str | None = None,
    author: str | None = None,
    label: str | None = None,
    limit: int = 20,
) -> str | None:
    """Search GitHub pull requests across repositories.

    Args:
        query: Search query string (GitHub search syntax)
        repo: Optional repo filter (owner/repo)
        state: Optional state filter: open, closed, merged
        author: Optional author filter
        label: Optional label filter
        limit: Maximum results (default: 20)

    Returns:
        Formatted search results string, or None on error
    """
    if not shutil.which("gh"):
        logger.debug("gh CLI not available for PR search")
        return None

    cmd = [
        "gh",
        "search",
        "prs",
        query,
        "--limit",
        str(limit),
        "--json",
        "number,title,state,repository,author,labels,updatedAt",
    ]

    if repo:
        cmd.extend(["--repo", repo])
    if state:
        cmd.extend(["--state", state])
    if author:
        cmd.extend(["--author", author])
    if label:
        cmd.extend(["--label", label])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        prs = json.loads(result.stdout)

        if not prs:
            return f'No pull requests found for query: "{query}"'

        output = f'Search results for PRs: "{query}"\n\n'
        for pr in prs:
            number = pr.get("number", "?")
            title = pr.get("title", "")
            pr_state = pr.get("state", "").upper()
            repo_info = pr.get("repository", {})
            repo_name = (
                repo_info.get("nameWithOwner", "")
                if isinstance(repo_info, dict)
                else ""
            )
            author_info = pr.get("author") or {}
            author_login = (
                author_info.get("login", "") if isinstance(author_info, dict) else ""
            )
            labels = pr.get("labels") or []
            labels_list = [
                lbl.get("name", "") for lbl in labels if isinstance(lbl, dict)
            ]
            updated = pr.get("updatedAt", "")[:10]

            line = f"  {repo_name}#{number} {title}"
            meta_parts = []
            if pr_state:
                meta_parts.append(pr_state)
            if labels_list:
                meta_parts.append(f"[{', '.join(labels_list)}]")
            if author_login:
                meta_parts.append(f"by @{author_login}")
            if updated:
                meta_parts.append(f"updated:{updated}")

            if meta_parts:
                line += f"  ({' | '.join(meta_parts)})"

            output += line + "\n"

        output += f"\nShowing {len(prs)} results."
        return output

    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to search PRs: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse PR search JSON: {e}")
        return None


def merge_github_pr(
    owner: str,
    repo: str,
    pr_number: str | int,
    *,
    method: str = "squash",
    auto: bool = False,
    delete_branch: bool = False,
    match_head_commit: str | None = None,
) -> dict[str, str | bool]:
    """Merge a GitHub pull request.

    Args:
        owner: Repository owner
        repo: Repository name
        pr_number: Pull request number
        method: Merge method — "squash", "rebase", or "merge"
        auto: Enable auto-merge (merges when checks pass)
        delete_branch: Delete head branch after merge
        match_head_commit: Only merge if HEAD matches this SHA (safety check)

    Returns:
        dict with keys: success (bool), message (str), and optionally
        sha (str) for the merge commit, or url (str) for the PR.
    """
    if method not in ("squash", "rebase", "merge"):
        return {
            "success": False,
            "message": f"Invalid merge method: {method}. Use squash, rebase, or merge.",
        }

    cmd = [
        "gh",
        "pr",
        "merge",
        str(pr_number),
        "--repo",
        f"{owner}/{repo}",
        f"--{method}",
    ]

    if auto:
        cmd.append("--auto")
    if delete_branch:
        cmd.append("--delete-branch")
    if match_head_commit:
        cmd.extend(["--match-head-commit", match_head_commit])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )

        stdout = result.stdout.strip()

        # Try to extract merge commit SHA from gh output
        # gh pr merge typically outputs something like:
        #   "✓ Squashed and merged pull request #123"
        #   or with auto: "✓ Pull request #123 will be automatically merged..."
        response: dict[str, str | bool] = {
            "success": True,
            "message": stdout or f"Pull request #{pr_number} merged successfully.",
            "url": f"https://github.com/{owner}/{repo}/pull/{pr_number}",
        }

        # Fetch merge commit SHA if the merge already happened (not auto)
        if not auto:
            try:
                api_result = subprocess.run(
                    [
                        "gh",
                        "api",
                        f"/repos/{owner}/{repo}/pulls/{pr_number}",
                        "--jq",
                        ".merge_commit_sha",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                sha = api_result.stdout.strip()
                if sha and sha != "null":
                    response["sha"] = sha
            except subprocess.CalledProcessError:
                pass  # Non-critical — merge succeeded, just can't fetch SHA

        return response

    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else ""
        # Provide helpful messages for common failure modes
        if "merge conflict" in stderr.lower() or "not mergeable" in stderr.lower():
            return {
                "success": False,
                "message": f"Cannot merge PR #{pr_number}: merge conflicts exist. Resolve conflicts first.",
            }
        if "required status check" in stderr.lower():
            return {
                "success": False,
                "message": f"Cannot merge PR #{pr_number}: required status checks have not passed. Use --auto to merge when checks pass.",
            }
        if (
            "head branch was modified" in stderr.lower()
            or "expected head sha" in stderr.lower()
        ):
            return {
                "success": False,
                "message": f"Cannot merge PR #{pr_number}: HEAD commit changed since check. Verify the current HEAD and retry with --match-head-commit.",
            }
        if "pull request is in draft" in stderr.lower():
            return {
                "success": False,
                "message": f"Cannot merge PR #{pr_number}: PR is still in draft. Convert to ready-for-review first.",
            }

        return {
            "success": False,
            "message": f"Failed to merge PR #{pr_number}: {stderr or str(e)}",
        }
