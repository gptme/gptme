"""`gptme init --force` must not write through existing symlinks."""

from pathlib import Path

import pytest
from click.testing import CliRunner

import gptme.cli.cmd_init as cmd_init

pytestmark = pytest.mark.usefixtures("requires_symlinks")


def test_dangling_claude_symlink_writes_outside_project(tmp_path):
    """A dangling CLAUDE.md symlink makes the copy fallback write through the
    link to an arbitrary path outside the project."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    victim = outside / "notes.md"  # does not exist yet -> dangling link
    target = tmp_path / "proj"
    target.mkdir()
    (target / "CLAUDE.md").symlink_to(victim)

    r = CliRunner().invoke(cmd_init.main, [str(target), "--force"])
    assert r.exit_code == 0, r.output
    assert not victim.exists(), (
        f"gptme init wrote {victim} outside the project: {victim.read_text()[:40]!r}"
    )
    # The broken link is replaced, so the project instructions are readable.
    claude = target / "CLAUDE.md"
    assert claude.exists()
    assert claude.read_text() == (target / "AGENTS.md").read_text()


def test_force_does_not_clobber_claude_md_through_agents_symlink(tmp_path):
    """Common layout: real CLAUDE.md + AGENTS.md -> CLAUDE.md. The code skips
    CLAUDE.md to preserve it, but writing AGENTS.md follows the link."""
    target = tmp_path / "proj"
    target.mkdir()
    (target / "CLAUDE.md").write_text("MY HAND-WRITTEN INSTRUCTIONS\n")
    (target / "AGENTS.md").symlink_to("CLAUDE.md")

    r = CliRunner().invoke(cmd_init.main, [str(target), "--force"])
    assert r.exit_code == 0, r.output
    assert (target / "CLAUDE.md").read_text() == "MY HAND-WRITTEN INSTRUCTIONS\n"


@pytest.mark.parametrize("directory", ["tasks", ".github", ".github/workflows"])
@pytest.mark.parametrize("dangling", [False, True])
def test_force_rejects_symlink_directories(
    tmp_path: Path, directory: str, dangling: bool
) -> None:
    target = tmp_path / "proj"
    target.mkdir()
    outside = tmp_path / "elsewhere"
    if not dangling:
        outside.mkdir()
        (outside / "README.md").write_text("preserve me\n")
    link = target / directory
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)

    result = CliRunner().invoke(cmd_init.main, [str(target), "--force", "--ci"])

    assert result.exit_code != 0
    assert "symlink directory" in result.output
    assert link.is_symlink()
    if dangling:
        assert not outside.exists()
    else:
        assert (outside / "README.md").read_text() == "preserve me\n"
        assert sorted(p.name for p in outside.iterdir()) == ["README.md"]


@pytest.mark.parametrize("directory", ["journal", "knowledge", "lessons", "skills"])
def test_force_preserves_links_to_unwritten_directories(tmp_path, directory):
    target = tmp_path / "proj"
    target.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "notes.md").write_text("preserve me\n")
    link = target / directory
    link.symlink_to(outside, target_is_directory=True)

    result = CliRunner().invoke(
        cmd_init.main, [str(target), "--force", "--lessons", "--skills"]
    )

    assert result.exit_code == 0, result.output
    assert link.is_symlink()
    assert (target / "gptme.toml").is_file()
    assert (outside / "notes.md").read_text() == "preserve me\n"
    assert sorted(p.name for p in outside.iterdir()) == ["notes.md"]


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
    result = CliRunner().invoke(cmd_init.main, [str(target), "--force"])
    assert result.exit_code != 0
    assert "junction directory" in result.output
    assert (tasks / "README.md").read_text() == "preserve me\n"
