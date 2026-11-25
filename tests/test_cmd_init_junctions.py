"""Directory junctions do not require symlink privileges on Windows."""

import os
import subprocess

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_init import main

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows junctions")


@pytest.mark.parametrize("directory", ["tasks", ".github", ".github/workflows"])
def test_force_rejects_junction_directories(tmp_path, directory):
    target = tmp_path / "proj"
    target.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "README.md").write_text("preserve me\n")
    (outside / "test.yml").write_text("preserve workflow\n")
    link = target / directory
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        check=True,
        capture_output=True,
    )
    try:
        result = CliRunner().invoke(main, [str(target), "--force", "--ci"])
        assert result.exit_code != 0
        assert "junction directory" in result.output
        assert (outside / "README.md").read_text() == "preserve me\n"
        assert (outside / "test.yml").read_text() == "preserve workflow\n"
        assert sorted(p.name for p in outside.iterdir()) == ["README.md", "test.yml"]
    finally:
        # Remove only the junction, never recursively remove its target.
        link.rmdir()


def test_force_preserves_junction_to_unwritten_directory(tmp_path):
    target = tmp_path / "proj"
    target.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link = target / "journal"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        check=True,
        capture_output=True,
    )
    try:
        result = CliRunner().invoke(main, [str(target), "--force"])
        assert result.exit_code == 0, result.output
        assert list(outside.iterdir()) == []
    finally:
        link.rmdir()
