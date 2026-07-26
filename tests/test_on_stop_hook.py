"""Tests for the on_stop session-end hook."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.config.models import ProjectConfig
from gptme.hooks import HookType, clear_hooks, trigger_hook


@pytest.fixture(autouse=True)
def clear_all_hooks():
    clear_hooks()
    yield
    clear_hooks()


def _make_manager(logdir: Path) -> MagicMock:
    manager = MagicMock()
    manager.logdir = logdir
    return manager


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_project_config_on_stop_from_prompt_section():
    """on_stop is parsed from the [prompt] section."""
    config = ProjectConfig.from_dict({"prompt": {"on_stop": "scripts/save-context.sh"}})
    assert config.on_stop == "scripts/save-context.sh"


def test_project_config_on_stop_defaults_none():
    """on_stop defaults to None when absent."""
    config = ProjectConfig.from_dict({"prompt": {"context_cmd": "scripts/context.sh"}})
    assert config.on_stop is None


# ---------------------------------------------------------------------------
# Hook behaviour
# ---------------------------------------------------------------------------


def test_on_stop_hook_runs_command(tmp_path):
    """on_stop subprocess is called with correct args."""
    from gptme.cli.main import _register_on_stop_hook

    _register_on_stop_hook("echo hello", tmp_path)

    manager = _make_manager(tmp_path / "session-abc")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        list(trigger_hook(HookType.SESSION_END, manager=manager))

    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args
    assert call_kwargs.kwargs["shell"] is True
    assert call_kwargs.kwargs["cwd"] == tmp_path
    assert "GPTME_LOGDIR" in call_kwargs.kwargs["env"]
    assert str(tmp_path / "session-abc") == call_kwargs.kwargs["env"]["GPTME_LOGDIR"]


def test_on_stop_hook_runs_on_session_complete_path(tmp_path):
    """on_stop fires on the SessionCompleteException path (same trigger_hook call)."""
    from gptme.cli.main import _register_on_stop_hook

    called = []

    def fake_run(*args, **kwargs):
        called.append(kwargs.get("args", args))
        return MagicMock(returncode=0, stderr="")

    _register_on_stop_hook("touch done", tmp_path)
    manager = _make_manager(tmp_path / "session-xyz")

    with patch("subprocess.run", side_effect=fake_run):
        list(trigger_hook(HookType.SESSION_END, manager=manager))

    assert len(called) == 1


def test_on_stop_hook_failure_is_logged_not_raised(tmp_path, caplog):
    """A failing on_stop command logs a warning but does not raise."""
    from gptme.cli.main import _register_on_stop_hook

    _register_on_stop_hook("exit 1", tmp_path)
    manager = _make_manager(tmp_path / "session-err")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="something went wrong")
        import logging

        with caplog.at_level(logging.WARNING, logger="gptme.cli.main"):
            list(trigger_hook(HookType.SESSION_END, manager=manager))

    assert any("on_stop command failed" in r.message for r in caplog.records)


def test_on_stop_hook_timeout_is_logged_not_raised(tmp_path, caplog):
    """A timed-out on_stop command logs a warning but does not raise."""
    from gptme.cli.main import _register_on_stop_hook

    _register_on_stop_hook("sleep 9999", tmp_path)
    manager = _make_manager(tmp_path / "session-timeout")

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sleep", 30)):
        import logging

        with caplog.at_level(logging.WARNING, logger="gptme.cli.main"):
            list(trigger_hook(HookType.SESSION_END, manager=manager))

    assert any("timed out" in r.message for r in caplog.records)


def test_on_stop_hook_passes_model_from_registration(tmp_path):
    """Model passed at registration time takes precedence over GPTME_MODEL env."""
    from gptme.cli.main import _register_on_stop_hook

    _register_on_stop_hook("echo model", tmp_path, model="anthropic/claude-opus-5")
    manager = _make_manager(tmp_path / "session-model")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        list(trigger_hook(HookType.SESSION_END, manager=manager))

    env = mock_run.call_args.kwargs["env"]
    assert env["GPTME_MODEL"] == "anthropic/claude-opus-5"
