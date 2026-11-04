"""Observational config loads resolve paths without initializing workspaces."""

from pathlib import Path

import pytest

from gptme.config import ChatConfig


@pytest.mark.parametrize("configured", [False, True])
def test_noncreating_load_resolves_workspace(tmp_path: Path, configured: bool) -> None:
    logdir = tmp_path / "history"
    logdir.mkdir()
    if configured:
        (logdir / "config.toml").write_text(
            '[chat]\nname = "Saved name"\nworkspace = "@log"\n', encoding="utf-8"
        )
    before = {p.name: p.read_bytes() for p in logdir.iterdir()}

    config = ChatConfig.from_logdir(logdir, create_workspace=False)

    assert config.workspace == (logdir / "workspace").resolve()
    assert config.name == ("Saved name" if configured else None)
    assert {p.name: p.read_bytes() for p in logdir.iterdir()} == before


@pytest.mark.parametrize("configured", [False, True])
def test_default_load_still_initializes_workspace(
    tmp_path: Path, configured: bool
) -> None:
    logdir = tmp_path / "history"
    logdir.mkdir()
    if configured:
        (logdir / "config.toml").write_text(
            '[chat]\nworkspace = "@log"\n', encoding="utf-8"
        )

    config = ChatConfig.from_logdir(logdir)

    assert config.workspace == logdir / "workspace"
    assert config.workspace.is_dir()


def test_noncreating_load_does_not_resurrect_removed_history(tmp_path: Path) -> None:
    logdir = tmp_path / "removed"

    config = ChatConfig.from_logdir(logdir, create_workspace=False)

    assert config.workspace == logdir / "workspace"
    assert not logdir.exists()
