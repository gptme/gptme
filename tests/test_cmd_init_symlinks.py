"""`gptme init --force` must not write through existing symlinks."""

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
