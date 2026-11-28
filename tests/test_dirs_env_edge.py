"""dirs.py edge-case env values."""

from pathlib import Path

import pytest

from gptme import dirs


def _isolate(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "project"
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)
    return home, cwd


def test_logs_home_tilde_is_expanded(monkeypatch, tmp_path: Path):
    """GPTME_LOGS_HOME='~/gptme-logs' (quoted in a shell rc, .env file, systemd
    Environment=, docker -e) must not create a literal '~' dir under CWD."""
    home, cwd = _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("GPTME_LOGS_HOME", "~/gptme-logs")

    logs = dirs.get_logs_dir()

    assert not (cwd / "~").exists(), "created literal ./~ directory in CWD"
    assert logs == home / "gptme-logs"


def test_workspace_env_tilde_is_expanded(monkeypatch, tmp_path: Path):
    home, cwd = _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("GPTME_WORKSPACE", "~/ws")
    assert dirs.get_workspace() == home / "ws"


@pytest.mark.parametrize(
    ("var", "func"),
    [("XDG_DATA_HOME", "get_data_dir"), ("XDG_STATE_HOME", "get_state_dir")],
)
def test_relative_xdg_home_is_ignored(monkeypatch, tmp_path: Path, var, func):
    """XDG spec: relative paths in XDG_*_HOME are invalid and must be ignored.
    Neither gptme's override nor the platformdirs fallback may resolve them
    against CWD, where logs/state would move with the working directory."""
    home, cwd = _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv(var, "relative/share")

    result = getattr(dirs, func)()

    assert result.is_absolute(), f"{func}() returned CWD-relative path {result!r}"


@pytest.mark.parametrize(
    ("func", "platform_func", "default"),
    [
        ("get_data_dir", "user_data_dir", ".local/share/gptme"),
        ("get_state_dir", "user_state_dir", ".local/state/gptme"),
    ],
)
def test_relative_platformdirs_fallback_is_ignored(
    monkeypatch, tmp_path: Path, func, platform_func, default
):
    """Supported platformdirs 4.11 still returns relative XDG overrides."""
    home, _ = _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(dirs, platform_func, lambda app: "relative/share/" + app)

    assert getattr(dirs, func)() == home / default
