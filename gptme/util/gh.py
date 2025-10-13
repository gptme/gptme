"""
Fetches GitHub issue or PR content using the `gh` CLI tool, including essential info like comments and reviews.

Inspired by scripts/gh-pr-view-with-pr-comments.py
"""

import json
import logging
import shutil
import subprocess
import urllib.parse

logger = logging.getLogger(__name__)


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
    except Exception:
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
                        body = comment.get("body", "")
                        path = comment.get("path", "")
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

                        content += f"\n**@{user}** on {line_ref}:\n{body}\n"

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

                        # Extract and display code suggestions
                        if "```suggestion" in body:
                            # Find suggestion blocks in the body
                            lines = body.split("\n")
                            in_suggestion = False
                            suggestion_lines: list[str] = []

                            for line in lines:
                                if line.strip().startswith("```suggestion"):
                                    in_suggestion = True
                                    continue
                                elif line.strip() == "```" and in_suggestion:
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
