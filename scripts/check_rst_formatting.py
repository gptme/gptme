#!/usr/bin/env python3
"""
Check RST files and autodoc-rendered Python docstrings for proper formatting of lists.
Lists in RST format need to be properly separated by blank lines for correct rendering.

This script enforces consistent formatting by checking:

- All nested lists require blank lines before them for proper rendering
- Bullet lists should be preceded by blank lines when starting after other content
- List tables (e.g., .. list-table::) are correctly identified and skipped
- Content in comment blocks is checked with the same rules for consistency
- Only true nested lists are flagged (headings/descriptive text between lists are allowed)

Python docstrings are checked too, since Sphinx renders them as RST. When a docs
directory is checked, every module referenced by an ``autodoc`` directive
(``automodule``, ``autoclass``, ``autofunction``, ...) in it is scanned: the module
docstring plus the docstrings of public classes, functions, and methods. A common
mistake is writing a markdown-style list directly after a line like ``Package
structure:``, which RST renders as a single paragraph.

The goal is to prevent rendering issues and maintain consistent formatting across
all documentation, including both visible content and comments.
"""

import argparse
import ast
import inspect
import re
import sys
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories under docs/ that aren't Sphinx sources or don't use autodoc
SKIP_DOC_DIRS = {"_build", "releases", "lessons"}

AUTODOC_PATTERN = re.compile(
    r"(?:\.\.\s+|\{)auto(?:module|class|function|exception|data|attribute|method)"
    r"(?:::|\})\s+([\w.]+)"
)

# (issue type, first line, second line, context) with 1-indexed line numbers
Issue = tuple[str, int, int, str]


def check_lines(lines: list[str]) -> list[Issue]:
    """
    Check RST lines for list formatting issues.

    Args:
        lines: The RST content, split into lines

    Returns:
        Issues as ``(type, line1, line2, context)`` with 1-indexed line numbers
    """
    # Track list items and indentation levels
    issues: list[Issue] = []

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
                        context = f"{lines[last_list_line]}\n{lines[i - 1]}\n{line}"
                        issues.append(("nested", last_list_line + 1, i + 1, context))

            # Check if a new list needs a blank line before it. An item that follows
            # an indented continuation line of the previous item continues that
            # list, so only lists starting outside a list are checked.
            elif not in_list and i > 0:
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


def check_file(file_path: Path) -> list[Issue]:
    """
    Check a single RST file for list formatting issues.

    Args:
        file_path: Path to the RST file to check
    """
    return check_lines(file_path.read_text(encoding="utf-8").splitlines())


def _is_doc_source(path: Path, docs_dir: Path) -> bool:
    return not SKIP_DOC_DIRS.intersection(path.relative_to(docs_dir).parts)


def find_autodoc_modules(docs_dir: Path) -> set[str]:
    """Collect dotted names referenced by autodoc directives in a docs directory."""
    names: set[str] = set()
    for pattern in ("**/*.rst", "**/*.md"):
        for path in docs_dir.glob(pattern):
            if _is_doc_source(path, docs_dir):
                names.update(AUTODOC_PATTERN.findall(path.read_text(encoding="utf-8")))
    return names


def resolve_module_file(dotted_name: str) -> Path | None:
    """Resolve a dotted module/object name to its source file (longest module prefix)."""
    parts = dotted_name.split(".")
    for n in range(len(parts), 0, -1):
        base = REPO_ROOT.joinpath(*parts[:n])
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if candidate.is_file():
                return candidate
    return None


def _docstring_start(node: ast.AST) -> tuple[str, int] | None:
    """Return the raw docstring of a node and the source line its string starts on."""
    body = getattr(node, "body", None)
    if not body:
        return None
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first.value.value, first.lineno
    return None


def iter_public_docstrings(tree: ast.Module) -> Iterator[tuple[str, int]]:
    """Yield ``(raw docstring, start line)`` for the module and its public API.

    Covers the module docstring, public top-level classes and functions, and
    public methods of public classes — what ``automodule :members:`` renders.
    """
    nodes: list[ast.AST] = [tree]
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name.startswith("_"):
                continue
            nodes.append(node)
            if isinstance(node, ast.ClassDef):
                nodes.extend(
                    child
                    for child in node.body
                    if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                    and not child.name.startswith("_")
                )
    for documented in nodes:
        if (doc := _docstring_start(documented)) is not None:
            yield doc


def check_python_file(file_path: Path) -> list[Issue]:
    """
    Check the rendered docstrings of a Python file for list formatting issues.

    Line numbers in the returned issues refer to lines in the Python source.
    """
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    issues: list[Issue] = []
    for raw, start_line in iter_public_docstrings(tree):
        cleaned = inspect.cleandoc(raw).splitlines()
        # cleandoc drops leading blank lines; count them to map lines back to source
        raw_lines = raw.expandtabs().split("\n")
        offset = 0
        while offset < len(raw_lines) and not raw_lines[offset].strip():
            offset += 1
        for issue_type, line1, line2, context in check_lines(cleaned):
            issues.append(
                (
                    issue_type,
                    start_line + offset + line1 - 1,
                    start_line + offset + line2 - 1,
                    context,
                )
            )
    return issues


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def report(file_path: Path, issues: list[Issue]) -> None:
    newline = "\n"
    shown = _display_path(file_path)
    for issue_type, line1, line2, context in issues:
        if issue_type == "nested":
            print(
                f"{shown}:{line2}: nested list without blank line separation (parent list item at line {line1})"
            )
            fix = "Add a blank line before the nested list"
        else:
            print(
                f"{shown}:{line2}: bullet list not preceded by blank line (previous content at line {line1})"
            )
            fix = "Add a blank line before the bullet list"
        print(f"  Context:\n    {context.replace(newline, newline + '    ')}")
        print(f"  Fix: {fix}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Check RST files and autodoc-rendered docstrings for proper list formatting."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="RST or Python files to check, or docs directories (default: docs/). "
        "Directories also check the docstrings of modules their autodoc directives reference.",
    )
    parser.add_argument(
        "--fix", action="store_true", help="Attempt to fix issues (not implemented)"
    )

    args = parser.parse_args()

    # If no files provided, check docs/ (RST files and autodoc-rendered docstrings)
    if not args.files:
        args.files = [str(REPO_ROOT / "docs")]

    rst_files: set[Path] = set()
    py_files: set[Path] = set()
    for path_str in args.files:
        path = Path(path_str)
        if path.is_dir():
            rst_files.update(
                p.resolve() for p in path.glob("**/*.rst") if "_build" not in p.parts
            )
            for name in find_autodoc_modules(path):
                if (module_file := resolve_module_file(name)) is not None:
                    py_files.add(module_file.resolve())
        elif path.suffix.lower() == ".rst":
            rst_files.add(path.resolve())
        elif path.suffix.lower() == ".py":
            py_files.add(path.resolve())

    found_issues = False
    for file_path in sorted(rst_files):
        if issues := check_file(file_path):
            found_issues = True
            report(file_path, issues)
    for file_path in sorted(py_files):
        if issues := check_python_file(file_path):
            found_issues = True
            report(file_path, issues)

    if found_issues:
        print(
            "List formatting error: RST requires blank lines before lists and between parent list items and nested lists"
        )
        print(
            "See: https://docutils.sourceforge.io/docs/ref/rst/restructuredtext.html#bullet-lists"
        )
        sys.exit(1)
    else:
        print(
            f"✓ No list formatting issues found in {len(rst_files)} RST files "
            f"and {len(py_files)} autodoc-rendered Python files."
        )


if __name__ == "__main__":
    main()
