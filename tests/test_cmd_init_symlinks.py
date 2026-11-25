"""`gptme init --force` must not write through existing symlinks."""

from pathlib import Path

import pytest
from click.testing import CliRunner

import gptme.cli.cmd_init as cmd_init


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
