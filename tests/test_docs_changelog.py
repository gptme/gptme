"""Check that stable release notes appear in the rendered Sphinx changelog."""

import shutil
import subprocess
import sys
from pathlib import Path


def test_stable_release_notes_in_published_changelog(tmp_path: Path) -> None:
    docs = Path(__file__).resolve().parents[1] / "docs"
    source = tmp_path / "source"
    source.mkdir()
    shutil.copyfile(docs / "changelog.rst", source / "changelog.rst")
    shutil.copytree(docs / "releases", source / "releases")
    # Build the actual changelog tree without unrelated autodoc and network inputs.
    (source / "conf.py").write_text(
        "extensions = ['myst_parser']\nroot_doc = 'changelog'\n", encoding="utf-8"
    )
    output = tmp_path / "html"
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "sphinx.cmd.build",
            "-b",
            "html",
            "-W",
            str(source),
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    changelog = (output / "changelog.html").read_text(encoding="utf-8")
    release_links = [
        f'href="releases/{version}.html"'
        for version in ("v0.37.0", "v0.36.0", "v0.35.1")
    ]
    for version, link in zip(
        ("v0.37.0", "v0.36.0", "v0.35.1"), release_links, strict=True
    ):
        assert link in changelog, f"{version} is missing from the rendered changelog"
        page = (output / "releases" / f"{version}.html").read_text(encoding="utf-8")
        assert f"These are the release notes for gptme version {version}." in page
    newest, previous, existing = map(changelog.index, release_links)
    assert newest < previous < existing
