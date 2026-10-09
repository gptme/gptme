#!/usr/bin/env python3
"""Check that gptme-org ``blob``/``tree``/``master`` links in the docs point at real paths.

Sphinx linkcheck skips these links (see ``linkcheck_ignore`` in docs/conf.py):
github.com throttles CI runners with 429s on blob/tree pages, which turned the
daily linkcheck red on links nobody had touched. This script checks the same
thing without hitting those pages. gptme/gptme paths are checked against this
checkout. Paths in other gptme repos are checked against a blobless shallow
clone, which costs one git fetch per repo.

Usage: python3 scripts/check_docs_repo_links.py
"""

import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["docs", "gptme"]
SCAN_SUFFIXES = {".rst", ".md", ".py"}
# Generated release notes link real refs; linkcheck excludes them too.
EXCLUDE = re.compile(r"^docs/(releases|_build)/")
LINK = re.compile(
    r"https://github\.com/gptme/([A-Za-z0-9_.-]+)/(?:blob|tree)/master/([^\s#?)>`\"'\]]*)"
)


def find_links() -> dict[str, dict[str, set[str]]]:
    """Return {repo: {path: {"file:line", ...}}}."""
    links: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for scan_dir in SCAN_DIRS:
        for f in sorted((ROOT / scan_dir).rglob("*")):
            rel = f.relative_to(ROOT).as_posix()
            if f.suffix not in SCAN_SUFFIXES or EXCLUDE.match(rel) or not f.is_file():
                continue
            for lineno, line in enumerate(
                f.read_text(errors="replace").splitlines(), 1
            ):
                for repo, path in LINK.findall(line):
                    # RST trailing "_" (``link <url>`_``) and sentence punctuation
                    links[repo][path.rstrip("/._,;:")].add(f"{rel}:{lineno}")
    return links


def tree_paths(git_dir: Path, ref: str) -> set[str]:
    """All file and directory paths at ``ref``."""
    out = subprocess.run(
        ["git", "-C", str(git_dir), "ls-tree", "-r", "-t", "--name-only", ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return set(out.splitlines()) | {""}


def remote_tree_paths(repo: str, tmp: Path) -> set[str]:
    dest = tmp / repo
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--depth=1",
            "--filter=blob:none",
            "--no-checkout",
            "--branch=master",
            f"https://github.com/gptme/{repo}.git",
            str(dest),
        ],
        check=True,
    )
    return tree_paths(dest, "HEAD")


def main() -> int:
    links = find_links()
    broken: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for repo, paths in sorted(links.items()):
            # Own repo: check the commit under test, not the published master.
            existing = (
                tree_paths(ROOT, "HEAD")
                if repo == "gptme"
                else remote_tree_paths(repo, Path(tmp))
            )
            for path, refs in sorted(paths.items()):
                if path not in existing:
                    broken.extend(
                        f"{ref}: gptme/{repo}@master has no {path!r}"
                        for ref in sorted(refs)
                    )
    total = sum(len(p) for p in links.values())
    for msg in broken:
        print(f"broken: {msg}")
    print(f"checked {total} gptme repo link path(s) in {len(links)} repo(s)")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
