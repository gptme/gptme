#!/usr/bin/env python3
"""
Check markdown files for suspicious endings that might indicate cut-off codeblocks.

Detects patterns like:
- Files ending with "# Header"
- Files ending with "Title:"

These patterns often indicate incomplete content from markdown codeblock parsing issues.
"""

import sys
from pathlib import Path


def check_file(filepath: Path) -> bool:
    """
    Check if markdown file has suspicious ending.

    Returns True if file passes validation, False if it has issues.
    """
    try:
        content = filepath.read_text(encoding="utf-8")
        lines = content.split("\n")

        if not lines:
            return True

        # Get last non-empty line
        last_line = ""
        for line in reversed(lines):
            stripped = line.strip()
            if stripped:
                last_line = stripped
                break

        if not last_line:
            return True

        # Check for suspicious patterns
        is_suspicious = False
        reason = ""

        if last_line.startswith("#"):
            is_suspicious = True
            reason = f"ends with header: '{last_line}'"
        elif last_line.endswith(":"):
            is_suspicious = True
            reason = f"ends with colon: '{last_line}'"

        if is_suspicious:
            print(f"⚠️  {filepath}")
            print(f"   File {reason}")
            print(
                "   This might indicate incomplete content from markdown codeblock cut-off."
            )
            print("   Did you forget to specify language tag in codeblock?")
            print("   Use: ```txt, ```csv, ```ascii, ```diagram instead of ```")
            return False

        return True

    except Exception as e:
        print(f"Error checking {filepath}: {e}", file=sys.stderr)
        return True  # Don't fail on read errors


def main():
    """Check all markdown files passed as arguments."""
    if len(sys.argv) < 2:
        print("Usage: check_markdown_endings.py <file1.md> [file2.md ...]")
        sys.exit(0)

    files = [Path(f) for f in sys.argv[1:]]

    all_passed = True
    for filepath in files:
        if not check_file(filepath):
            all_passed = False

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
