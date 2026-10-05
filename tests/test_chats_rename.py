"""Tests for the chats rename command."""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from gptme.cli.cmd_chats import chats_rename
from gptme.logmanager import ConversationMeta, rename_conversation

# --- Helpers ---


def _make_conv_dir(tmp_path: Path, conv_id: str, messages: int = 3) -> Path:
    """Create a minimal conversation directory with a JSONL file."""
    conv_dir = tmp_path / conv_id
    conv_dir.mkdir()
    jsonl = conv_dir / "conversation.jsonl"
    lines = []
    for i in range(messages):
        role = "user" if i % 2 == 0 else "assistant"
        lines.append(
            json.dumps(
                {
                    "role": role,
                    "content": f"msg {i}",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }
            )
        )
    jsonl.write_text("\n".join(lines) + "\n")
    return conv_dir


def _make_conv(
    id: str,
    path: str = "",
    messages: int = 3,
) -> ConversationMeta:
    return ConversationMeta(
        id=id,
        name=id,
        path=path or f"/tmp/fake/{id}/conversation.jsonl",
        created=1000.0,
        modified=2000.0,
        messages=messages,
        branches=1,
        workspace="",
    )


# --- Unit tests for rename_conversation ---


def test_rename_conversation_not_found():
    """rename_conversation returns False when conversation doesn't exist."""
    with patch(
        "gptme.logmanager.conversations.get_conversation_by_id", return_value=None
    ):
        assert rename_conversation("nonexistent", "new name") is False


def test_rename_conversation_updates_config(tmp_path: Path):
    """rename_conversation updates the display name in config.toml."""
    conv_dir = _make_conv_dir(tmp_path, "test-conv")
    conv = _make_conv("test-conv", path=str(conv_dir / "conversation.jsonl"))

    with patch(
        "gptme.logmanager.conversations.get_conversation_by_id", return_value=conv
    ):
        result = rename_conversation("test-conv", "My Renamed Chat")

    assert result is True

    # Verify config.toml was created with the new name
    config_path = conv_dir / "config.toml"
    assert config_path.exists()
    config_text = config_path.read_text()
    assert "My Renamed Chat" in config_text


def test_rename_conversation_preserves_existing_config(tmp_path: Path):
    """rename_conversation preserves other config fields when renaming."""
    conv_dir = _make_conv_dir(tmp_path, "test-conv")

    # Write an existing config with workspace
    import tomlkit

    config = {"chat": {"name": "Old Name", "stream": True}}
    (conv_dir / "config.toml").write_text(tomlkit.dumps(config))

    conv = _make_conv("test-conv", path=str(conv_dir / "conversation.jsonl"))

    with patch(
        "gptme.logmanager.conversations.get_conversation_by_id", return_value=conv
    ):
        result = rename_conversation("test-conv", "New Name")

    assert result is True

    # Re-read and verify name changed, other fields preserved
    with open(conv_dir / "config.toml") as f:
        saved = tomlkit.load(f).unwrap()
    assert saved["chat"]["name"] == "New Name"
    assert saved["chat"]["stream"] is True  # original field must survive
    assert not (conv_dir / "workspace").exists()  # must not create spurious symlink


def test_rename_conversation_idempotent(tmp_path: Path):
    """Renaming to the same name twice works without error."""
    conv_dir = _make_conv_dir(tmp_path, "test-conv")
    conv = _make_conv("test-conv", path=str(conv_dir / "conversation.jsonl"))

    with patch(
        "gptme.logmanager.conversations.get_conversation_by_id", return_value=conv
    ):
        assert rename_conversation("test-conv", "Name A") is True

    with patch(
        "gptme.logmanager.conversations.get_conversation_by_id", return_value=conv
    ):
        assert rename_conversation("test-conv", "Name A") is True


def test_rename_conversation_no_workspace_symlink(tmp_path: Path):
    """rename_conversation must not create a spurious workspace symlink."""
    conv_dir = _make_conv_dir(tmp_path, "test-conv")
    # No pre-existing config.toml, no workspace/ dir
    conv = _make_conv("test-conv", path=str(conv_dir / "conversation.jsonl"))

    with patch(
        "gptme.logmanager.conversations.get_conversation_by_id", return_value=conv
    ):
        rename_conversation("test-conv", "NicerName")

    assert not (conv_dir / "workspace").exists(), (
        "rename must not create a workspace symlink as a side effect"
    )


# --- CLI tests ---


def test_cli_rename_success(tmp_path: Path):
    """CLI rename command prints success message."""
    conv_dir = _make_conv_dir(tmp_path, "my-chat")
    conv = _make_conv("my-chat", path=str(conv_dir / "conversation.jsonl"))

    runner = CliRunner()
    with patch(
        "gptme.logmanager.conversations.get_conversation_by_id", return_value=conv
    ):
        result = runner.invoke(chats_rename, ["my-chat", "Better Name"])

    assert result.exit_code == 0
    assert "Renamed" in result.output
    assert "Better Name" in result.output


def test_cli_rename_not_found():
    """CLI rename command exits with error when chat not found."""
    runner = CliRunner()
    with patch(
        "gptme.logmanager.conversations.get_conversation_by_id", return_value=None
    ):
        result = runner.invoke(chats_rename, ["nonexistent", "New Name"])

    assert result.exit_code == 1
    assert "not found" in result.output


# --- CLI input normalization ---


def test_chats_rename_cli_rejects_blank_name():
    """A blank or whitespace-only name would make the chat unidentifiable."""
    with patch("gptme.logmanager.rename_conversation") as rename:
        for blank in ("", "   ", "\n\t"):
            result = CliRunner().invoke(chats_rename, ["some-conv", blank])
            assert result.exit_code == 2
            assert "must not be blank" in result.output
        rename.assert_not_called()


def test_chats_rename_cli_collapses_whitespace_to_single_line():
    """Newlines/runs of whitespace are collapsed so list output stays one line."""
    with patch("gptme.logmanager.rename_conversation", return_value=True) as rename:
        result = CliRunner().invoke(chats_rename, ["some-conv", "  line1\nline2   x "])
    assert result.exit_code == 0
    rename.assert_called_once_with("some-conv", "line1 line2 x")
    assert "Renamed 'some-conv' to 'line1 line2 x'" in result.output
