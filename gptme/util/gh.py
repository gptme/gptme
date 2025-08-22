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

        # Combine all content
        content = pr_result.stdout

        if comments_result.stdout.strip():
            content += "\n\n" + comments_result.stdout

        # Format review comments if we got them
        if (
            review_comments_result.returncode == 0
            and review_comments_result.stdout.strip()
        ):
            try:
                review_comments = json.loads(review_comments_result.stdout)
                if review_comments:
                    content += "\n\n## Review Comments\n"
                    for comment in review_comments:
                        user = comment.get("user", {}).get("login", "unknown")
                        body = comment.get("body", "")
                        path = comment.get("path", "")
                        line = comment.get("line", "")
                        content += f"\n**@{user}** on {path}:{line}:\n{body}\n"
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
