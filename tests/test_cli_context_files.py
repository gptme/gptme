"""gptme-util context files: emit the same files the prompt loader would include."""

from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from gptme.cli.util import main


def _invoke(
    tmp_path: Path, toml: str, extra_files: dict[str, str] | None = None
) -> Result:
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


@pytest.mark.parametrize("home_relative", [False, True])
def test_context_files_reads_external_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home_relative: bool
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    notes = tmp_path / "notes.md"
    notes.write_text("shared notes\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    entry = "~/notes.md" if home_relative else notes.as_posix()
    result = _invoke(workspace, f'[prompt]\nfiles = ["{entry}"]\n')
    assert result.exit_code == 0, result.output + str(result.exception)
    assert f"## FILE: {notes}" in result.output
    assert "shared notes" in result.output


@pytest.mark.parametrize("excluded", ["docs/releases/*", "private.rst"])
def test_context_files_honors_exclusions(tmp_path: Path, excluded: str) -> None:
    result = _invoke(
        tmp_path,
        f'[prompt]\nfiles = ["docs/**/*.rst"]\nexclude = ["{excluded}"]\n',
        {
            "docs/guide.rst": "public guide\n",
            "docs/releases/private.rst": "excluded content\n",
        },
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    assert "public guide" in result.output
    assert "private.rst" not in result.output
    assert "excluded content" not in result.output
    assert "(not found)" not in result.output + result.stderr


@pytest.mark.parametrize("absolute", [False, True])
def test_context_files_excludes_symlink_by_configured_path(
    tmp_path: Path, absolute: bool
) -> None:
    shared = tmp_path / "shared" / "notes.md"
    shared.parent.mkdir()
    shared.write_text("shared notes\n")
    private = tmp_path / "private" / "notes.md"
    private.parent.mkdir()
    try:
        private.symlink_to(shared)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    entry = private.as_posix() if absolute else "private/notes.md"
    result = _invoke(
        tmp_path,
        f'[prompt]\nfiles = ["{entry}"]\nexclude = ["private/*"]\n',
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    assert "shared notes" not in result.output
    result = _invoke(
        tmp_path,
        f'[prompt]\nfiles = ["{entry}", "shared/notes.md"]\nexclude = ["private/*"]\n',
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    # The excluded alias must not suppress its allowed entry.
    assert result.output.count("shared notes") == 1
    assert "## FILE: shared/notes.md" in result.output
    assert "private/notes.md" not in result.output


def test_context_files_excludes_absolute_path_with_parent_components(
    tmp_path: Path,
) -> None:
    (tmp_path / "docs").mkdir()
    entry = (tmp_path / "docs" / ".." / "private" / "notes.md").as_posix()
    result = _invoke(
        tmp_path,
        f'[prompt]\nfiles = ["{entry}"]\nexclude = ["private/*"]\n',
        {"private/notes.md": "excluded content\n"},
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    assert "excluded content" not in result.output
