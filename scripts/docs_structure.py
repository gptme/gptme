#!/usr/bin/env python3
"""Inspect and check the structure of the built HTML docs.

The left sidebar lists pages; the right sidebar lists the headings of the current
page. The sources don't show how those combine for a reader, so this reads the
built site instead:

    python scripts/docs_structure.py                    # left-sidebar navigation tree
    python scripts/docs_structure.py --headings         # ...with each page's headings
    python scripts/docs_structure.py --page tools/subagent.html
    python scripts/docs_structure.py --check            # report structural problems

BUILD_DIR defaults to docs/_build/html (build it with ``make docs``). The checks
follow the conventions in the "Documentation" section of docs/contributing.rst.
"""

from __future__ import annotations

import argparse
import posixpath
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

# Deepest heading level allowed on a page: h4 is three levels below the title.
MAX_HEADING_LEVEL = 4
# More top-level sections than this makes the right sidebar hard to scan.
MAX_SECTIONS = 30
MAX_HEADING_CHARS = 80
# A page with at least this many sections of its own shouldn't have a lone subpage:
# the left sidebar then suggests that subpage is its only subtopic.
LONE_CHILD_MIN_SECTIONS = 3
LONE_CHILD_ALLOWED = {
    # Installation extras live under the install guide on purpose.
    "getting-started.html",
}


@dataclass
class NavNode:
    title: str
    # Page path relative to the build dir, an external URL, or None for captions.
    href: str | None = None
    children: list[NavNode] = field(default_factory=list)

    @property
    def is_external(self) -> bool:
        return bool(self.href) and "://" in (self.href or "")


@dataclass
class Heading:
    level: int
    text: str
    anchor: str | None = None


@dataclass
class Issue:
    severity: str  # "error" or "warning"
    code: str
    page: str
    message: str


def _classes(attrs: list[tuple[str, str | None]]) -> list[str]:
    return (dict(attrs).get("class") or "").split()


class _NavParser(HTMLParser):
    """Parse the pydata/book theme's left sidebar (``nav.bd-docs-nav``)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.roots: list[NavNode] = []
        self._nav_depth = 0
        self._stack: list[NavNode] = []
        self._capture: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "nav":
            if self._nav_depth or "bd-docs-nav" in _classes(attrs):
                self._nav_depth += 1
            return
        if not self._nav_depth:
            return
        if tag == "p" and "caption" in _classes(attrs):
            self._capture, self._text = "caption", []
        elif tag == "li":
            if not self.roots:
                self.roots.append(NavNode(title=""))
            parent = self._stack[-1] if self._stack else self.roots[-1]
            node = NavNode(title="")
            parent.children.append(node)
            self._stack.append(node)
        elif tag == "a" and self._stack and self._stack[-1].href is None:
            self._stack[-1].href = dict(attrs).get("href")
            self._capture, self._text = "link", []

    def handle_endtag(self, tag):
        if tag == "nav" and self._nav_depth:
            self._nav_depth -= 1
            return
        if not self._nav_depth:
            return
        text = " ".join("".join(self._text).split())
        if tag == "p" and self._capture == "caption":
            self.roots.append(NavNode(title=text))
            self._capture = None
        elif tag == "a" and self._capture == "link":
            self._stack[-1].title = text
            self._capture = None
        elif tag == "li" and self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if self._capture:
            self._text.append(data)


class _HeadingParser(HTMLParser):
    """Collect h1-h6 inside the page content (``article.bd-article``)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[Heading] = []
        self._article_depth = 0
        self._section_id: str | None = None
        self._current: tuple[int, str | None] | None = None
        self._text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag == "article":
            if self._article_depth or "bd-article" in _classes(attrs):
                self._article_depth += 1
            return
        if not self._article_depth:
            return
        if tag == "section":
            self._section_id = dict(attrs).get("id")
        elif len(tag) == 2 and tag[0] == "h" and tag[1] in "123456":
            self._current = (int(tag[1]), dict(attrs).get("id") or self._section_id)
            self._text = []
        elif tag == "a" and self._current and "headerlink" in _classes(attrs):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "article" and self._article_depth:
            self._article_depth -= 1
            return
        if not self._article_depth:
            return
        if tag == "a" and self._skip:
            self._skip -= 1
        elif self._current and tag == f"h{self._current[0]}":
            level, anchor = self._current
            text = " ".join("".join(self._text).split())
            self.headings.append(Heading(level, text, anchor))
            self._current = None

    def handle_data(self, data):
        if self._current and not self._skip:
            self._text.append(data)


def parse_nav(html: str, page: str = "index.html") -> list[NavNode]:
    """Parse the left-sidebar tree from a built page, resolving hrefs to build-dir paths."""
    parser = _NavParser()
    parser.feed(html)

    def resolve(node: NavNode) -> None:
        if node.href and "://" not in node.href:
            path = node.href.split("#", 1)[0]
            node.href = posixpath.normpath(
                posixpath.join(posixpath.dirname(page), path)
            )
        for child in node.children:
            resolve(child)

    for root in parser.roots:
        resolve(root)
    return [r for r in parser.roots if r.title or r.children]


def parse_headings(html: str) -> list[Heading]:
    parser = _HeadingParser()
    parser.feed(html)
    return parser.headings


def iter_nodes(nodes: list[NavNode]):
    for node in nodes:
        yield node
        yield from iter_nodes(node.children)


