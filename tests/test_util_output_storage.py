"""Tests for the output_storage utility module."""

from pathlib import Path

from gptme.util.output_storage import create_tool_result_summary, save_large_output


def test_save_large_output_creates_file(tmp_path: Path):
    """save_large_output should create a file with the content."""
    content = "Hello, world!\nLine 2\nLine 3"
    summary, saved_path = save_large_output(
        content=content,
        logdir=tmp_path,
        output_type="shell",
    )
    assert saved_path.exists()
    assert saved_path.read_text() == content
    assert "shell" in str(saved_path)
    assert "[Large shell output saved]" in summary


def test_save_large_output_with_metadata(tmp_path: Path):
    """save_large_output should include command and token info in summary."""
    summary, _ = save_large_output(
        content="output data",
        logdir=tmp_path,
        output_type="python",
        command_info="print('hello')",
        original_tokens=5000,
    )
    assert "Command: print('hello')" in summary
    assert "Original: 5000 tokens" in summary
    assert "Full output:" in summary


def test_save_large_output_unique_filenames(tmp_path: Path):
    """Different content should produce different filenames."""
    _, path1 = save_large_output(content="content A", logdir=tmp_path)
    _, path2 = save_large_output(content="content B", logdir=tmp_path)
    assert path1 != path2


def test_save_large_output_directory_structure(tmp_path: Path):
    """Output should be saved under tool-outputs/<type>/."""
    _, saved_path = save_large_output(
        content="test",
        logdir=tmp_path,
        output_type="shell",
    )
    assert "tool-outputs" in str(saved_path)
    assert "shell" in str(saved_path)


def test_create_tool_result_summary_no_logdir():
    """Without logdir, should return a simple message."""
    result = create_tool_result_summary(
        content="lots of output",
        original_tokens=10000,
        logdir=None,
        tool_name="shell",
    )
    assert "10000 tokens" in result
    assert "automatically removed" in result
    assert "saved to" not in result.lower()


def test_create_tool_result_summary_with_logdir(tmp_path: Path):
    """With logdir, should save output and reference the file."""
    result = create_tool_result_summary(
        content="lots of output",
        original_tokens=10000,
        logdir=tmp_path,
        tool_name="shell",
    )
    assert "10000 tokens" in result
    assert "saved to" in result.lower()
    assert "read or grep" in result


def test_create_tool_result_summary_extracts_command(tmp_path: Path):
    """Should extract command info from content."""
    content = "Ran command: ls -la\nfile1.txt\nfile2.txt"
    result = create_tool_result_summary(
        content=content,
        original_tokens=500,
        logdir=tmp_path,
        tool_name="shell",
    )
    assert "Ran command: ls -la" in result


def test_create_tool_result_summary_no_command(tmp_path: Path):
    """Content without command prefix should work fine."""
    content = "just some output\nwithout command info"
    result = create_tool_result_summary(
        content=content,
        original_tokens=500,
        logdir=tmp_path,
        tool_name="python",
    )
    assert "500 tokens" in result
    # Should not have command info
    assert "Command:" not in result or "Ran command:" not in result
