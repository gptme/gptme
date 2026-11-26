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


@pytest.mark.parametrize("linked_parent", [False, True])
def test_force_rejects_mount_point_reparse_tag(tmp_path, monkeypatch, linked_parent):
    """Exercise the Windows lstat junction tag even on non-Windows CI."""
    from types import SimpleNamespace

    if linked_parent:
        real_parent = tmp_path / "real"
        real_parent.mkdir()
        alias = tmp_path / "alias"
        try:
            alias.symlink_to(real_parent, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"Directory symlinks unavailable: {exc}")
        tmp_path = alias

    target = tmp_path / "proj"
    tasks = target / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "README.md").write_text("preserve me\n")
    original_lstat = Path.lstat
    resolved_tasks = tasks.resolve()

    def junction_lstat(path, *args, **kwargs):
        result = original_lstat(path, *args, **kwargs)
        if path == resolved_tasks:
            return SimpleNamespace(st_mode=result.st_mode, st_reparse_tag=0xA0000003)
        return result

    monkeypatch.setattr(Path, "lstat", junction_lstat)
    result = CliRunner().invoke(main, [str(target), "--force"])
    assert result.exit_code != 0
    assert "junction directory" in result.output
    assert (tasks / "README.md").read_text() == "preserve me\n"


@pytest.mark.parametrize("copy_instructions", [False, True])
def test_scaffold_uses_utf8_on_legacy_locale(tmp_path, monkeypatch, copy_instructions):
    """Model Windows' cp1252 default even when tests run on a UTF-8 host."""
    import io

    original_text_encoding = io.text_encoding

    def legacy_text_encoding(encoding, stacklevel=2):
        return original_text_encoding(encoding or "cp1252", stacklevel)

    monkeypatch.setattr(io, "text_encoding", legacy_text_encoding)
    if copy_instructions:

        def no_symlinks(*args, **kwargs):
            raise OSError("Symlinks unavailable")

        monkeypatch.setattr(Path, "symlink_to", no_symlinks)
    target = tmp_path / "proj"
    result = CliRunner().invoke(main, [str(target), "--name", "项目", "--ci"])
    assert result.exit_code == 0, result.output
    for filename in ("gptme.toml", "AGENTS.md", "README.md", "CLAUDE.md"):
        assert "项目" in (target / filename).read_text(encoding="utf-8")
    assert "backlog → todo" in (target / "tasks/README.md").read_text(encoding="utf-8")
