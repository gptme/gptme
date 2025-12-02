#!/usr/bin/env python3
"""
GitHub Bot for gptme - A standalone script to handle @gptme commands in GitHub issues/PRs.

This script can be run locally for testing or in CI via Docker container.
It processes GitHub issue/PR comments that start with @gptme and executes the command.

Environment Variables:
    GITHUB_TOKEN: GitHub personal access token (required)
    GITHUB_REPOSITORY: Repository in owner/repo format (required)
    GITHUB_EVENT_PATH: Path to GitHub event JSON (optional, for CI)
    OPENAI_API_KEY: OpenAI API key (optional)
    ANTHROPIC_API_KEY: Anthropic API key (optional)
    MODEL: Model to use (default: anthropic/claude-sonnet-4-20250514)
    ALLOWLIST: Comma-separated list of allowed usernames (default: ErikBjare)
    DRY_RUN: If set, don't make changes (for testing)

Usage:
    # Local testing with an issue
    GITHUB_TOKEN=xxx GITHUB_REPOSITORY=gptme/gptme ./scripts/github_bot.py \
        --issue 123 --comment-body "@gptme What is this project about?"

    # Local testing with a PR
    GITHUB_TOKEN=xxx GITHUB_REPOSITORY=gptme/gptme ./scripts/github_bot.py \
        --pr 456 --comment-body "@gptme Fix the typo in README"

    # In CI (reads from GITHUB_EVENT_PATH)
    ./scripts/github_bot.py
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitHubEvent:
    """Represents a GitHub webhook event."""

    issue_number: int
    comment_body: str
    comment_id: int
    comment_author: str
    is_pull_request: bool
    repository: str


def get_env(name: str, default: str | None = None, required: bool = False) -> str:
    """Get environment variable with optional default and required check."""
    value = os.environ.get(name, default)
    if required and not value:
        print(f"Error: {name} environment variable is required", file=sys.stderr)
        sys.exit(1)
    return value or ""


def run_command(
    cmd: list[str],
    check: bool = True,
    capture: bool = False,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a shell command with error handling and logging."""
    cmd_str = " ".join(cmd)
    print(f"[CMD] {cmd_str}")
    try:
        result = subprocess.run(
            cmd,
            check=check,
            capture_output=capture,
            text=True,
            cwd=cwd,
        )
        if capture and result.returncode != 0:
            print(f"[WARN] Command returned non-zero: {result.returncode}")
            if result.stderr:
                print(f"[STDERR] {result.stderr[:500]}")
        return result
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Command failed: {cmd_str}")
        if e.stderr:
            print(f"[STDERR] {e.stderr[:500]}")
        raise


def parse_event_from_file(event_path: str) -> GitHubEvent:
    """Parse GitHub event from webhook JSON file."""
    with open(event_path) as f:
        event = json.load(f)

    issue = event.get("issue", {})
    comment = event.get("comment", {})

    return GitHubEvent(
        issue_number=issue.get("number", 0),
        comment_body=comment.get("body", ""),
        comment_id=comment.get("id", 0),
        comment_author=comment.get("user", {}).get("login", ""),
        is_pull_request=issue.get("pull_request") is not None,
        repository=event.get("repository", {}).get("full_name", ""),
    )


def parse_event_from_args(args: argparse.Namespace) -> GitHubEvent:
    """Create GitHubEvent from command line arguments."""
    return GitHubEvent(
        issue_number=args.issue or args.pr or 0,
        comment_body=args.comment_body,
        comment_id=args.comment_id or 0,
        comment_author=args.author or "local-test",
        is_pull_request=args.pr is not None,
        repository=get_env("GITHUB_REPOSITORY", required=True),
    )


def detect_gptme_command(comment_body: str) -> str | None:
    """Extract @gptme command from comment body."""
    if comment_body.startswith("@gptme "):
        return comment_body[7:].strip()
    return None


