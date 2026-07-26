"""Tests for the on_stop session-end hook."""

import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.config.models import ProjectConfig
from gptme.hooks import HookType, clear_hooks, get_hooks, trigger_hook


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
    completed = threading.Event()

    def fake_run(*args, **kwargs):
        completed.set()
        return MagicMock(returncode=0, stderr="")

    with patch("subprocess.run", side_effect=fake_run) as mock_run:
        list(trigger_hook(HookType.SESSION_END, manager=manager))
        assert completed.wait(timeout=1)

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
    completed = threading.Event()

    def fake_run(*args, **kwargs):
        called.append(kwargs.get("args", args))
        completed.set()
        return MagicMock(returncode=0, stderr="")

    _register_on_stop_hook("touch done", tmp_path)
    manager = _make_manager(tmp_path / "session-xyz")

    with patch("subprocess.run", side_effect=fake_run):
        list(trigger_hook(HookType.SESSION_END, manager=manager))
        assert completed.wait(timeout=1)

    assert len(called) == 1


def test_on_stop_hook_failure_is_logged_not_raised(tmp_path, caplog):
    """A failing on_stop command logs a warning but does not raise."""
    from gptme.cli.main import _register_on_stop_hook

    _register_on_stop_hook("exit 1", tmp_path)
    manager = _make_manager(tmp_path / "session-err")

    completed = threading.Event()

    def failing_run(*args, **kwargs):
        completed.set()
        return MagicMock(returncode=1, stderr="something went wrong")

    with patch("subprocess.run", side_effect=failing_run):
        import logging

        with caplog.at_level(logging.WARNING, logger="gptme.cli.main"):
            list(trigger_hook(HookType.SESSION_END, manager=manager))
            assert completed.wait(timeout=1)

    assert any("on_stop command failed" in r.message for r in caplog.records)


def test_on_stop_hook_timeout_is_logged_not_raised(tmp_path, caplog):
    """A timed-out on_stop command logs a warning but does not raise."""
    from gptme.cli.main import _register_on_stop_hook

    _register_on_stop_hook("sleep 9999", tmp_path)
    manager = _make_manager(tmp_path / "session-timeout")

    completed = threading.Event()

    def timing_out_run(*args, **kwargs):
        completed.set()
        raise subprocess.TimeoutExpired("sleep", 30)

    with patch("subprocess.run", side_effect=timing_out_run):
        import logging

        with caplog.at_level(logging.WARNING, logger="gptme.cli.main"):
            list(trigger_hook(HookType.SESSION_END, manager=manager))
            assert completed.wait(timeout=1)

    assert any("timed out" in r.message for r in caplog.records)


def test_on_stop_hook_passes_model_from_registration(tmp_path):
    """Model passed at registration time takes precedence over GPTME_MODEL env."""
    from gptme.cli.main import _register_on_stop_hook

    _register_on_stop_hook("echo model", tmp_path, model="anthropic/claude-opus-5")
    manager = _make_manager(tmp_path / "session-model")

    completed = threading.Event()

    def fake_run(*args, **kwargs):
        completed.set()
        return MagicMock(returncode=0, stderr="")

    with patch("subprocess.run", side_effect=fake_run) as mock_run:
        list(trigger_hook(HookType.SESSION_END, manager=manager))
        assert completed.wait(timeout=1)

    env = mock_run.call_args.kwargs["env"]
    assert env["GPTME_MODEL"] == "anthropic/claude-opus-5"


def test_on_stop_hook_is_non_blocking(tmp_path):
    """SESSION_END returns without waiting for the command to finish."""
    from gptme.cli.main import _register_on_stop_hook

    _register_on_stop_hook("sleep forever", tmp_path, model="test/model")
    hook = get_hooks(HookType.SESSION_END)[0]
    assert hook.async_mode is True

    started = threading.Event()
    release = threading.Event()

    def blocking_run(*args, **kwargs):
        started.set()
        assert release.wait(timeout=1)
        return MagicMock(returncode=0, stderr="")

    manager = _make_manager(tmp_path / "session-async")
    with patch("subprocess.run", side_effect=blocking_run):
        list(trigger_hook(HookType.SESSION_END, manager=manager))
        assert started.wait(timeout=1)
        assert not release.is_set()
        release.set()


def test_on_stop_hook_uses_effective_config_model(tmp_path):
    """Registration accepts the resolved model from project or resumed config."""
    from gptme.cli.main import _register_on_stop_hook

    _register_on_stop_hook("echo model", tmp_path, model="openai/gpt-5.4")
    manager = _make_manager(tmp_path / "session-config-model")
    completed = threading.Event()

    def fake_run(*args, **kwargs):
        completed.set()
        return MagicMock(returncode=0, stderr="")

    with patch("subprocess.run", side_effect=fake_run) as mock_run:
        list(trigger_hook(HookType.SESSION_END, manager=manager))
        assert completed.wait(timeout=1)

    assert mock_run.call_args.kwargs["env"]["GPTME_MODEL"] == "openai/gpt-5.4"
