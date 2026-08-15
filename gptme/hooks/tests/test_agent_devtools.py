"""Tests for the Agent-Devtools hook plugin."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import requests

from gptme.hooks import HookRegistry, HookType, get_registry, set_registry
from gptme.hooks.agent_devtools import (
    _init_from_config,
    _remember_tool_start,
    _send_event,
    emit_generation_post,
    emit_tool_post,
    emit_tool_pre,
    register,
)
from gptme.hooks.types import ToolExecutePostData, ToolExecutePreData
from gptme.logmanager import Log
from gptme.message import Message
from gptme.tools.base import ToolUse


def test_tool_pre_omits_sensitive_payload_by_default(monkeypatch):
    monkeypatch.setenv("GPTME_AGENT_DEVTOOLS_ENDPOINT", "http://trace.local/events")
    monkeypatch.delenv("GPTME_AGENT_DEVTOOLS_INCLUDE_SENSITIVE", raising=False)
    tool_use = ToolUse(
        tool="shell",
        args=["bash"],
        content="echo secret",
        kwargs={"command": "echo secret", "mode": "fast"},
        call_id="call-123",
    )

    with patch("gptme.hooks.agent_devtools.requests.post") as post:
        post.return_value.status_code = 200
        assert (
            list(
                emit_tool_pre(
                    ToolExecutePreData(
                        log=cast(
                            Log, SimpleNamespace(messages=[Message("user", "hi")])
                        ),
                        workspace=Path("/tmp/workspace"),
                        tool_use=tool_use,
                    )
                )
            )
            == []
        )

    payload = post.call_args.kwargs["json"]
    assert payload["event"] == HookType.TOOL_EXECUTE_PRE.value
    assert payload["tool"]["name"] == "shell"
    assert payload["tool"]["call_id"] == "call-123"
    assert payload["tool"]["args_preview"]["arg_count"] == 1
    assert payload["tool"]["args_preview"]["kwarg_keys"] == ["command", "mode"]
    assert "args" not in payload["tool"]
    assert "kwargs" not in payload["tool"]
    assert "content" not in payload["tool"]


def test_tool_post_includes_sensitive_payload_when_enabled(monkeypatch):
    monkeypatch.setenv("GPTME_AGENT_DEVTOOLS_ENDPOINT", "http://trace.local/events")
    monkeypatch.setenv("GPTME_AGENT_DEVTOOLS_INCLUDE_SENSITIVE", "1")
    tool_use = ToolUse(
        tool="shell",
        args=["bash"],
        content="echo hello",
        kwargs={"command": "echo hello"},
        call_id="call-456",
    )
    result_msg = Message("system", "hello\n")

    with (
        patch(
            "gptme.hooks.agent_devtools.time.monotonic",
            side_effect=[10.0, 10.25],
        ),
        patch("gptme.hooks.agent_devtools.requests.post") as post,
    ):
        post.return_value.status_code = 200
        list(
            _remember_tool_start(
                ToolExecutePreData(
                    log=cast(Log, SimpleNamespace(messages=[Message("user", "hi")])),
                    workspace=Path("/tmp/workspace"),
                    tool_use=tool_use,
                )
            )
        )
        list(
            emit_tool_pre(
                ToolExecutePreData(
                    log=cast(Log, SimpleNamespace(messages=[Message("user", "hi")])),
                    workspace=Path("/tmp/workspace"),
                    tool_use=tool_use,
                )
            )
        )
        assert (
            list(
                emit_tool_post(
                    ToolExecutePostData(
                        log=cast(
                            Log, SimpleNamespace(messages=[Message("user", "hi")])
                        ),
                        workspace=Path("/tmp/workspace"),
                        tool_use=tool_use,
                        result_msgs=(result_msg,),
                    )
                )
            )
            == []
        )

    payload = post.call_args.kwargs["json"]
    assert payload["event"] == HookType.TOOL_EXECUTE_POST.value
    assert payload["tool"]["args"] == ["bash"]
    assert payload["tool"]["kwargs"] == {"command": "echo hello"}
    assert payload["tool"]["content"] == "echo hello"
    assert payload["tool"]["result"] == "hello\n"
    assert payload["usage"]["duration_ms"] == 250
    assert payload["usage"]["result_message_count"] == 1


def test_generation_post_uses_message_metadata(monkeypatch):
    monkeypatch.setenv("GPTME_AGENT_DEVTOOLS_ENDPOINT", "http://trace.local/events")
    msg = Message(
        "assistant",
        "done",
        metadata={
            "model": "openai/test",
            "resolved_model": "openai/test@provider",
            "usage": {"input_tokens": 12, "output_tokens": 5},
            "timings": {"ttft_ms": 40.0, "gen_ms": 120.0},
        },
    )

    with patch("gptme.hooks.agent_devtools.requests.post") as post:
        post.return_value.status_code = 200
        assert list(emit_generation_post(msg, workspace=Path("/tmp/workspace"))) == []

    payload = post.call_args.kwargs["json"]
    assert payload["event"] == HookType.GENERATION_POST.value
    assert payload["usage"]["model"] == "openai/test"
    assert payload["usage"]["resolved_model"] == "openai/test@provider"
    assert payload["usage"]["input_tokens"] == 12
    assert payload["usage"]["output_tokens"] == 5
    assert payload["usage"]["ttft_ms"] == 40.0
    assert payload["usage"]["gen_ms"] == 120.0
    assert "assistant" not in payload


def test_send_event_fails_open_on_timeout(monkeypatch):
    monkeypatch.setenv("GPTME_AGENT_DEVTOOLS_ENDPOINT", "http://trace.local/events")

    with patch(
        "gptme.hooks.agent_devtools.requests.post",
        side_effect=requests.Timeout("boom"),
    ):
        _send_event({"event": "tool.execute.pre"})


def test_register_adds_async_export_hooks():
    old = get_registry()
    set_registry(HookRegistry())
    try:
        register()
        tool_pre_hooks = get_registry().get_hooks(HookType.TOOL_EXECUTE_PRE)
        hook_map = {hook.name: hook for hook in tool_pre_hooks}
        assert hook_map["agent_devtools.tool_pre_state"].async_mode is False
        assert hook_map["agent_devtools.tool_pre"].async_mode is True

        session_hooks = get_registry().get_hooks(HookType.SESSION_START)
        assert any(
            hook.name == "agent_devtools.session_start" and hook.async_mode
            for hook in session_hooks
        )
    finally:
        set_registry(old)


def test_init_from_config_sets_env(monkeypatch):
    for name in (
        "GPTME_AGENT_DEVTOOLS",
        "GPTME_AGENT_DEVTOOLS_ENDPOINT",
        "GPTME_AGENT_DEVTOOLS_TIMEOUT",
        "GPTME_AGENT_DEVTOOLS_INCLUDE_SENSITIVE",
    ):
        monkeypatch.delenv(name, raising=False)

    config = SimpleNamespace(
        user=SimpleNamespace(
            plugin={
                "agent_devtools": {
                    "endpoint": "http://trace.local/events",
                    "timeout": 2.5,
                    "include_sensitive": True,
                }
            }
        ),
        project=None,
    )

    _init_from_config(config)

    assert os.environ["GPTME_AGENT_DEVTOOLS"] == "1"
    assert os.environ["GPTME_AGENT_DEVTOOLS_ENDPOINT"] == "http://trace.local/events"
    assert os.environ["GPTME_AGENT_DEVTOOLS_TIMEOUT"] == "2.5"
    assert os.environ["GPTME_AGENT_DEVTOOLS_INCLUDE_SENSITIVE"] == "True"
