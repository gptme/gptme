#!/usr/bin/env python3
"""
Pre-commit hook to validate conventional commit messages.

Usage:
    check_conventional_commits.py --commit-msg-file <file>

Conventional Commits spec: https://www.conventionalcommits.org/en/v1.0.0/

Valid types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert
"""

import argparse
import re
import sys

VALID_TYPES = [
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
]

# Pattern: type(scope)!: description  OR  type: description
CONVENTIONAL_RE = re.compile(
    r"^(?P<type>feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(?:\((?P<scope>[a-z0-9_\-]+)\))?"
    r"(?P<breaking>!)?"
    r":\s"  # colon followed by exactly one space
    r"(?P<subject>.+)$",
    re.IGNORECASE,
)


def check_commit_message(message: str, filename: str = "<stdin>") -> list[str]:
    """Check a commit message and return a list of errors."""
    errors = []
    lines = message.strip().split("\n")

    if not lines:
        errors.append(f"{filename}: Commit message is empty")
        return errors

    subject = lines[0]

    m = CONVENTIONAL_RE.match(subject)
    if not m:
        errors.append(
            f"{filename}: Commit message does not follow Conventional Commits format:\n"
            f"  '{subject}'\n"
            f"  Expected: type(scope)!: description\n"
            f"  Valid types: {', '.join(VALID_TYPES)}"
        )
        return errors

    commit_type = m.group("type").lower()
    if commit_type not in VALID_TYPES:
        errors.append(
            f"{filename}: Invalid commit type '{commit_type}'. "
            f"Valid types: {', '.join(VALID_TYPES)}"
        )

    # Check subject length (recommendation, not hard error)
    subject_text = m.group("subject")
    if len(subject_text) > 72:
        errors.append(
            f"{filename}: Subject line is {len(subject_text)} chars (max 72 recommended): "
            f"'{subject_text[:60]}...'"
        )

    # Check that subject doesn't end with a period
    if subject_text.rstrip().endswith("."):
        errors.append(
            f"{filename}: Subject line should not end with a period: "
            f"'{subject_text}'"
        )

    # Check that scope (if present) is lowercase
    scope = m.group("scope")
    if scope and scope != scope.lower():
        errors.append(
            f"{filename}: Scope should be lowercase: ({scope})"
        )

    return errors


def main():
    parser = argparse.ArgumentParser(description="Check conventional commit messages")
    parser.add_argument(
        "--commit-msg-file",
        help="Path to the commit message file",
    )
    args = parser.parse_args()

    if args.commit_msg_file:
        with open(args.commit_msg_file, "r") as f:
            message = f.read()
    else:
        message = sys.stdin.read()

    errors = check_commit_message(message, args.commit_msg_file or "<stdin>")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        sys.exit(1)
    else:
        print(f"OK: {message.strip().split(chr(10))[0][:72]}")
        sys.exit(0)


if __name__ == "__main__":
    main()