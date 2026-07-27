#!/usr/bin/env python3
"""
Generate a changelog from conventional commits.

Usage:
    python scripts/changelog.py [--range RANGE] [--output FILE] [--since TAG]

Examples:
    python scripts/changelog.py --since v0.28.0
    python scripts/changelog.py --range v0.28.0...v0.29.0
    python scripts/changelog.py --output CHANGELOG.md
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Commit:
    """A single commit with its conventional commit metadata."""

    sha: str
    subject: str
    body: str = ""
    author: str = ""
    date: str = ""

    # Parsed conventional commit fields
    type: str = ""
    scope: str = ""
    breaking: bool = False
    breaking_description: str = ""
    references: list[str] = None  # e.g. ["#123", "#456"]

    def __post_init__(self):
        if self.references is None:
            self.references = []

    @property
    def short_sha(self) -> str:
        return self.sha[:7]

    @property
    def pr_refs(self) -> list[str]:
        """Extract PR numbers from the commit message."""
        refs = []
        for ref in self.references:
            m = re.search(r"#(\d+)", ref)
            if m:
                refs.append(f"#{m.group(1)}")
        return refs


# Conventional commit pattern
# Matches: type(scope): description  OR  type: description  OR  type!: description
CONVENTIONAL_RE = re.compile(
    r"^(?P<type>feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(?:\((?P<scope>[a-z0-9_\-]+)\))?(?P<breaking>!)?:\s+(?P<subject>.+)$",
    re.IGNORECASE,
)

# Breaking change pattern
BREAKING_RE = re.compile(r"^BREAKING[ \t]+CHANGE:", re.IGNORECASE)

# PR reference patterns
PR_RE = re.compile(r"#(\d+)")


def run_git(args: list[str]) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def parse_commits(range_spec: str) -> list[Commit]:
    """Parse commits in the given range into Commit objects."""
    commits: list[Commit] = []

    # Get log in a parseable format
    log = run_git([
        "log",
        range_spec,
        "--pretty=format:%H%n%B%n---COMMIT_SEP---",
    ])

    if not log:
        return commits

    blocks = log.split("---COMMIT_SEP---\n")
    blocks = [b for b in blocks if b.strip()]

    for block in blocks:
        lines = block.split("\n", 1)
        sha = lines[0].strip()
        body = lines[1] if len(lines) > 1 else ""

        # Get author and date
        author = run_git(["log", "-1", "--format=%an", sha])
        date = run_git(["log", "-1", "--format=%ai", sha])

        # Get subject (first line of body)
        subject_lines = body.strip().split("\n")
        subject = subject_lines[0] if subject_lines else ""

        commit = Commit(
            sha=sha,
            subject=subject,
            body=body.strip(),
            author=author,
            date=date,
        )

        # Parse conventional commit
        m = CONVENTIONAL_RE.match(subject)
        if m:
            commit.type = m.group("type").lower()
            commit.scope = m.group("scope") or ""
            commit.breaking = bool(m.group("breaking"))
            commit.subject = m.group("subject")

        # Check for breaking change in body
        if not commit.breaking:
            for line in body.split("\n"):
                if BREAKING_RE.match(line.strip()):
                    commit.breaking = True
                    commit.breaking_description = line.strip()
                    break

        # Extract PR references from body
        for line in body.split("\n"):
            for m in PR_RE.finditer(line):
                commit.references.append(f"#{m.group(1)}")
        # Also check subject
        for m in PR_RE.finditer(subject):
            commit.references.append(f"#{m.group(1)}")
        # Deduplicate
        commit.references = list(dict.fromkeys(commit.references))

        commits.append(commit)

    return commits


# Human-readable type labels
TYPE_LABELS = {
    "feat": "Features",
    "fix": "Bug Fixes",
    "docs": "Documentation",
    "style": "Styling",
    "refactor": "Refactoring",
    "perf": "Performance",
    "test": "Tests",
    "build": "Build",
    "ci": "CI",
    "chore": "Chores",
    "revert": "Reverts",
}

# Types that should appear in the changelog
VISIBLE_TYPES = {"feat", "fix", "docs", "refactor", "perf", "style"}


def format_commit(commit: Commit) -> str:
    """Format a single commit for the changelog."""
    parts = []

    # Breaking changes get special treatment
    if commit.breaking:
        parts.append(f"**BREAKING CHANGE:** {commit.breaking_description or 'See below'}")

    # Scope
    if commit.scope:
        parts.append(f"`{commit.scope}`")

    # Subject
    parts.append(commit.subject)

    # PR references
    if commit.pr_refs:
        parts.append(f" ({', '.join(commit.pr_refs)})")

    return "".join(parts)


def generate_changelog(
    commits: list[Commit],
    version: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> str:
    """Generate a changelog markdown from commits."""
    lines: list[str] = []

    # Header
    if version:
        lines.append(f"## [{version}]")
    elif since:
        lines.append(f"## Changes since {since}")
    else:
        lines.append("## Changelog")

    lines.append("")

    # Group by type
    groups: dict[str, list[Commit]] = defaultdict(list)
    breaking: list[Commit] = []

    for commit in commits:
        if commit.breaking:
            breaking.append(commit)
        elif commit.type in VISIBLE_TYPES:
            groups[commit.type].append(commit)

    # Breaking changes first
    if breaking:
        lines.append("### ⚠️ Breaking Changes")
        lines.append("")
        for commit in breaking:
            lines.append(f"- {format_commit(commit)}")
        lines.append("")

    # Grouped by type
    for type_key, label in TYPE_LABELS.items():
        if type_key not in VISIBLE_TYPES:
            continue
        if type_key not in groups:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for commit in groups[type_key]:
            lines.append(f"- {format_commit(commit)}")
        lines.append("")

    # Contributors
    authors = sorted(set(c.author for c in commits))
    if authors:
        lines.append("### Contributors")
        lines.append("")
        for author in authors:
            lines.append(f"- {author}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate changelog from conventional commits")
    parser.add_argument("--range", help="Git range (e.g. v0.28.0...v0.29.0)")
    parser.add_argument("--since", help="Generate changelog since this tag")
    parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    parser.add_argument("--version", help="Version string for header")
    parser.add_argument("--project-title", default="gptme", help="Project title")
    parser.add_argument("--org", default="gptme", help="GitHub org")
    parser.add_argument("--repo", default="gptme", help="GitHub repo")
    args = parser.parse_args()

    # Determine range
    if args.range:
        range_spec = args.range
    elif args.since:
        range_spec = f"{args.since}..HEAD"
    else:
        # Default: all commits
        range_spec = "--all"

    commits = parse_commits(range_spec)

    if not commits:
        print("No commits found.", file=sys.stderr)
        sys.exit(0)

    changelog = generate_changelog(
        commits,
        version=args.version,
        since=args.since,
    )

    if args.output:
        Path(args.output).write_text(changelog)
        print(f"Changelog written to {args.output}")
    else:
        print(changelog)


if __name__ == "__main__":
    main()