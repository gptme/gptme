"""Tests for the gptme-util CLI."""

import time
from pathlib import Path

from click.testing import CliRunner
from gptme.logmanager import ConversationMeta
from gptme.util.cli import main


def test_tokens_count():
    """Test the tokens count command."""
    runner = CliRunner()

    # Test basic token counting
    result = runner.invoke(main, ["tokens", "count", "Hello, world!"])
    assert result.exit_code == 0
    assert "Token count" in result.output
    assert "gpt-4" in result.output  # default model

    # Test invalid model
    result = runner.invoke(
        main, ["tokens", "count", "--model", "invalid-model", "test"]
    )
    assert result.exit_code == 1
    assert "not supported" in result.output

    # Test file input
    with runner.isolated_filesystem():
        Path("test.txt").write_text("Hello from file!")
        result = runner.invoke(main, ["tokens", "count", "-f", "test.txt"])
        assert result.exit_code == 0
        assert "Token count" in result.output


def test_chats_list(tmp_path, mocker):
    """Test the chats list command."""
    runner = CliRunner()

    # Create test conversations
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    # Mock both the logs directory and the conversation listing
    mocker.patch("gptme.dirs.get_logs_dir", return_value=str(logs_dir))
    mocker.patch("gptme.logmanager.get_user_conversations", return_value=[])

    # Test empty list (should work now since we're using our empty logs_dir)
    result = runner.invoke(main, ["chats", "ls"])
    assert result.exit_code == 0
    assert "No conversations found" in result.output

    # Create test conversation files with names that won't be filtered
    conv1_dir = logs_dir / "2024-01-01-chat-one"
    conv1_dir.mkdir()
    (conv1_dir / "conversation.jsonl").write_text(
        '{"role": "user", "content": "hello", "timestamp": "2024-01-01T00:00:00"}\n'
    )

    conv2_dir = logs_dir / "2024-01-01-chat-two"
    conv2_dir.mkdir()
    (conv2_dir / "conversation.jsonl").write_text(
        '{"role": "user", "content": "hello", "timestamp": "2024-01-01T00:00:00"}\n'
        '{"role": "assistant", "content": "hi", "timestamp": "2024-01-01T00:00:01"}\n'
    )

    # Create ConversationMeta objects for our test conversations

    conv1 = ConversationMeta(
        name="2024-01-01-chat-one",
        path=str(conv1_dir / "conversation.jsonl"),
        created=time.time(),
        modified=time.time(),
        messages=1,
        branches=1,
    )
    conv2 = ConversationMeta(
        name="2024-01-01-chat-two",
        path=str(conv2_dir / "conversation.jsonl"),
        created=time.time(),
        modified=time.time(),
        messages=2,
        branches=1,
    )

    # Update the mock to return our test conversations
    mocker.patch("gptme.logmanager.get_user_conversations", return_value=[conv1, conv2])

    # Test with conversations
    result = runner.invoke(main, ["chats", "ls"])
    assert result.exit_code == 0
    assert "chat-one" in result.output
    assert "chat-two" in result.output
    assert "(1 msgs)" in result.output  # First chat has 1 message
    assert "(2 msgs)" in result.output  # Second chat has 2 messages


def test_context_generate(tmp_path, mocker):
    """Test the context generate command."""
    # Create a test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("Hello, world!")

    # Mock RAG dependencies
    mocker.patch("gptme.tools.rag._has_gptme_rag", return_value=True)
    mocker.patch("gptme.tools.rag.init")  # Mock the init function

    # Mock the rag_index function
    mock_index = mocker.patch("gptme.tools.rag.rag_index")
    mock_index.return_value = 1

    runner = CliRunner()
    result = runner.invoke(main, ["context", "generate", str(test_file)])

    assert result.exit_code == 0
    mock_index.assert_called_once_with(str(test_file))
    assert "Indexed 1" in result.output


def test_tools_list():
    """Test the tools list command."""
    runner = CliRunner()

    # Test basic list
    result = runner.invoke(main, ["tools", "list"])
    assert result.exit_code == 0
    assert "Available tools:" in result.output

    # Test langtags
    result = runner.invoke(main, ["tools", "list", "--langtags"])
    assert result.exit_code == 0
    assert "language tags" in result.output.lower()


def test_tools_info():
    """Test the tools info command."""
    runner = CliRunner()

    # Test valid tool
    result = runner.invoke(main, ["tools", "info", "ipython"])
    assert result.exit_code == 0
    assert "Tool: ipython" in result.output
    assert "Description:" in result.output
    assert "Instructions:" in result.output

    # Test invalid tool
    result = runner.invoke(main, ["tools", "info", "nonexistent-tool"])
    assert result.exit_code != 0  # returns non-zero for not found tool
    assert "not found" in result.output
