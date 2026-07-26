"""Tests for project-configured lifecycle script hooks."""

import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.config import (
    ChatConfig,
    Config,
    HooksConfig,
    ProjectConfig,
    ScriptHookConfig,
    load_user_config,
)
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


def test_user_and_project_script_hooks_layer_by_priority(tmp_path):
    user_config_path = tmp_path / "config.toml"
    user_config_path.write_text(
        """
[[hooks.scripts]]
event = "session.end"
command = "echo user"
priority = 10
"""
    )
    project = ProjectConfig.from_dict(
        {
            "hooks": {
                "scripts": [
                    {
                        "event": "session.end",
                        "command": "echo project",
                        "priority": 20,
                    }
                ]
            }
        }
    )
    config = Config(user=load_user_config(str(user_config_path)), project=project)

    hooks = config.get_script_hooks()

    assert [hook.command for hook in hooks] == ["echo project", "echo user"]
    assert [hook.priority for hook in hooks] == [20, 10]


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
    with patch("gptme.hooks.script.subprocess.Popen") as mock_popen:
        process = mock_popen.return_value
        process.wait.return_value = 0
        process.returncode = 0
        list(trigger_hook(HookType.SESSION_END, manager=manager))

    registered = get_hooks(HookType.SESSION_END)
    assert len(registered) == 1
    assert registered[0].async_mode is False
    mock_popen.assert_called_once()
    kwargs = mock_popen.call_args.kwargs
    assert kwargs["cwd"] == tmp_path
    assert kwargs["env"]["GPTME_HOOK_EVENT"] == "session.end"
    assert kwargs["env"]["GPTME_LOGDIR"] == str(logdir)
    assert kwargs["env"]["GPTME_WORKSPACE"] == str(tmp_path)
    assert kwargs["env"]["GPTME_MODEL"] == "openai/gpt-5.4"
    assert kwargs["start_new_session"] is True
    assert kwargs["creationflags"] == 0
    process.wait.assert_called_once_with(timeout=17)


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

    with patch("gptme.hooks.script.subprocess.Popen") as mock_popen:
        mock_popen.return_value.wait.return_value = 0
        mock_popen.return_value.returncode = 0
        list(trigger_hook(HookType.SESSION_END, manager=_make_manager(logdir)))

    assert (
        mock_popen.call_args.kwargs["env"]["GPTME_MODEL"]
        == "anthropic/claude-sonnet-4-6"
    )


def test_session_start_script_hook_uses_current_model_and_trigger_workspace(tmp_path):
    logdir = tmp_path / "session-start"
    set_default_model("openai/gpt-5.4")
    register_script_hooks(
        [ScriptHookConfig(event="session.start", command="echo start")], tmp_path
    )

    with patch("gptme.hooks.script.subprocess.Popen") as mock_popen:
        mock_popen.return_value.wait.return_value = 0
        mock_popen.return_value.returncode = 0
        list(
            trigger_hook(
                HookType.SESSION_START,
                logdir=logdir,
                workspace=tmp_path,
                initial_msgs=[],
            )
        )

    kwargs = mock_popen.call_args.kwargs
    assert kwargs["env"]["GPTME_HOOK_EVENT"] == "session.start"
    assert kwargs["env"]["GPTME_MODEL"] == "openai/gpt-5.4"
    assert kwargs["cwd"] == tmp_path


def test_script_hooks_register_priority_and_preserve_tie_order(tmp_path):
    register_script_hooks(
        [
            ScriptHookConfig(event="session.end", command="echo first", priority=5),
            ScriptHookConfig(event="session.end", command="echo second", priority=5),
            ScriptHookConfig(event="session.end", command="echo low", priority=-1),
        ],
        tmp_path,
    )

    hooks = get_hooks(HookType.SESSION_END)
    assert [hook.priority for hook in hooks] == [5, 5, -1]
    assert [hook.name for hook in hooks] == [
        "script.000003.session.end",
        "script.000002.session.end",
        "script.000001.session.end",
    ]


def test_script_hook_output_is_spooled_to_disk(tmp_path, caplog, monkeypatch):
    logdir = tmp_path / "session-output"
    ChatConfig(_logdir=logdir, workspace=tmp_path).save()
    hook = ScriptHookConfig(
        event="session.end",
        command=f"{sys.executable} -c \"import sys; sys.stderr.write('x' * 1000000)\"",
    )
    register_script_hooks([hook], tmp_path)
    temporary_file = MagicMock(wraps=tempfile.TemporaryFile)
    monkeypatch.setattr("gptme.hooks.script.tempfile.TemporaryFile", temporary_file)

    with caplog.at_level(logging.WARNING, logger="gptme.hooks.script"):
        list(trigger_hook(HookType.SESSION_END, manager=_make_manager(logdir)))

    assert caplog.text == ""
    temporary_file.assert_called_once_with(mode="w+t")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group regression")
