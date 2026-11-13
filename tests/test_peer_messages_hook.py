"""Peer input works at a general session's step boundary without subagents."""

from pathlib import Path

from gptme.hooks.peer_messages import peer_messages
from gptme.logmanager import LogManager
from gptme.prompt_queue import queue_prompt


def test_peer_hook_is_branch_scoped(tmp_path: Path) -> None:
    manager = LogManager([], logdir=tmp_path, lock=False)
    queue_prompt(tmp_path, "ordinary user input")
    queue_prompt(
        tmp_path, "peer main", peer_sender="sibling", peer_message_id="a", steer=True
    )
    queue_prompt(
        tmp_path,
        "peer other",
        peer_sender="sibling",
        peer_message_id="b",
        branch="other",
    )
    messages = list(peer_messages(manager))
    assert [message.content for message in messages] == ["peer main"]
    assert messages[0].metadata is not None
    assert messages[0].metadata["peer_message_id"] == "a"
    assert list(peer_messages(manager)) == []
    remaining = (tmp_path / "prompt-queue.jsonl").read_text()
    assert "ordinary user input" in remaining
    assert "peer other" in remaining
