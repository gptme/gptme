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
directory is checked, the docstrings rendered by its ``autodoc`` directives are
scanned, following each directive's target and options: ``automodule`` checks the
module docstring (and its public members only with ``:members:``), ``autoclass``
checks the class (and its methods only with ``:members:``), and ``autofunction`` /
``automethod`` check just that object. A common mistake is writing a markdown-style
list directly after a line like ``Package structure:``, which RST renders as a
single paragraph.

The goal is to prevent rendering issues and maintain consistent formatting across
all documentation, including both visible content and comments.
"""

import argparse
import ast
import inspect
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories under docs/ that aren't Sphinx sources or don't use autodoc
SKIP_DOC_DIRS = {"_build", "releases", "lessons"}

# ``.. automodule:: gptme.tools`` (RST) or ```{automodule} gptme.tools`` (MyST)
AUTODOC_DIRECTIVE = re.compile(
    r"^(?P<indent>\s*)(?:\.\.\s+auto(?P<rst>\w+)::|```\{auto(?P<myst>\w+)\})"
    r"\s+(?P<target>[\w.]+)\s*$"
)
AUTODOC_OPTION = re.compile(r"^\s*:(?P<name>[\w-]+):(?:\s+(?P<value>.*))?$")

# directive kinds that document a single class, function, or method
OBJECT_KINDS = {"class", "exception", "function", "method", "decorator"}

DefNode = ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
DEF_TYPES = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

# docstrings to check: file -> docstring start line -> raw docstring
Docstrings = dict[Path, dict[int, str]]

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


@dataclass(frozen=True)
class AutodocDirective:
    """An autodoc directive: what it documents and which members it renders."""

    kind: str  # the directive name without "auto", e.g. "module", "class", "function"
    target: str
    members: bool = False
    # explicit ``:members: a, b``; empty means all public members
    member_names: frozenset[str] = frozenset()
    exclude_members: frozenset[str] = frozenset()

    def includes(self, name: str) -> bool:
        """Whether a member with this name is rendered by the directive."""
        if not self.members or name in self.exclude_members:
            return False
        if self.member_names:
            return name in self.member_names
        return not name.startswith("_")


def _is_doc_source(path: Path, docs_dir: Path) -> bool:
    return not SKIP_DOC_DIRS.intersection(path.relative_to(docs_dir).parts)


def _option_names(value: str) -> frozenset[str]:
    return frozenset(name.strip() for name in value.split(",") if name.strip())


def parse_autodoc_directives(text: str) -> list[AutodocDirective]:
    """Parse autodoc directives and their options from RST or MyST source."""
    lines = text.splitlines()
    directives: list[AutodocDirective] = []
    for i, line in enumerate(lines):
        if not (match := AUTODOC_DIRECTIVE.match(line)):
            continue
        is_rst = match.group("rst") is not None
        indent = len(match.group("indent"))
        options: dict[str, str] = {}
        for option_line in lines[i + 1 :]:
            option = AUTODOC_OPTION.match(option_line)
            if option is None:
                break
            if is_rst and len(option_line) - len(option_line.lstrip()) <= indent:
                break
            options[option.group("name")] = (option.group("value") or "").strip()
        directives.append(
            AutodocDirective(
                kind=match.group("rst") or match.group("myst"),
                target=match.group("target"),
                members="members" in options,
                member_names=_option_names(options.get("members", "")),
                exclude_members=_option_names(options.get("exclude-members", "")),
            )
        )
    return directives


def find_autodoc_directives(docs_dir: Path) -> list[AutodocDirective]:
    """Collect the autodoc directives used in a docs directory."""
    directives: list[AutodocDirective] = []
    for pattern in ("**/*.rst", "**/*.md"):
        for path in sorted(docs_dir.glob(pattern)):
            if _is_doc_source(path, docs_dir):
                directives.extend(
                    parse_autodoc_directives(path.read_text(encoding="utf-8"))
                )
    return directives


def resolve_target(dotted_name: str) -> tuple[Path, list[str]] | None:
    """Resolve a dotted name to its module file and the object path within it.

    Uses the longest module prefix, so ``gptme.message.Message`` resolves to
    ``(gptme/message.py, ["Message"])`` and ``gptme.tools.subagent`` to the
    package's ``__init__.py`` with an empty object path.
    """
    parts = dotted_name.split(".")
    for n in range(len(parts), 0, -1):
        base = REPO_ROOT.joinpath(*parts[:n])
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if candidate.is_file():
                return candidate, parts[n:]
    return None


@cache
def _parse(file_path: Path) -> ast.Module:
    return ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))


def _module_name(file_path: Path) -> str:
    parts = list(file_path.relative_to(REPO_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _import_source(file_path: Path, node: ast.ImportFrom) -> str | None:
    """The absolute module name a ``from ... import`` statement imports from."""
    if node.level == 0:
        return node.module
    package = _module_name(file_path).split(".")
    if file_path.name != "__init__.py":
        package = package[:-1]
    if node.level > 1:
        package = package[: len(package) - (node.level - 1)]
    return ".".join(package + ([node.module] if node.module else []))


def find_object(
    file_path: Path, objpath: list[str], depth: int = 0
) -> tuple[Path, DefNode] | None:
    """Find a class, function, or method by its path within a module.

    Follows re-exports such as ``from .types import Name``, since autodoc
    documents the object where it's defined.
    """
    tree = _parse(file_path)
    body: list[ast.stmt] = tree.body
    node: DefNode | None = None
    for i, name in enumerate(objpath):
        node = next(
            (n for n in body if isinstance(n, DEF_TYPES) and n.name == name), None
        )
        if node is None:
            if i == 0 and depth < 3:
                return _find_reexport(file_path, tree, objpath, depth)
            return None
        body = node.body
    return (file_path, node) if node is not None else None


def _find_reexport(
    file_path: Path, tree: ast.Module, objpath: list[str], depth: int
) -> tuple[Path, DefNode] | None:
    for stmt in tree.body:
        if not isinstance(stmt, ast.ImportFrom):
            continue
        for alias in stmt.names:
            if (alias.asname or alias.name) != objpath[0]:
                continue
            module = _import_source(file_path, stmt)
            resolved = resolve_target(f"{module}.{alias.name}") if module else None
            if resolved is None or not resolved[1]:
                return None
            source_file, source_path = resolved
            return find_object(source_file, source_path + objpath[1:], depth + 1)
    return None


def _add_docstring(docstrings: Docstrings, file_path: Path, node: ast.AST) -> None:
    if (doc := _docstring_start(node)) is not None:
        raw, start_line = doc
        docstrings.setdefault(file_path, {})[start_line] = raw


def _add_members(
    docstrings: Docstrings,
    file_path: Path,
    container: ast.Module | ast.ClassDef,
    directive: AutodocDirective,
) -> None:
    for child in container.body:
        if isinstance(child, DEF_TYPES) and directive.includes(child.name):
            _add_docstring(docstrings, file_path, child)
            # member options propagate from a module to the classes it documents
            if isinstance(child, ast.ClassDef) and isinstance(container, ast.Module):
                _add_members(docstrings, file_path, child, directive)


def collect_rendered_docstrings(directives: list[AutodocDirective]) -> Docstrings:
    """Collect the docstrings that autodoc directives render, keyed by file and line.

    - ``automodule``: the module docstring, plus members only with ``:members:``
    - ``autoclass`` / ``autoexception``: the class docstring, plus methods only with ``:members:``
    - ``autofunction`` / ``automethod`` / ``autodecorator``: just that object
    """
    docstrings: Docstrings = {}
    for directive in directives:
        if (resolved := resolve_target(directive.target)) is None:
            continue
        file_path, objpath = resolved
        if directive.kind == "module":
            if objpath:
                continue
            tree = _parse(file_path)
            _add_docstring(docstrings, file_path, tree)
            if directive.members:
                _add_members(docstrings, file_path, tree, directive)
        elif directive.kind in OBJECT_KINDS and objpath:
            if (found := find_object(file_path, objpath)) is None:
                continue
            source_file, node = found
            _add_docstring(docstrings, source_file, node)
            if directive.members and isinstance(node, ast.ClassDef):
                _add_members(docstrings, source_file, node, directive)
    return docstrings


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


def check_docstring(raw: str, start_line: int) -> list[Issue]:
    """
    Check one docstring for list formatting issues.

    Line numbers in the returned issues refer to lines in the Python source.
    """
    cleaned = inspect.cleandoc(raw).splitlines()
    # cleandoc drops leading blank lines; count them to map lines back to source
    raw_lines = raw.expandtabs().split("\n")
    offset = 0
    while offset < len(raw_lines) and not raw_lines[offset].strip():
        offset += 1
    return [
        (
            issue_type,
            start_line + offset + line1 - 1,
            start_line + offset + line2 - 1,
            context,
        )
        for issue_type, line1, line2, context in check_lines(cleaned)
    ]


def public_docstrings(file_path: Path) -> dict[int, str]:
    """All public docstrings of a Python file, for files passed explicitly."""
    return {line: raw for raw, line in iter_public_docstrings(_parse(file_path))}


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
    docstrings: Docstrings = {}
    for path_str in args.files:
        path = Path(path_str)
        if path.is_dir():
            rst_files.update(
                p.resolve() for p in path.glob("**/*.rst") if "_build" not in p.parts
            )
            rendered = collect_rendered_docstrings(find_autodoc_directives(path))
            for file_path, file_docstrings in rendered.items():
                docstrings.setdefault(file_path, {}).update(file_docstrings)
        elif path.suffix.lower() == ".rst":
            rst_files.add(path.resolve())
        elif path.suffix.lower() == ".py":
            file_path = path.resolve()
            docstrings.setdefault(file_path, {}).update(public_docstrings(file_path))

    found_issues = False
    for file_path in sorted(rst_files):
        if issues := check_file(file_path):
            found_issues = True
            report(file_path, issues)
    for file_path in sorted(docstrings):
        issues = [
            issue
            for start_line, raw in sorted(docstrings[file_path].items())
            for issue in check_docstring(raw, start_line)
        ]
        if issues:
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
            f"and {sum(map(len, docstrings.values()))} docstrings in {len(docstrings)} Python files."
        )


if __name__ == "__main__":
    main()
