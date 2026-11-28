"""Regressions for the third tool follow-through review of gptme/gptme#5249."""

import json
from unittest.mock import patch

import click
import pytest

from gptme.message import Message


def test_session_allowlist_keeps_slash_qualified_mcp_names():
    from gptme.tools import get_session_allowlist, set_session_allowlist

    before = get_session_allowlist()
    try:
        set_session_allowlist(["docs/team.echo_text", "shell"])
        assert get_session_allowlist() == ["docs/team.echo_text", "shell"]
    finally:
        set_session_allowlist(before)


def test_subprocess_arg_keeps_slash_qualified_mcp_name():
    from gptme.tools.base import ToolSpec
    from gptme.tools.subagent.execution import _subprocess_tools_arg

    tools = [ToolSpec(name="docs/team.echo_text", desc="echo")]
    with patch(
        "gptme.tools.subagent.execution.get_available_tools", return_value=tools
    ):
        names = _subprocess_tools_arg(None, ["docs/team.echo_text", "*"]).split(",")
    assert "docs/team.echo_text" in names
    assert not any(
        name.endswith("docs/team.echo_text") and name.startswith("/") for name in names
    )


def test_subprocess_glob_leaves_optional_tool_availability_to_the_child(
    monkeypatch,
):
    from gptme.config.cli_setup import _normalize_tool_allowlist
    from gptme.tools.base import ToolSpec
    from gptme.tools.subagent.execution import _subprocess_tools_arg

    tools = [
        ToolSpec(name="shell", desc="shell"),
        ToolSpec(name="tts", desc="tts", available=False),
    ]
    with patch(
        "gptme.tools.subagent.execution.get_available_tools", return_value=tools
    ):
        names = _subprocess_tools_arg(None, ["*"]).split(",")
    # The parent sends it (#5379); a child that also lacks it skips it at
    # startup instead of failing.
    assert "shell" in names
    assert "tts" in names
    monkeypatch.setenv("GPTME_SUBAGENT_AGENT_ID", "abc")
    with patch("gptme.config.cli_setup.get_available_tools", return_value=tools):
        assert "tts" not in (_normalize_tool_allowlist(["shell", "tts"]) or [])


def test_thread_child_counts_xml_from_message_metadata(tmp_path):
    from gptme.tools.subagent.types import Subagent

    messages = [
        Message("user", "task"),
        Message(
            "assistant",
            "<tool-use><shell>echo ok</shell></tool-use>",
            metadata={"tool_format": "xml"},
        ),
        Message(
            "assistant",
            "<tool-use><complete>Done</complete></tool-use>",
            metadata={"tool_format": "xml"},
        ),
    ]
    (tmp_path / "conversation.jsonl").write_text(
        "".join(json.dumps(m.to_dict()) + "\n" for m in messages)
    )
    child = Subagent(
        agent_id="xml-meta", prompt="task", thread=None, logdir=tmp_path, model=None
    )
    assert child._read_log().tool_uses == 2


def test_resumed_child_counts_each_reply_recorded_format(tmp_path):
    from gptme.tools.subagent.types import Subagent

    messages = [
        Message("user", "task"),
        Message(
            "assistant",
            "```shell\necho ok\n```",
            metadata={"tool_format": "markdown"},
        ),
        Message(
            "assistant",
            "<tool-use><complete>Done</complete></tool-use>",
            metadata={"tool_format": "xml"},
        ),
    ]
    (tmp_path / "conversation.jsonl").write_text(
        "".join(json.dumps(m.to_dict()) + "\n" for m in messages)
    )
    (tmp_path / "config.toml").write_text('[chat]\ntool_format = "tool"\n')
    child = Subagent(
        agent_id="mixed", prompt="task", thread=None, logdir=tmp_path, model=None
    )
    assert child._read_log().tool_uses == 2


def test_context_option_rejects_mcp_looking_typos():
    from gptme.cli.main import main

    option = next(param for param in main.params if param.name == "context_include")
    with pytest.raises(click.exceptions.BadParameter):
        option.type.convert("files.txt", option, None)


def test_tools_option_still_accepts_slash_qualified_mcp_name():
    from gptme.cli.main import main

    option = next(param for param in main.params if param.name == "tool_allowlist")
    assert (
        option.type.convert("shell,docs/team.echo_text", option, None)
        == "shell,docs/team.echo_text"
    )
