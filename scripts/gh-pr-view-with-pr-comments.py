#!/usr/bin/env python3

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse


@dataclass
class Comment:
    type: str
    user: str
    body: str
    created_at: datetime

    @classmethod
    def from_dict(cls, d: dict) -> "Comment":
        return cls(
            type="pr_comment",
            user=d["user"]["login"],
            body=d["body"],
            created_at=datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")),
        )


@dataclass
class Review:
    type: str
    user: str
    body: str
    created_at: datetime
    state: str

    @classmethod
    def from_dict(cls, d: dict) -> "Review":
        return cls(
            type="review",
            user=d["user"]["login"],
            body=d["body"],
            created_at=datetime.fromisoformat(d["submitted_at"].replace("Z", "+00:00")),
            state=d["state"],
        )


@dataclass
class ReviewComment:
    type: str
    user: str
    body: str
    created_at: datetime
    path: str
    line: int
    diff_hunk: str
    thread_id: str  # Either its own ID or the ID it's replying to
    id: str
    in_reply_to_id: str | None
    review_id: str  # ID of the review this comment belongs to
    commit_id: str  # The commit SHA this comment was made on
    thread_size: int = 1
    is_thread_start: bool = False
    is_thread_reply: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "ReviewComment":
        return cls(
            type="review_comment",
            user=d["user"]["login"],
            body=d["body"],
            created_at=datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")),
            path=d["path"],
            line=d["line"],
            diff_hunk=d["diff_hunk"],
            id=str(d["id"]),
            in_reply_to_id=(
                str(d["in_reply_to_id"]) if d.get("in_reply_to_id") else None
            ),
            thread_id=str(d["in_reply_to_id"] if d.get("in_reply_to_id") else d["id"]),
            review_id=str(d["pull_request_review_id"]),
            commit_id=d["commit_id"],
        )