def test_timeout_is_bounded_when_detached_descendant_inherits_output(tmp_path, caplog):
    logdir = tmp_path / "session-detached-child"
    ChatConfig(_logdir=logdir, workspace=tmp_path).save()
    hook = ScriptHookConfig(
        event="session.end",
        command=(
            f'{sys.executable} -c "import subprocess, sys, time; '
            "subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(10)'], start_new_session=True); "
            'time.sleep(10)"'
        ),
        timeout=1,
    )
    register_script_hooks([hook], tmp_path)

    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="gptme.hooks.script"):
        list(trigger_hook(HookType.SESSION_END, manager=_make_manager(logdir)))

    assert time.monotonic() - started < 3
    assert "timed out after 1s" in caplog.text


def test_script_hook_failure_and_timeout_are_logged(tmp_path, caplog):
    hooks = [
        ScriptHookConfig(event="session.end", command="exit 1", timeout=3),
        ScriptHookConfig(event="session.end", command="sleep forever", timeout=4),
    ]
    register_script_hooks(hooks, tmp_path)
    manager = _make_manager(tmp_path / "session-errors")

    failed = MagicMock(pid=101, returncode=1)
    failed.wait.return_value = 1
    timed_out = MagicMock(pid=202)
    timed_out.wait.side_effect = [subprocess.TimeoutExpired("sleep", 4), None]

    with (
        caplog.at_level(logging.WARNING, logger="gptme.hooks.script"),
        patch(
            "gptme.hooks.script.subprocess.Popen",
            side_effect=lambda command, **_kwargs: (
                failed if command == "exit 1" else timed_out
            ),
        ),
        patch("gptme.hooks.script.os.killpg") as killpg,
    ):
        list(trigger_hook(HookType.SESSION_END, manager=manager))

    assert "failed (exit 1)" in caplog.text
    assert "timed out after 4s" in caplog.text
    killpg.assert_called_once_with(202, 9)
    assert timed_out.wait.call_count == 2
    timed_out.wait.assert_any_call(timeout=5)


@pytest.mark.parametrize("taskkill_error", [OSError("missing"), None])
def test_windows_timeout_cleanup_failure_is_not_silenced(
    tmp_path, caplog, taskkill_error
):
    register_script_hooks(
        [ScriptHookConfig(event="session.end", command="sleep forever")],
        tmp_path,
    )
    process = MagicMock(pid=202)
    process.wait.side_effect = [subprocess.TimeoutExpired("sleep", 30), None]
    taskkill = MagicMock(returncode=1)
    run_result = taskkill_error if taskkill_error is not None else taskkill

    with (
        caplog.at_level(logging.ERROR, logger="gptme.hooks.script"),
        patch("gptme.hooks.script.sys.platform", "win32"),
        patch("gptme.hooks.script.subprocess.Popen", return_value=process),
        patch("gptme.hooks.script.subprocess.run", side_effect=run_result),
    ):
        list(
            trigger_hook(
                HookType.SESSION_END,
                manager=_make_manager(tmp_path / "session-windows-failure"),
            )
        )

    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2
    process.wait.assert_any_call(timeout=30)
    process.wait.assert_any_call(timeout=1)
    assert "process tree could not be terminated" in caplog.text
    assert "Error executing hook" in caplog.text


def test_windows_timeout_kills_and_drains_process(tmp_path, caplog):
    register_script_hooks(
        [ScriptHookConfig(event="session.end", command="sleep forever")],
        tmp_path,
    )
    process = MagicMock(pid=202)
    process.wait.side_effect = [subprocess.TimeoutExpired("sleep", 30), None]

    taskkill = MagicMock(returncode=0)
    with (
        caplog.at_level(logging.WARNING, logger="gptme.hooks.script"),
        patch("gptme.hooks.script.sys.platform", "win32"),
        patch("gptme.hooks.script.subprocess.Popen", return_value=process) as popen,
        patch("gptme.hooks.script.subprocess.run", return_value=taskkill) as run,
        patch("gptme.hooks.script.os.killpg") as killpg,
    ):
        list(
            trigger_hook(
                HookType.SESSION_END,
                manager=_make_manager(tmp_path / "session-windows"),
            )
        )

    kwargs = popen.call_args.kwargs
    assert kwargs["start_new_session"] is False
    assert kwargs["creationflags"] == 0x00000200
    run.assert_called_once_with(
        ["taskkill", "/F", "/T", "/PID", "202"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    process.kill.assert_not_called()
    killpg.assert_not_called()
    assert process.wait.call_count == 2
    assert "timed out after 30s" in caplog.text


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
            "timeout must be between 1 and 300 seconds",
        ),
        (
            {"event": "session.end", "command": "echo", "timeout": 301},
            "timeout must be between 1 and 300 seconds",
        ),
        (
            {"event": "session.end", "command": "echo", "timeout": "slow"},
            "timeout must be an integer",
        ),
        (
            {"event": "session.end", "command": "echo", "priority": True},
            "priority must be an integer",
        ),
    ],
)
def test_script_hook_config_rejects_unsafe_values(data, message):
    with pytest.raises(ValueError, match=message):
        ScriptHookConfig(**data)
