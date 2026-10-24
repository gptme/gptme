"""gptme-util context files: emit the same files the prompt loader would include."""

from pathlib import Path

from click.testing import CliRunner

from gptme.cli.util import main


def _invoke(tmp_path: Path, toml: str, extra_files: dict[str, str] | None = None):
    (tmp_path / "gptme.toml").write_text(toml)
    for rel, content in (extra_files or {}).items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return CliRunner().invoke(
        main, ["context", "files", "--config", str(tmp_path / "gptme.toml")]
    )


def test_context_files_reads_legacy_top_level_files(tmp_path: Path):
    """Canonical gptme.toml uses prompt = "..." plus top-level files = [...]."""
    result = _invoke(
        tmp_path,
        'prompt = "This is gptme"\nfiles = ["README.md"]\n',
        {"README.md": "hello from readme\n"},
    )
    assert result.exit_code == 0, result.output + result.stderr
    assert "AttributeError" not in (result.output + result.stderr)
    assert "## FILE: README.md" in result.output
    assert "hello from readme" in result.output


def test_context_files_reads_prompt_section(tmp_path: Path):
    result = _invoke(
        tmp_path,
        '[prompt]\nfiles = ["ABOUT.md"]\n',
        {"ABOUT.md": "about bob\n"},
    )
    assert result.exit_code == 0, result.output
    assert "## FILE: ABOUT.md" in result.output
    assert "about bob" in result.output


def test_context_files_expands_workspace_globs(tmp_path: Path):
    result = _invoke(
        tmp_path,
        'files = ["src/types/*.ts"]\n',
        {
            "src/types/a.ts": "export type A = 1\n",
            "src/types/b.ts": "export type B = 2\n",
            "src/types/skip.md": "not a ts file\n",
        },
    )
    assert result.exit_code == 0, result.output + result.stderr
    assert "## FILE: src/types/a.ts" in result.output
    assert "## FILE: src/types/b.ts" in result.output
    assert "export type A = 1" in result.output
    assert "export type B = 2" in result.output
    assert "skip.md" not in result.output
    assert "(not found)" not in result.output + result.stderr