class PRViewer:
    def __init__(self, url: str):
        # Parse URL or repo/pr format
        if not url.startswith("http"):
            url = f"https://github.com/{url}"
        parts = urlparse(url).path.strip("/").split("/")
        if len(parts) != 4 or parts[2] != "pull":
            raise ValueError("Invalid PR URL format. Expected: owner/repo/pull/number")

        self.owner = parts[0]
        self.repo = parts[1]
        self.pr_number = parts[3]

    def run_gh(
        self, args: list[str], parse_json: bool = True, check: bool = True
    ) -> Any:
        """Run GitHub CLI command and return output."""
        cmd = ["gh"] + args
        result = subprocess.run(cmd, capture_output=True, text=True, check=check)
        if check and result.returncode != 0:
            raise RuntimeError(
                f"Command failed: {' '.join(cmd)}\nError: {result.stderr}"
            )
        return json.loads(result.stdout) if parse_json else result.stdout

    def get_file_content(self, path: str, ref: str | None = None) -> str:
        """Get file content at specific ref."""
        # Use the raw.githubusercontent.com URL which is simpler and more reliable
        raw_url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{ref or 'HEAD'}/{path}"
        result = subprocess.run(
            ["curl", "-sL", raw_url], capture_output=True, text=True, check=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to get file content: {result.stderr}")
        return result.stdout

    def get_pr_info_api(self) -> dict[str, Any]:
        """Get PR info from API."""
        result = self.run_gh(
            ["api", f"/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}"]
        )
        assert isinstance(result, dict)  # Runtime check for mypy
        return result

    def format_suggestion_diff(
        self, file_content: str, line: int, suggestion: str, context_lines: int = 2
    ) -> str:
        """Format a proper diff for a suggestion."""
        lines = file_content.splitlines()

        # Calculate context ranges
        start = max(0, line - context_lines - 1)  # -1 because line is 1-indexed
        end = min(len(lines), line + context_lines)

        # Format as unified diff
        diff_lines = []

        # Add the diff header (including context lines in the count)
        num_lines = end - start
        diff_lines.append(f"@@ -{start+1},{num_lines} +{start+1},{num_lines} @@")

        # Add context and changes
        for i in range(start, end):
            if i == line - 1:  # This is the line being changed
                diff_lines.append(f"-{lines[i]}")
                diff_lines.append(f"+{suggestion}")
            else:  # This is context
                diff_lines.append(f" {lines[i]}")

        return "\n".join(diff_lines)

    def get_pr_info(self) -> str:
        """Get PR description and metadata."""
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                f"https://github.com/{self.owner}/{self.repo}/pull/{self.pr_number}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to get PR info: {result.stderr}")
        return result.stdout

    def get_comments(self) -> list[Comment | Review | ReviewComment]:
        """Get all comments, reviews, and review comments."""
        comments: list[Comment | Review | ReviewComment] = []

        # Get PR comments
        pr_comments = self.run_gh(  # type: ignore
            [
                "api",
                f"/repos/{self.owner}/{self.repo}/issues/{self.pr_number}/comments",
                "--header",
                "Accept: application/vnd.github+json",
                "--header",
                "X-GitHub-Api-Version: 2022-11-28",
            ]
        )
        comments.extend(Comment.from_dict(c) for c in pr_comments)

        # Get reviews
        reviews = self.run_gh(
            [
                "api",
                f"/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}/reviews",
                "--header",
                "Accept: application/vnd.github+json",
                "--header",
                "X-GitHub-Api-Version: 2022-11-28",
            ]
        )
        comments.extend(Review.from_dict(r) for r in reviews if r["body"])

        # Get review comments
        comments_path = (
            f"/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}/comments"
        )
        review_comments = self.run_gh(
            [
                "api",
                comments_path,
                "--header",
                "Accept: application/vnd.github+json",
                "--header",
                "X-GitHub-Api-Version: 2022-11-28",
            ]
        )

        # First, group comments by thread
        threads: dict[str, list[Any]] = {}
        for rc in review_comments:  # type: ignore
            thread_id = str(rc.get("in_reply_to_id") or rc["id"])
            if thread_id not in threads:
                threads[thread_id] = []
            threads[thread_id].append(rc)

        # Parse comments, excluding those from resolved threads
        rc_list = []
        for thread_id, thread in threads.items():
            # Check if any comment in the thread indicates resolution
            thread_resolved = any(
                "resolved" in c.get("body", "").lower() for c in thread
            )
            if not thread_resolved:
                thread_comments = [ReviewComment.from_dict(rc) for rc in thread]
                # Add thread ID to first comment for reference
                if thread_comments:
                    first_comment = thread_comments[0]
                    assert isinstance(
                        first_comment, ReviewComment
                    )  # Type guard for mypy
                    first_comment.thread_id = thread_id
                rc_list.extend(thread_comments)

        # Group review comments into threads
        review_threads: dict[str, list[ReviewComment]] = {}
        for rc in rc_list:
            if isinstance(rc, ReviewComment):  # Type guard for mypy
                thread_id = rc.thread_id
                if thread_id not in review_threads:
                    review_threads[thread_id] = []
                review_threads[thread_id].append(rc)

        # Mark thread starts and replies
        for thread in review_threads.values():
            # Sort thread by creation time
            thread.sort(key=lambda x: x.created_at)
            # Set thread size and mark comments
            thread[0].is_thread_start = True
            thread[0].thread_size = len(thread)
            for comment in thread[1:]:
                comment.is_thread_reply = True

        # First separate review comments from other comments
        review_comments = [c for c in comments if isinstance(c, ReviewComment)]
        other_comments = [c for c in comments if not isinstance(c, ReviewComment)]

        # Sort non-review comments by timestamp
        regular_comments: list[Comment | Review | ReviewComment] = sorted(
            other_comments,
            key=lambda x: x.created_at,
        )

        # Add review comments grouped by thread
        sorted_review_comments: list[ReviewComment] = []
        for thread in sorted(review_threads.values(), key=lambda t: t[0].created_at):
            sorted_review_comments.extend(sorted(thread, key=lambda x: x.created_at))
        regular_comments.extend(sorted_review_comments)

        return regular_comments

    def format_comment(self, comment: Comment | Review | ReviewComment) -> str:
        """Format a single comment."""
        if isinstance(comment, Comment):
            return f"@{comment.user}:\n{comment.body}"

        if isinstance(comment, Review):
            if comment.user == "ellipsis-dev[bot]":
                first_line = comment.body.split("\n")[0]
                return f"## Review by @{comment.user} ({comment.state})\n{first_line}"
            return f"## Review by @{comment.user} ({comment.state})\n{comment.body}"

        if isinstance(comment, ReviewComment):
            # Start new thread if this is the first comment
            output = []
            if isinstance(comment, ReviewComment) and comment.is_thread_start:
                comment_count = comment.thread_size
                comment_desc = (
                    f"{comment_count} comment{'s' if comment_count > 1 else ''}"
                )

                output.append("")  # Add newline before thread header
                output.append("---")  # Add separator between threads
                output.append(
                    f"▼ Thread about {comment.path} ({comment_desc}) [#{comment.thread_id}]:"
                )

            output.append(f"### Comment by @{comment.user}:")

            # Handle suggestion comments specially
            if "```suggestion" in comment.body:
                # Extract the comment text before the suggestion
                comment_text = comment.body.split("```suggestion")[0].strip()
                if comment_text:
                    output.append(comment_text)

                # Extract and format the suggestion (preserve indentation)
                suggestion = (
                    comment.body.split("```suggestion\n")[1].split("```")[0].rstrip()
                )

                try:
                    # Get file content at the exact commit the comment was made on
                    file_content = self.get_file_content(
                        comment.path, comment.commit_id
                    )

                    # Format a proper diff
                    diff = self.format_suggestion_diff(
                        file_content, comment.line, suggestion
                    )

                    output.append(
                        f"Suggestion for {comment.path}:{comment.line} (at {comment.commit_id[:7]})"
                    )
                    output.append(f"```diff\n{diff}\n```")
                except Exception as e:
                    # Fallback to simple suggestion if we can't get the context
                    output.append("Suggestion:")
                    output.append(f"```suggestion\n{suggestion}\n```")
                    output.append(
                        f"(Failed to get context from commit {comment.commit_id[:7]}: {e})"
                    )
            else:
                output.append(comment.body)

            # For thread starts without suggestions, show the diff context
            if comment.is_thread_start and "```suggestion" not in comment.body:
                output.append(f"Referenced code in {comment.path}:{comment.line}")
                output.append("Context:")
                output.append(f"```{comment.path}\n{comment.diff_hunk}\n```")

            return "\n".join(output)

        return ""

    def print_pr_with_comments(self):
        """Print PR info and all comments in chronological order."""
        print("<pr_info>")
        print(self.get_pr_info().strip())
        print("</pr_info>")
        print()
        print("<comments>")
        comments = self.get_comments()
        for comment in comments:
            print(self.format_comment(comment))
            print()
        print("</comments>")


def main():
    if len(sys.argv) != 2:
        print("Usage: gh-pr-view-with-pr-comments.py <pr-url>")
        print("  pr-url can be either:")
        print("    https://github.com/owner/repo/pull/number")
        print("    owner/repo/pull/number")
        sys.exit(1)

    try:
        viewer = PRViewer(sys.argv[1])
        viewer.print_pr_with_comments()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
