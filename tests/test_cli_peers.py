"""Peer entry points use the durable queue, not another conversation's writer."""

import json
from pathlib import Path

from click.testing import CliRunner

from gptme.cli.cmd_chats import chats
from gptme.prompt_queue import drain_peer_prompts


def test_peer_send_cli(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "sender").mkdir()
    monkeypatch.setattr("gptme.peers.get_logs_dir", lambda: tmp_path)
    monkeypatch.setattr("gptme.cli.cmd_chats.get_logs_dir", lambda: tmp_path)
    result = CliRunner().invoke(
        chats, ["send", "target", "--peer", "--sender", "sender", "hello peer"]
    )
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt
    messages = drain_peer_prompts(target)
    assert len(messages) == 1
    assert "hello peer" in messages[0].content
    assert messages[0].metadata
    assert messages[0].metadata.get("peer_message_id")


def test_peer_send_cli_rejects_missing_target(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "sender").mkdir()
    monkeypatch.setattr("gptme.peers.get_logs_dir", lambda: tmp_path)
    monkeypatch.setattr("gptme.cli.cmd_chats.get_logs_dir", lambda: tmp_path)
    result = CliRunner().invoke(
        chats, ["send", "missing", "--peer", "--sender", "sender", "hello"]
    )
    assert result.exit_code != 0
    assert "Peer conversation does not exist: missing" in result.output
    assert not (tmp_path / "missing").exists()


def test_chats_tool_exposes_peer_functions() -> None:
    from gptme.tools.chats import tool

    assert tool.functions is not None
    names = {function.name for function in tool.functions}
    assert {"list_peers", "send_peer_message"} <= names
