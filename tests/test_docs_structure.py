"""Tests for scripts/docs_structure.py (built-docs navigation and heading checks)."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "docs_structure.py"
_spec = importlib.util.spec_from_file_location("docs_structure", _SCRIPT)
assert _spec and _spec.loader
ds = importlib.util.module_from_spec(_spec)
# dataclasses look up their module in sys.modules while the module executes
sys.modules["docs_structure"] = ds
_spec.loader.exec_module(ds)

NAV = """
<nav class="bd-docs-nav" aria-label="Main">
  <p class="caption" role="heading"><span class="caption-text">Using gptme</span></p>
  <ul class="nav bd-sidenav">
    <li class="toctree-l1 has-children"><a class="reference internal" href="tools.html">Tools</a>
      <details><summary><i class="fa-solid fa-chevron-down"></i></summary><ul>
        <li class="toctree-l2"><a class="reference internal" href="tools/browser.html">Browser</a></li>
      </ul></details>
    </li>
    <li class="toctree-l1"><a class="reference internal" href="security.html">Security</a></li>
  </ul>
  <p class="caption" role="heading"><span class="caption-text">External</span></p>
  <ul class="nav bd-sidenav">
    <li class="toctree-l1"><a class="reference external" href="https://github.com/gptme/gptme">GitHub</a></li>
  </ul>
</nav>
"""


def page(body: str) -> str:
    return f"""
<html><body>
{NAV}
<article class="bd-article">
{body}
</article>
<div class="bd-sidebar-secondary"><h2> Contents </h2></div>
</body></html>
"""


def heading(level: int, text: str) -> str:
    anchor = text.lower().replace(" ", "-")
    return (
        f'<section id="{anchor}"><h{level}>{text}'
        f'<a class="headerlink" href="#{anchor}" title="Link to this heading">#</a></h{level}>'
        f"<p>text</p></section>"
    )


def test_parse_nav_captions_nesting_and_hrefs():
    roots = ds.parse_nav(page(""), page="index.html")
    assert [r.title for r in roots] == ["Using gptme", "External"]
    tools, security = roots[0].children
    assert (tools.title, tools.href) == ("Tools", "tools.html")
    assert [(c.title, c.href) for c in tools.children] == [
        ("Browser", "tools/browser.html")
    ]
    assert security.children == []
    assert roots[1].children[0].is_external


def test_parse_nav_resolves_relative_hrefs_from_subpage():
    html = page("").replace('href="tools.html"', 'href="../tools.html"')
    roots = ds.parse_nav(html, page="tools/browser.html")
    assert roots[0].children[0].href == "tools.html"


def test_parse_headings_ignores_headerlinks_and_sidebar():
    headings = ds.parse_headings(page(heading(1, "Tools") + heading(2, "Overview")))
    assert [(h.level, h.text, h.anchor) for h in headings] == [
        (1, "Tools", "tools"),
        (2, "Overview", "overview"),
    ]


def test_check_page_flags_skips_depth_duplicates_and_titles():
    headings = [
        ds.Heading(1, "Page"),
        ds.Heading(3, "Skipped"),
        ds.Heading(2, "Setup"),
        ds.Heading(3, "Linux"),
        ds.Heading(4, "Deep"),
        ds.Heading(5, "Too deep"),
        ds.Heading(2, "Setup"),
        ds.Heading(1, "Second title"),
    ]
    codes = [i.code for i in ds.check_page("p.html", headings)]
    assert codes.count("title") == 1
    assert "heading-skip" in codes
    assert "deep-heading" in codes
    assert "duplicate-heading" in codes


def test_check_page_allows_same_heading_under_different_parents():
    headings = [
        ds.Heading(1, "Page"),
        ds.Heading(2, "Linux"),
        ds.Heading(3, "Install"),
        ds.Heading(2, "macOS"),
        ds.Heading(3, "Install"),
    ]
    assert ds.check_page("p.html", headings) == []


@pytest.fixture
def build_dir(tmp_path):
    (tmp_path / "tools").mkdir()
    sections = "".join(heading(2, f"Section {i}") for i in range(3))
    (tmp_path / "index.html").write_text(page(heading(1, "Home")))
    (tmp_path / "tools.html").write_text(page(heading(1, "Tools") + sections))
    (tmp_path / "tools" / "browser.html").write_text(page(heading(1, "Browser")))
    return tmp_path


def test_check_nav_flags_lone_child_and_missing_page(build_dir):
    roots = ds.parse_nav((build_dir / "index.html").read_text())
    headings = {
        "tools.html": ds.parse_headings((build_dir / "tools.html").read_text()),
    }
    issues = ds.check_nav(roots, build_dir, headings)
    assert [(i.code, i.page) for i in issues] == [
        ("lone-child", "tools.html"),
        ("missing-page", "security.html"),
    ]


def test_render_tree_with_headings(build_dir):
    roots = ds.parse_nav((build_dir / "index.html").read_text())
    headings = {"tools.html": ds.parse_headings((build_dir / "tools.html").read_text())}
    tree = ds.render_tree(roots, headings, depth=2)
    assert tree.splitlines()[:4] == [
        "== Using gptme ==",
        "  Tools  <tools.html>",
        "    ## Section 0",
        "    ## Section 1",
    ]
    assert "    Browser  <tools/browser.html>" in tree


def test_main_check_exit_codes(build_dir, capsys):
    # missing security.html is an error
    assert ds.main([str(build_dir), "--check"]) == 1
    (build_dir / "security.html").write_text(page(heading(1, "Security")))
    # only the lone-child warning remains
    assert ds.main([str(build_dir), "--check"]) == 0
    assert ds.main([str(build_dir), "--check", "--strict"]) == 1
    assert "lone-child" in capsys.readouterr().out


def test_check_nav_flags_duplicate_titles_across_sections(build_dir):
    html = page("").replace(
        '<span class="caption-text">External</span>',
        '<span class="caption-text">Tools</span>',
    )
    roots = ds.parse_nav(html)
    issues = [
        i for i in ds.check_nav(roots, build_dir, {}) if i.code == "duplicate-title"
    ]
    assert len(issues) == 1
    assert "'Tools' appears 2 times" in issues[0].message
