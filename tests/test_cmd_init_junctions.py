"""Directory junctions do not require symlink privileges on Windows."""

import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_init import main

requires_windows = pytest.mark.skipif(os.name != "nt", reason="Windows junctions")


@requires_windows
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


@requires_windows
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


def test_force_rejects_mount_point_reparse_tag(tmp_path, monkeypatch):
    """Exercise the Windows lstat junction tag even on non-Windows CI."""
    from types import SimpleNamespace

    target = tmp_path / "proj"
    tasks = target / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "README.md").write_text("preserve me\n")
    original_lstat = Path.lstat

    def junction_lstat(path, *args, **kwargs):
        result = original_lstat(path, *args, **kwargs)
        if path == tasks:
            return SimpleNamespace(st_mode=result.st_mode, st_reparse_tag=0xA0000003)
        return result

    monkeypatch.setattr(Path, "lstat", junction_lstat)
    result = CliRunner().invoke(main, [str(target), "--force"])
    assert result.exit_code != 0
    assert "junction directory" in result.output
    assert (tasks / "README.md").read_text() == "preserve me\n"
