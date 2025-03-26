#!/usr/bin/env python3
"""
Check RST files for proper formatting of nested lists.
Nested lists in RST format need to be separated from parent list items by blank lines.

This script enforces consistent formatting by checking:
- All nested lists require blank lines before them for proper rendering
- List tables (e.g., .. list-table::) are correctly identified and skipped
- Content in comment blocks is checked with the same rules for consistency
- Only true nested lists are flagged (headings/descriptive text between lists are allowed)

The goal is to prevent rendering issues and maintain consistent formatting across
all documentation, including both visible content and comments.

TODO:
- Support checking docstrings in Python files which are also in RST format (autodoc)
"""

import argparse
import re
import sys
from pathlib import Path


def check_file(file_path):
    """
    Check a single RST file for nested list formatting issues.

    Args:
        file_path: Path to the RST file to check
    """
    with open(file_path, encoding="utf-8") as f:
        content = f.read()
        lines = content.splitlines()

    # Track list items and indentation levels
    issues = []

    # Regex patterns for list items
    bullet_pattern = re.compile(r"^(\s*)[-*+]\s+")
    numbered_pattern = re.compile(r"^(\s*)(?:\d+\.|[a-zA-Z]\.|\#\.)\s+")

    # Pattern to detect list table directives
    list_table_pattern = re.compile(r"^\s*\.\.\s+list-table::")

    # Track the last line that had a list marker and its indentation
    last_list_line = -1
    last_indent_level = -1

    # Flag to track if we're inside a list-table section
    in_list_table = False

    for i, line in enumerate(lines):
        # Skip empty lines
        if not line.strip():
            continue

        # Check for list-table directive
        if list_table_pattern.match(line):
            in_list_table = True
            continue

        # If we're in a list-table and find a line that's not indented, we've exited the list-table
        if in_list_table and line.strip() and not line.startswith(" "):
            in_list_table = False

        # Skip checking inside list-tables
        if in_list_table:
            continue

        # Check for list markers
        bullet_match = bullet_pattern.match(line)
        numbered_match = numbered_pattern.match(line)

        if match := (bullet_match or numbered_match):
            indent_level = len(match.group(1))

            # If this is a nested list (more indented than the previous)
            if last_list_line >= 0 and indent_level > last_indent_level:
                # Check if there's a blank line between this and the parent list item
                if i > 0 and lines[i - 1].strip():
                    # Allow headings and descriptive text between list levels
                    prev_is_list = bool(
                        bullet_pattern.match(lines[i - 1])
                        or numbered_pattern.match(lines[i - 1])
                    )

                    # Only report if the previous line is part of the parent list
                    if prev_is_list:
                        context = f"{lines[last_list_line]}\n{lines[i-1]}\n{line}"
                        issues.append((last_list_line + 1, i + 1, context))

            # Update tracking
            last_list_line = i
            last_indent_level = indent_level

    return issues


def main():
    parser = argparse.ArgumentParser(
        description="Check RST files for proper nested list formatting."
    )
    parser.add_argument("files", nargs="*", help="RST files to check (or directories)")
    parser.add_argument(
        "--fix", action="store_true", help="Attempt to fix issues (not implemented)"
    )

    args = parser.parse_args()

    # If no files provided, check all .rst files in docs/
    if not args.files:
        args.files = ["docs"]

    # Collect all .rst files
    rst_files = []
    for path_str in args.files:
        path = Path(path_str)
        if path.is_dir():
            # Skip _build directories which contain generated files
            rst_files.extend(
                [p for p in path.glob("**/*.rst") if "_build" not in str(p)]
            )
        elif path.suffix.lower() == ".rst":
            rst_files.append(path)

    found_issues = False

    for file_path in rst_files:
        issues = check_file(file_path)

        if issues:
            found_issues = True
            print(f"Issues found in {file_path}:")
            for parent_line, child_line, context in issues:
                newline = "\n"
                print(
                    f"  Parent list item at line {parent_line}, nested list at line {child_line} without blank line separation"
                )
                print(f"  Context:\n    {context.replace(newline, newline + '    ')}")
                print("  Fix: Add a blank line before the nested list")
                print()

    if found_issues:
        print(
            "Nested list formatting error: RST requires blank lines between parent list items and nested lists"
        )
        print(
            "See: https://docutils.sourceforge.io/docs/ref/rst/restructuredtext.html#bullet-lists"
        )
        sys.exit(1)
    else:
        print(
            f"✓ No nested list formatting issues found in {len(rst_files)} RST files."
        )


if __name__ == "__main__":
    main()
