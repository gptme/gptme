"""Tests for project-configured lifecycle script hooks."""

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.config import ChatConfig, HooksConfig, ProjectConfig, ScriptHookConfig
from gptme.hooks import HookType, clear_hooks, get_hooks, trigger_hook
from gptme.hooks.script import register_script_hooks
from gptme.llm.models import set_default_model


@pytest.fixture(autouse=True)
def clear_all_hooks():
    clear_hooks()
    yield
    clear_hooks()


def _make_manager(logdir: Path) -> MagicMock:
    manager = MagicMock()
    manager.logdir = logdir
    return manager


def test_project_config_parses_script_hooks():
    config = ProjectConfig.from_dict(
        {
            "hooks": {
                "scripts": [
                    {
                        "event": "session.end",
                        "command": "scripts/save-context.sh",
                        "timeout": 12,
                    }
                ]
            }
        }
    )
    assert config.hooks == HooksConfig(
        scripts=[
            ScriptHookConfig(
                event="session.end",
                command="scripts/save-context.sh",
                timeout=12,
            )
        ]
    )


def test_project_config_script_hooks_round_trip():
    config = ProjectConfig.from_dict(
        {
            "hooks": {
                "scripts": [
                    {"event": "session.start", "command": "echo start"},
                    {"event": "session.end", "command": "echo end"},
                ]
            }
        }
    )
    assert ProjectConfig.from_dict(config.to_dict()) == config


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"hooks": {"scripts": {}}}, "hooks.scripts must be a list"),
        ({"hooks": {"scripts": ["echo nope"]}}, "must be an object"),
        (
            {"hooks": {"scripts": [{"event": "session.end"}]}},
            "invalid hooks.scripts config",
        ),
    ],
)
def test_project_config_rejects_invalid_script_hooks(data, message):
    with pytest.raises(ValueError, match=message):
        ProjectConfig.from_dict(data)


def test_session_end_script_hook_runs_synchronously_with_metadata(tmp_path):
    logdir = tmp_path / "session-end"
    ChatConfig(_logdir=logdir, model="openai/gpt-5.4", workspace=tmp_path).save()
    hook = ScriptHookConfig(
        event="session.end", command="scripts/save-context.sh", timeout=17
    )
    register_script_hooks([hook], tmp_path)

    manager = _make_manager(logdir)
    with patch("gptme.hooks.script.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        list(trigger_hook(HookType.SESSION_END, manager=manager))

    registered = get_hooks(HookType.SESSION_END)
    assert len(registered) == 1
    assert registered[0].async_mode is False
    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    assert kwargs["cwd"] == tmp_path
    assert kwargs["timeout"] == 17
    assert kwargs["env"]["GPTME_HOOK_EVENT"] == "session.end"
    assert kwargs["env"]["GPTME_LOGDIR"] == str(logdir)
    assert kwargs["env"]["GPTME_WORKSPACE"] == str(tmp_path)
    assert kwargs["env"]["GPTME_MODEL"] == "openai/gpt-5.4"


def test_session_end_script_hook_reads_model_switch_at_trigger_time(tmp_path):
    logdir = tmp_path / "session-model-switch"
    chat_config = ChatConfig(
        _logdir=logdir, model="openai/gpt-5.4", workspace=tmp_path
    ).save()
    register_script_hooks(
        [ScriptHookConfig(event="session.end", command="echo model")], tmp_path
    )
    chat_config.model = "anthropic/claude-sonnet-4-6"
    chat_config.save()

    with patch("gptme.hooks.script.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        list(trigger_hook(HookType.SESSION_END, manager=_make_manager(logdir)))

    assert (
        mock_run.call_args.kwargs["env"]["GPTME_MODEL"] == "anthropic/claude-sonnet-4-6"
    )


def test_session_start_script_hook_uses_current_model_and_trigger_workspace(tmp_path):
    logdir = tmp_path / "session-start"
    set_default_model("openai/gpt-5.4")
    register_script_hooks(
        [ScriptHookConfig(event="session.start", command="echo start")], tmp_path
    )

    with patch("gptme.hooks.script.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        list(
            trigger_hook(
                HookType.SESSION_START,
                logdir=logdir,
                workspace=tmp_path,
                initial_msgs=[],
            )
        )

    kwargs = mock_run.call_args.kwargs
    assert kwargs["env"]["GPTME_HOOK_EVENT"] == "session.start"
    assert kwargs["env"]["GPTME_MODEL"] == "openai/gpt-5.4"
    assert kwargs["cwd"] == tmp_path


def test_script_hook_failure_and_timeout_are_logged(tmp_path, caplog):
    hooks = [
        ScriptHookConfig(event="session.end", command="exit 1", timeout=3),
        ScriptHookConfig(event="session.end", command="sleep forever", timeout=4),
    ]
    register_script_hooks(hooks, tmp_path)
    manager = _make_manager(tmp_path / "session-errors")

    def fake_run(command, **_kwargs):
        if command == "exit 1":
            return MagicMock(returncode=1, stderr="bad command")
        raise subprocess.TimeoutExpired(command, 4)

    with (
        caplog.at_level(logging.WARNING, logger="gptme.hooks.script"),
        patch("gptme.hooks.script.subprocess.run", side_effect=fake_run),
    ):
        list(trigger_hook(HookType.SESSION_END, manager=manager))

    assert "failed (exit 1): bad command" in caplog.text
    assert "timed out after 4s" in caplog.text


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {"event": "turn.pre", "command": "echo no"},
            "unsupported hooks.scripts event",
        ),
        (
            {"event": "session.end", "command": "  "},
            "command must not be empty",
        ),
        (
            {"event": "session.end", "command": "echo", "timeout": 0},
            "timeout must be greater than zero",
        ),
        (
            {"event": "session.end", "command": "echo", "timeout": "slow"},
            "timeout must be an integer",
        ),
    ],
)
def test_script_hook_config_rejects_unsafe_values(data, message):
    with pytest.raises(ValueError, match=message):
        ScriptHookConfig(**data)