def check_allowlist(author: str, allowlist: str) -> bool:
    """Check if the comment author is on the allowlist."""
    allowed_users = [u.strip() for u in allowlist.split(",")]
    return author in allowed_users


def react_to_comment(
    repository: str, comment_id: int, token: str, dry_run: bool = False
) -> None:
    """Add a +1 reaction to the comment."""
    if dry_run:
        print(f"[DRY RUN] Would react to comment {comment_id}")
        return

    run_command(
        [
            "gh",
            "api",
            f"/repos/{repository}/issues/comments/{comment_id}/reactions",
            "-X",
            "POST",
            "-f",
            "content=+1",
        ]
    )


# Maximum context sizes to prevent token limit issues
MAX_CONTEXT_CHARS = 50000  # ~12.5k tokens
MAX_DIFF_CHARS = 30000  # Diffs can be large, limit separately
MAX_COMMENT_CHARS = 20000  # Comments can accumulate


def truncate_content(content: str, max_chars: int, label: str = "content") -> str:
    """Truncate content to max_chars with a notice if truncated."""
    if len(content) <= max_chars:
        return content
    truncated = content[:max_chars]
    # Try to truncate at a newline for cleaner output
    last_newline = truncated.rfind("\n", max_chars - 500, max_chars)
    if last_newline > max_chars - 500:
        truncated = truncated[:last_newline]
    return f"{truncated}\n\n[... {label} truncated, {len(content) - len(truncated)} chars omitted ...]"


def get_context(
    repository: str, issue_number: int, is_pr: bool, token: str
) -> dict[str, str]:
    """Get context from the issue or PR with size limits to prevent token overflow."""
    context = {}
    ctx_dir = tempfile.mkdtemp()

    if is_pr:
        # Get PR details
        result = run_command(
            ["gh", "pr", "view", str(issue_number), "--repo", repository],
            capture=True,
        )
        context["pr"] = truncate_content(result.stdout, MAX_CONTEXT_CHARS, "PR details")

        # Get PR comments
        result = run_command(
            ["gh", "pr", "view", str(issue_number), "--repo", repository, "-c"],
            capture=True,
        )
        context["comments"] = truncate_content(
            result.stdout, MAX_COMMENT_CHARS, "comments"
        )

        # Get PR diff (often the largest, limit more aggressively)
        result = run_command(
            ["gh", "pr", "diff", str(issue_number), "--repo", repository],
            capture=True,
        )
        context["diff"] = truncate_content(result.stdout, MAX_DIFF_CHARS, "diff")

        # Get PR checks
        result = run_command(
            ["gh", "pr", "checks", str(issue_number), "--repo", repository],
            capture=True,
            check=False,
        )
        context["checks"] = result.stdout  # Checks are usually small
    else:
        # Get issue details
        result = run_command(
            ["gh", "issue", "view", str(issue_number), "--repo", repository],
            capture=True,
        )
        context["issue"] = truncate_content(
            result.stdout, MAX_CONTEXT_CHARS, "issue details"
        )

        # Get issue comments
        result = run_command(
            ["gh", "issue", "view", str(issue_number), "--repo", repository, "-c"],
            capture=True,
        )
        context["comments"] = truncate_content(
            result.stdout, MAX_COMMENT_CHARS, "comments"
        )

    # Write context files
    for name, content in context.items():
        path = Path(ctx_dir) / f"gh-{name}.md"
        path.write_text(content)

    return {"dir": ctx_dir, **context}


def determine_action_type(command: str, model: str) -> str:
    """Determine if the command requires changes or just a response."""
    result = run_command(
        [
            "gptme",
            "--non-interactive",
            "--model",
            model,
            f"Determine if this command requires changes to be made or just a response. "
            f"Respond with ONLY 'make_changes' or 'respond'. Command: {command}",
        ],
        capture=True,
    )

    output = result.stdout.lower()
    if "make_changes" in output:
        return "make_changes"
    return "respond"


