#!/usr/bin/env python3
"""
Check RST files for proper formatting of lists.
Lists in RST format need to be properly separated by blank lines for correct rendering.

This script enforces consistent formatting by checking:
- All nested lists require blank lines before them for proper rendering
- Bullet lists should be preceded by blank lines when starting after other content
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
    Check a single RST file for list formatting issues.

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

    # Pattern to detect list table directives and other RST directives
    list_table_pattern = re.compile(r"^\s*\.\.\s+list-table::")
    directive_pattern = re.compile(r"^\s*\.\.\s+")

    # Track the last line that had a list marker and its indentation
    last_list_line = -1
    last_indent_level = -1
    in_list = False

    # Flag to track if we're inside a list-table section or other directive
    in_list_table = False
    in_code_block = False
    code_block_indent = 0

    for i, line in enumerate(lines):
        # Check for code block start (.. code-block:: or literal block ::)
        if re.match(r"^\s*\.\.\s+code-block::", line) or line.rstrip().endswith("::"):
            in_code_block = True
            code_block_indent = len(line) - len(line.lstrip())
            continue

        # Check if we're exiting a code block (line with same or less indentation that has content)
        if in_code_block and line.strip():
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= code_block_indent:
                in_code_block = False

        # Skip checking inside code blocks
        if in_code_block:
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

        # Skip empty lines for processing, but track them for blank line detection
        if not line.strip():
            continue

        # Check for list markers
        bullet_match = bullet_pattern.match(line)
        numbered_match = numbered_pattern.match(line)

        if match := (bullet_match or numbered_match):
            indent_level = len(match.group(1))

            # Check if this is the start of a new list (not continuing an existing one)
            is_new_list = not in_list or (
                last_list_line >= 0 and indent_level <= last_indent_level
            )

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
                        issues.append(("nested", last_list_line + 1, i + 1, context))

            # Check if bullet list needs blank line before it
            elif is_new_list and i > 0:
                # Find the previous non-empty line
                prev_non_empty_idx = i - 1
                while prev_non_empty_idx >= 0 and not lines[prev_non_empty_idx].strip():
                    prev_non_empty_idx -= 1

                if prev_non_empty_idx >= 0:
                    prev_non_empty = lines[prev_non_empty_idx]
                    # Check if there's no blank line before this list
                    has_blank_line_before = prev_non_empty_idx < i - 1

                    # Don't require blank line if:
                    # - Previous line is also a list item at the same or higher level
                    # - Previous line is a directive
                    # - We're at the start of the file
                    # - Previous line is a heading underline (=, -, ~, etc.)
                    prev_is_list_item = bool(
                        bullet_pattern.match(prev_non_empty)
                        or numbered_pattern.match(prev_non_empty)
                    )
                    prev_is_directive = directive_pattern.match(prev_non_empty)
                    prev_is_heading_underline = re.match(
                        r"^\s*[=\-~^'\"`#*+<>]{3,}\s*$", prev_non_empty
                    )

                    if (
                        not has_blank_line_before
                        and not prev_is_list_item
                        and not prev_is_directive
                        and not prev_is_heading_underline
                        and prev_non_empty.strip()
                    ):  # Previous line has content
                        context = f"{prev_non_empty}\n{line}"
                        issues.append(
                            ("blank_line", prev_non_empty_idx + 1, i + 1, context)
                        )

            # Update tracking
            last_list_line = i
            last_indent_level = indent_level
            in_list = True
        else:
            # If this line is not a list item, we're no longer in a list
            # unless it's continuation content (indented)
            if not line.startswith(" ") or not in_list:
                in_list = False

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
            for issue_type, line1, line2, context in issues:
                newline = "\n"
                if issue_type == "nested":
                    print(
                        f"  Parent list item at line {line1}, nested list at line {line2} without blank line separation"
                    )
                    print(
                        f"  Context:\n    {context.replace(newline, newline + '    ')}"
                    )
                    print("  Fix: Add a blank line before the nested list")
                elif issue_type == "blank_line":
                    print(
                        f"  Bullet list at line {line2} not preceded by blank line (previous content at line {line1})"
                    )
                    print(
                        f"  Context:\n    {context.replace(newline, newline + '    ')}"
                    )
                    print("  Fix: Add a blank line before the bullet list")
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
