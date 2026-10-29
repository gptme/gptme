"""Named rewinds must verify active content, not just a shared numeric index."""

from pathlib import Path

import pytest

import gptme.commands  # noqa: F401
from gptme.commands.base import handle_cmd
from gptme.logmanager import Log, LogManager
from gptme.logmanager.conv_checkpoints import (
    list_conv_checkpoints,
    save_conv_checkpoint,
)
from gptme.message import Message


def command(manager: LogManager, text: str) -> list[Message]:
    manager.append(Message("user", text, quiet=True))
    messages = list(handle_cmd(text, manager))
    for message in messages:
        manager.append(message)
    return messages


def manager_at(path: Path) -> LogManager:
    return LogManager(
        [Message("user", "root"), Message("assistant", "root reply")],
        logdir=path,
        lock=False,
    )


def test_sibling_divergence_preserves_messages(tmp_path, capsys):
    manager = manager_at(tmp_path)
    manager.branch("A")
    manager.append(Message("user", "A-only", quiet=True))
    command(manager, "/backtrack mark only-A")
    manager.branch("main")
    manager.branch("B")
    for text in ["B-only-1", "B-only-2", "B-only-3"]:
        manager.append(Message("user", text, quiet=True))
    before = list(manager.log.messages)
    messages = command(manager, "/backtrack only-A")
    assert manager.log.messages == before
    assert not messages
    assert "does not match" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["content", "role", "metadata", "compacted"])
def test_changed_prefix_is_rejected(tmp_path, capsys, change):
    manager = manager_at(tmp_path)
    command(manager, "/backtrack mark before")
    if change == "compacted":
        manager._views["compact"] = Log([Message("system", "summary")])
        manager.switch_view("compact")
        # Make the active view long enough that the numeric bound alone passes.
        for i in range(4):
            manager.append(Message("user", f"view {i}", quiet=True))
    else:
        replacement = {
            "content": Message("user", "edited root"),
            "role": Message("system", "root"),
            "metadata": Message("user", "root", metadata={"model": "different-model"}),
        }[change]
        manager.edit([replacement, *manager.log.messages[1:]])
    before = list(manager.log.messages)
    assert command(manager, "/backtrack before") == []
    assert manager.log.messages == before
    assert "does not match" in capsys.readouterr().out


def test_same_prefix_survives_reload_and_inherited_branch(tmp_path):
    manager = manager_at(tmp_path)
    command(manager, "/backtrack mark shared")
    record = list_conv_checkpoints(tmp_path)[0]
    assert record.prefix_hash
    manager.write()
    manager = LogManager.load(tmp_path, lock=False)
    assert list_conv_checkpoints(tmp_path)[0] == record
    manager.branch("inherited")
    manager.append(Message("assistant", "later", quiet=True))
    messages = command(manager, "/backtrack shared")
    assert len(messages) == 1
    assert "checkpoint: 'shared'" in messages[0].content
    assert all(m.content != "later" for m in manager.log)


def test_legacy_label_fails_closed_but_numeric_is_explicit(tmp_path, capsys):
    save_conv_checkpoint(tmp_path, 1, "legacy")
    manager = manager_at(tmp_path)
    before = list(manager.log.messages)
    assert command(manager, "/backtrack legacy") == []
    assert manager.log.messages == before
    assert "re-mark" in capsys.readouterr().out
    messages = command(manager, "/backtrack 1")
    assert len(messages) == 1
    assert manager.log.messages[0] == before[0]


def test_latest_duplicate_is_not_replaced_by_older_matching_label(tmp_path, capsys):
    manager = manager_at(tmp_path)
    command(manager, "/backtrack mark dup")
    manager.branch("other")
    manager.edit([Message("user", "different")])
    command(manager, "/backtrack mark dup")
    manager.branch("main")
    before = list(manager.log.messages)
    assert command(manager, "/backtrack dup") == []
    assert manager.log.messages == before
    assert "does not match" in capsys.readouterr().out


def test_label_starting_at_is_still_checked(tmp_path, capsys):
    save_conv_checkpoint(tmp_path, 1, "@label")
    manager = manager_at(tmp_path)
    assert command(manager, "/backtrack @label") == []
    assert "re-mark" in capsys.readouterr().out


@pytest.mark.parametrize("target", ["-1", "999"])
def test_invalid_indices_do_not_edit(tmp_path, target):
    manager = manager_at(tmp_path)
    before = list(manager.log.messages)
    assert command(manager, f"/backtrack {target}") == []
    assert manager.log.messages == before


def test_most_recent_duplicate_with_same_content_rewinds(tmp_path):
    manager = manager_at(tmp_path)
    command(manager, "/backtrack mark dup")
    manager.append(Message("assistant", "keep newer prefix", quiet=True))
    command(manager, "/backtrack mark dup")
    manager.append(Message("user", "remove me", quiet=True))
    messages = command(manager, "/backtrack dup")
    assert len(messages) == 1
    assert any(m.content == "keep newer prefix" for m in manager.log)
    assert not any(m.content == "remove me" for m in manager.log)


def test_named_marker_in_compacted_view_rewinds_same_view(tmp_path):
    manager = manager_at(tmp_path)
    manager._views["compact"] = Log([Message("system", "summary")])
    manager.switch_view("compact")
    command(manager, "/backtrack mark compact")
    manager.append(Message("user", "later", quiet=True))
    messages = command(manager, "/backtrack compact")
    assert len(messages) == 1
    assert manager.log.messages[0].content == "summary"
    assert not any(m.content == "later" for m in manager.log)


def test_attachment_change_rejects_named_rewind(tmp_path, capsys):
    manager = manager_at(tmp_path)
    attachment = tmp_path / "attachment.txt"
    attachment.write_text("first")
    manager.append(Message("user", "attached", files=[attachment], quiet=True))
    command(manager, "/backtrack mark attachment")
    original = manager.log.messages[-1]
    manager.edit(
        [
            *manager.log.messages[:-1],
            Message("user", "attached", files=[tmp_path / "other.txt"]),
        ]
    )
    assert original.files != manager.log.messages[-1].files
    before = list(manager.log.messages)
    assert command(manager, "/backtrack attachment") == []
    assert manager.log.messages == before
    assert "does not match" in capsys.readouterr().out