def run_gptme(
    command: str,
    context_dir: str,
    workspace: str,
    model: str,
    timeout: int = 120,
) -> bool:
    """Run gptme with the given command and context."""
    # Build the context file list
    context_files = list(Path(context_dir).glob("gh-*.md"))
    context_args = [str(f) for f in context_files]

    cmd = [
        "gptme",
        "--non-interactive",
        "--model",
        model,
        command,
        "<system>",
        "The project has been cloned to the current directory.",
        "Here is the context:",
        *context_args,
        "</system>",
        "-",
        "Write the response to 'response.md', it will be posted as a comment.",
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=workspace,
            timeout=timeout,
            check=False,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("gptme timed out", file=sys.stderr)
        return False


def post_response(
    repository: str, issue_number: int, workspace: str, dry_run: bool = False
) -> None:
    """Post the response.md as a comment."""
    response_file = Path(workspace) / "response.md"
    if not response_file.exists():
        print("No response.md generated")
        return

    if dry_run:
        print(f"[DRY RUN] Would post response:\n{response_file.read_text()}")
        return

    run_command(
        [
            "gh",
            "issue",
            "comment",
            str(issue_number),
            "--repo",
            repository,
            "--body-file",
            str(response_file),
        ]
    )


def commit_and_push(
    repository: str,
    issue_number: int,
    command: str,
    workspace: str,
    branch_name: str,
    model: str,
    is_pr: bool,
    dry_run: bool = False,
) -> None:
    """Commit changes and push to the branch."""
    # Stage all changes
    run_command(["git", "add", "-A"], check=False)

    # Check if there are changes
    result = run_command(
        ["git", "diff", "--staged", "--quiet"],
        check=False,
    )
    if result.returncode == 0:
        print("No changes to commit")
        return

    if dry_run:
        print("[DRY RUN] Would commit and push changes")
        run_command(["git", "diff", "--staged", "--stat"])
        return

    # Generate commit message using gptme
    run_command(
        [
            "gptme",
            "--non-interactive",
            "--model",
            model,
            "Run 'git diff --staged' to inspect what has changed.",
            "-",
            "Write a commit message for it to 'message.txt'. Use conventional commits style.",
        ],
        capture=True,
        check=False,
    )

    message_file = Path(workspace) / "message.txt"
    if message_file.exists():
        commit_msg = message_file.read_text().strip()
    else:
        commit_msg = f"feat: changes from gptme bot\n\n`gptme '{command}'`"

    # Configure git
    run_command(["git", "config", "user.name", "gptme-bot"])
    run_command(["git", "config", "user.email", "gptme-bot@superuserlabs.org"])

    # Commit
    run_command(["git", "commit", "-m", commit_msg])

    # Push
    run_command(["git", "push", "-u", "origin", branch_name])

    # Create PR or comment
    if is_pr:
        run_command(
            [
                "gh",
                "pr",
                "comment",
                str(issue_number),
                "--repo",
                repository,
                "--body",
                "Changes have been pushed to this pull request.",
            ]
        )
    else:
        result = run_command(
            [
                "gh",
                "pr",
                "create",
                "--title",
                commit_msg.split("\n")[0],
                "--body",
                f"Changes from `gptme '{command}'`",
                "--repo",
                repository,
            ],
            capture=True,
        )
        pr_url = result.stdout.strip()
        run_command(
            [
                "gh",
                "issue",
                "comment",
                str(issue_number),
                "--repo",
                repository,
                "--body",
                f"A pull request has been created: {pr_url}",
            ]
        )


def validate_workspace(workspace: str) -> bool:
    """Validate that workspace is a git repository."""
    git_dir = Path(workspace) / ".git"
    if not git_dir.exists():
        print(f"[WARN] Workspace {workspace} is not a git repository")
        print("[HINT] Clone the repository first or specify --workspace")
        return False
    return True


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="GitHub Bot for gptme - handles @gptme commands in GitHub issues/PRs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Local testing with an issue (dry run)
  GITHUB_TOKEN=xxx GITHUB_REPOSITORY=owner/repo \\
    ./github_bot.py --issue 123 --comment-body "@gptme What is this?" --dry-run

  # Local testing with a PR
  GITHUB_TOKEN=xxx GITHUB_REPOSITORY=owner/repo \\
    ./github_bot.py --pr 456 --comment-body "@gptme Fix the typo" --workspace /path/to/repo

  # In CI (reads from GITHUB_EVENT_PATH automatically)
  ./github_bot.py
""",
    )
    parser.add_argument("--issue", type=int, help="Issue number to process")
    parser.add_argument("--pr", type=int, help="PR number to process")
    parser.add_argument("--comment-body", help="Comment body (for local testing)")
    parser.add_argument("--comment-id", type=int, help="Comment ID (for local testing)")
    parser.add_argument("--author", help="Comment author (for local testing)")
    parser.add_argument("--dry-run", action="store_true", help="Don't make changes")
    parser.add_argument(
        "--workspace",
        help="Workspace directory (must be a git clone of the repository)",
        default=".",
    )
    args = parser.parse_args()

    # Get configuration from environment
    token = get_env("GITHUB_TOKEN", required=True)
    model = get_env("MODEL", "anthropic/claude-sonnet-4-20250514")
    allowlist = get_env("ALLOWLIST", "ErikBjare")
    dry_run = args.dry_run or bool(os.environ.get("DRY_RUN"))

    # Set GITHUB_TOKEN for gh CLI
    os.environ["GITHUB_TOKEN"] = token

    # Parse event
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and not args.comment_body:
        event = parse_event_from_file(event_path)
    elif args.comment_body:
        event = parse_event_from_args(args)
    else:
        print("Error: Either GITHUB_EVENT_PATH or --comment-body required")
        return 1

    # Detect command
    command = detect_gptme_command(event.comment_body)
    if not command:
        print("No @gptme command found in comment")
        return 0

    print(f"Detected command: {command}")

    # Check allowlist
    if not check_allowlist(event.comment_author, allowlist):
        print(f"User {event.comment_author} is not on the allowlist")
        return 1

    # React to comment
    react_to_comment(event.repository, event.comment_id, token, dry_run)

    # Get context
    context = get_context(
        event.repository, event.issue_number, event.is_pull_request, token
    )
    print(f"Context directory: {context['dir']}")

    # Determine action type
    action_type = determine_action_type(command, model)
    print(f"Action type: {action_type}")

    # Validate and set up workspace
    workspace = args.workspace
    if action_type == "make_changes" and not validate_workspace(workspace):
        print("[ERROR] Cannot make changes without a valid git workspace")
        return 1

    branch_name = f"gptme/bot-changes-{event.issue_number}"

    if event.is_pull_request:
        # Get PR branch name
        result = run_command(
            [
                "gh",
                "pr",
                "view",
                str(event.issue_number),
                "--repo",
                event.repository,
                "--json",
                "headRefName",
                "-q",
                ".headRefName",
            ],
            capture=True,
        )
        branch_name = result.stdout.strip()
        run_command(["git", "fetch", "origin", branch_name], check=False)
        run_command(["git", "checkout", branch_name], check=False)
    elif action_type == "make_changes":
        run_command(["git", "checkout", "-b", branch_name], check=False)

    # Run gptme
    success = run_gptme(command, context["dir"], workspace, model)
    if not success:
        print("gptme execution failed")
        return 1

    # Post response
    post_response(event.repository, event.issue_number, workspace, dry_run)

    # Commit and push if making changes
    if action_type == "make_changes":
        commit_and_push(
            event.repository,
            event.issue_number,
            command,
            workspace,
            branch_name,
            model,
            event.is_pull_request,
            dry_run,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
