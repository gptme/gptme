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


@pytest.mark.parametrize("directory_link", [False, True])
@pytest.mark.parametrize("glob_pattern", [False, True])
def test_context_files_skips_relative_symlinks_outside_workspace(
    tmp_path: Path, directory_link: bool, glob_pattern: bool
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "private"
    outside.mkdir()
    secret = outside / "notes.md"
    secret.write_text("outside private content\n")
    guide = workspace / "guide.md"
    guide.write_text("public guide\n")
    docs = workspace / "docs"
    try:
        if directory_link:
            docs.symlink_to(outside, target_is_directory=True)
        else:
            docs.mkdir()
            (docs / "notes.md").symlink_to(secret)
        (workspace / "guide-alias.md").symlink_to(guide)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    entry = "docs/*.md" if glob_pattern else "docs/notes.md"
    result = _invoke(
        workspace,
        f'[prompt]\nfiles = ["{entry}", "guide-alias.md", "guide.md"]\n',
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    assert "outside private content" not in result.output
    assert "## FILE: docs/notes.md" not in result.output
    # In-workspace symlinks still work, with resolved-path deduplication.
    assert result.output.count("public guide") == 1
    assert "## FILE: guide-alias.md" in result.output
    # Skipping a relative alias must not suppress an explicit external entry.
    result = _invoke(
        workspace,
        f'[prompt]\nfiles = ["{entry}", "{secret.as_posix()}"]\n',
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    assert result.output.count("outside private content") == 1
    assert f"## FILE: {secret}" in result.output
    assert "## FILE: docs/notes.md" not in result.output


@pytest.mark.parametrize("excluded", [False, True])
def test_context_files_reads_explicit_parent_relative_file(
    tmp_path: Path, excluded: bool
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shared = tmp_path / "shared" / "guide.md"
    shared.parent.mkdir()
    shared.write_text("shared instructions\n")
    exclusion = 'exclude = ["guide.md"]\n' if excluded else ""
    result = _invoke(
        workspace,
        '[prompt]\nfiles = ["../shared/guide.md"]\n' + exclusion,
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    if excluded:
        assert "shared instructions" not in result.output
    else:
        assert "## FILE: ../shared/guide.md" in result.output
        assert "shared instructions" in result.output


def test_context_files_keeps_parent_relative_glob_contained(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "private" / "notes.md"
    secret.parent.mkdir()
    secret.write_text("outside private content\n")
    result = _invoke(workspace, '[prompt]\nfiles = ["../private/*.md"]\n')
    assert result.exit_code == 0, result.output + str(result.exception)
    assert "outside private content" not in result.output


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
@pytest.mark.parametrize("parent_components", [False, True])
def test_context_files_excludes_symlink_by_configured_path(
    tmp_path: Path, absolute: bool, parent_components: bool
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
    entry_path = Path("private/notes.md")
    if parent_components:
        (tmp_path / "docs").mkdir()
        entry_path = Path("docs/..") / entry_path
    entry = (tmp_path / entry_path).as_posix() if absolute else entry_path.as_posix()
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


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("excluded", ["secret.rst", "secret.*"])
def test_context_files_excludes_symlink_by_target_basename(
    tmp_path: Path, external: bool, excluded: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target_root = tmp_path if external else workspace
    secret = target_root / "private" / "secret.rst"
    secret.parent.mkdir()
    secret.write_text("excluded secret content\n")
    alias = workspace / "public" / "notes.rst"
    alias.parent.mkdir()
    try:
        alias.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    # Explicit external aliases must reach exclusion matching, not be rejected
    # by the relative-path containment guard before the exclusion is evaluated.
    entry = alias.as_posix() if external else "public/notes.rst"
    allowed = _invoke(workspace, f'[prompt]\nfiles = ["{entry}"]\n')
    assert allowed.exit_code == 0, allowed.output + str(allowed.exception)
    assert "excluded secret content" in allowed.output
    result = _invoke(
        workspace,
        f'[prompt]\nfiles = ["{entry}", "guide.rst"]\nexclude = ["{excluded}"]\n',
        {"guide.rst": "public guide\n"},
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    assert "excluded secret content" not in result.output
    assert "public/notes.rst" not in result.output
    assert "public guide" in result.output


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
