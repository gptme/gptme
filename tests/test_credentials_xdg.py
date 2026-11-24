"""credentials.py resolves XDG_CONFIG_HOME without the empty/relative guard
that dirs.py applies, so an empty XDG_CONFIG_HOME stores API keys under CWD."""

from pathlib import Path

import pytest

from gptme.credentials import _get_credentials_path, set_stored_api_key


@pytest.mark.parametrize("xdg", ["", "   ", "relative/cfg", "~/cfg"])
def test_credentials_path_not_relative_to_cwd(monkeypatch, tmp_path: Path, xdg):
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "project"
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
    monkeypatch.chdir(cwd)

    path = set_stored_api_key("anthropic", "sk-ant-secret-1234")

    assert path.is_absolute(), f"credentials path is relative: {path!r}"
    leaked = list(cwd.rglob("credentials.toml"))
    assert not leaked, f"API key written inside the working directory: {leaked}"
    assert path == _get_credentials_path()