def check_page(page: str, headings: list[Heading]) -> list[Issue]:
    issues: list[Issue] = []

    def warn(code: str, message: str) -> None:
        issues.append(Issue("warning", code, page, message))

    titles = [h for h in headings if h.level == 1]
    if len(titles) != 1:
        issues.append(
            Issue(
                "error",
                "title",
                page,
                f"expected one page title (h1), found {len(titles)}",
            )
        )

    previous = 0
    ancestors: list[Heading] = []
    seen: set[tuple[tuple[str, ...], int, str]] = set()
    for h in headings:
        if previous and h.level > previous + 1:
            warn("heading-skip", f"'{h.text}' jumps from h{previous} to h{h.level}")
        if h.level > MAX_HEADING_LEVEL:
            warn(
                "deep-heading",
                f"'{h.text}' is h{h.level}; nest at most to h{MAX_HEADING_LEVEL}",
            )
        if len(h.text) > MAX_HEADING_CHARS:
            warn("long-heading", f"'{h.text[:40]}…' is {len(h.text)} characters")
        while ancestors and ancestors[-1].level >= h.level:
            ancestors.pop()
        key = (tuple(a.text for a in ancestors), h.level, h.text.lower())
        if key in seen:
            parent = ancestors[-1].text if ancestors else "the page"
            warn("duplicate-heading", f"'{h.text}' appears twice under '{parent}'")
        seen.add(key)
        ancestors.append(h)
        previous = h.level

    sections = sum(1 for h in headings if h.level == 2)
    if sections > MAX_SECTIONS:
        warn(
            "long-outline",
            f"{sections} top-level sections; consider splitting into subpages",
        )
    return issues


def check_nav(
    roots: list[NavNode], build_dir: Path, headings: dict[str, list[Heading]]
) -> list[Issue]:
    issues: list[Issue] = []
    for node in iter_nodes(roots):
        if node.href and not node.is_external and not (build_dir / node.href).exists():
            issues.append(
                Issue(
                    "error",
                    "missing-page",
                    node.href,
                    f"sidebar entry '{node.title}' links to a missing page",
                )
            )
        titles = [c.title for c in node.children]
        issues.extend(
            Issue(
                "warning",
                "duplicate-nav",
                node.href or node.title,
                f"'{title}' appears twice in the sidebar here",
            )
            for title in sorted({t for t in titles if titles.count(t) > 1})
        )
        pages = [c for c in node.children if c.href and not c.is_external]
        if node.href and len(pages) == 1 and node.href not in LONE_CHILD_ALLOWED:
            sections = sum(1 for h in headings.get(node.href, []) if h.level == 2)
            if sections >= LONE_CHILD_MIN_SECTIONS:
                issues.append(
                    Issue(
                        "warning",
                        "lone-child",
                        node.href,
                        f"'{node.title}' has {sections} sections but a single subpage "
                        f"'{pages[0].title}', which reads as its only subtopic",
                    )
                )
    return issues


def render_tree(
    roots: list[NavNode],
    headings: dict[str, list[Heading]] | None = None,
    depth: int = 3,
) -> str:
    lines: list[str] = []

    def walk(node: NavNode, indent: int) -> None:
        pad = "  " * indent
        location = f"  <{node.href}>" if node.href else ""
        lines.append(f"{pad}{node.title}{location}")
        if headings is not None and node.href:
            lines.extend(
                f"{pad}  {'#' * h.level} {h.text}"
                for h in headings.get(node.href, [])
                if 1 < h.level <= depth
            )
        for child in node.children:
            walk(child, indent + 1)

    for root in roots:
        if root.title:
            lines.append(f"== {root.title} ==")
        for child in root.children:
            walk(child, 1 if root.title else 0)
    return "\n".join(lines)


def render_outline(headings: list[Heading]) -> str:
    return "\n".join(
        f"{'  ' * (h.level - 1)}{'#' * h.level} {h.text}" for h in headings
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("build_dir", nargs="?", default="docs/_build/html", type=Path)
    parser.add_argument(
        "--headings", action="store_true", help="show each page's headings in the tree"
    )
    parser.add_argument(
        "--depth", type=int, default=3, help="deepest heading level shown (default: 3)"
    )
    parser.add_argument(
        "--page", help="show the full heading outline of one page, e.g. tools.html"
    )
    parser.add_argument(
        "--check", action="store_true", help="report structural problems"
    )
    parser.add_argument(
        "--strict", action="store_true", help="with --check, fail on warnings too"
    )
    args = parser.parse_args(argv)

    build_dir: Path = args.build_dir
    index = build_dir / "index.html"
    if not index.exists():
        print(
            f"error: {index} not found; build the docs first (make docs)",
            file=sys.stderr,
        )
        return 2

    if args.page:
        page = build_dir / args.page
        if not page.exists():
            print(f"error: {page} not found", file=sys.stderr)
            return 2
        print(render_outline(parse_headings(page.read_text(encoding="utf-8"))))
        return 0

    roots = parse_nav(index.read_text(encoding="utf-8"))
    pages = sorted(
        {
            n.href
            for n in iter_nodes(roots)
            if n.href and not n.is_external and (build_dir / n.href).exists()
        }
        | {"index.html"}
    )
    headings = {
        p: parse_headings((build_dir / p).read_text(encoding="utf-8")) for p in pages
    }

    if not args.check:
        print(render_tree(roots, headings if args.headings else None, args.depth))
        return 0

    issues = check_nav(roots, build_dir, headings)
    for p in pages:
        issues += check_page(p, headings[p])
    for issue in sorted(issues, key=lambda i: (i.severity != "error", i.page, i.code)):
        print(f"{issue.severity:7}  {issue.page}  [{issue.code}]  {issue.message}")
    errors = sum(i.severity == "error" for i in issues)
    warnings = len(issues) - errors
    print(f"{len(pages)} pages checked: {errors} errors, {warnings} warnings")
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
