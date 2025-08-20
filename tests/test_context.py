import os
from datetime import datetime, timedelta
from pathlib import Path

from gptme.message import Message
from gptme.util.context import (
    append_file_content,
    file_to_display_path,
    gather_fresh_context,
    get_mentioned_files,
)


def test_file_to_display_path(tmp_path):
    # Test relative path in workspace
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file = workspace / "test.txt"
    file.touch()

    # Should show relative path when in workspace
    os.chdir(workspace)
    assert file_to_display_path(file, workspace) == Path("test.txt")

    # Should show absolute path when outside workspace
    os.chdir(tmp_path)
    assert file_to_display_path(file, workspace) == file.absolute()


def test_append_file_content(tmp_path):
    # Create test file
    file = tmp_path / "test.txt"
    file.write_text("old content")

    # Create message with file reference and timestamp
    msg = Message(
        "user",
        "check this file",
        files=[file],
        timestamp=datetime.now() - timedelta(minutes=1),
    )

    # Modify file after message timestamp
    file.write_text("new content")

    # Should show file was modified
    result = append_file_content(msg, check_modified=True)
    assert "<file was modified after message>" in result.content


def test_gather_fresh_context(tmp_path):
    # Create test files
    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"
    file1.write_text("content 1")
    file2.write_text("content 2")

    # Create messages referencing files
    msgs = [
        Message("user", "check file1", files=[file1]),
        Message("user", "check file2", files=[file2]),
    ]

    # Should include both files in context
    context = gather_fresh_context(msgs, tmp_path)
    assert "file1.txt" in context.content
    assert "file2.txt" in context.content
    assert "content 1" in context.content
    assert "content 2" in context.content


def test_get_mentioned_files(tmp_path):
    # Create test files with different modification times
    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"
    file1.write_text("content 1")
    file2.write_text("content 2")

    # Create messages with multiple mentions
    msgs = [
        Message("user", "check file1", files=[file1]),
        Message("user", "check file1 again", files=[file1]),
        Message("user", "check file2", files=[file2]),
    ]

    # Should sort by mentions (file1 mentioned twice)
    files = get_mentioned_files(msgs, tmp_path)
    assert files[0] == file1
    assert files[1] == file2


def test_use_fresh_context(monkeypatch):
    """Test use_fresh_context environment variable detection."""
    from gptme.util.context import use_fresh_context

    # Test various true values
    for value in ["1", "true", "True", "TRUE", "yes", "YES"]:
        monkeypatch.setenv("GPTME_FRESH", value)
        assert use_fresh_context()

    # Test false/unset values
    for value in ["0", "false", "no", ""]:
        monkeypatch.setenv("GPTME_FRESH", value)
        assert not use_fresh_context()


def test_use_checks_explicit(monkeypatch, tmp_path):
    """Test use_checks with explicit environment variable settings."""
    from gptme.util.context import use_checks

    # Change to test directory
    os.chdir(tmp_path)

    # Test explicit enabled
    monkeypatch.setenv("GPTME_CHECK", "true")
    monkeypatch.setattr(
        "gptme.util.context.shutil.which", lambda x: "/usr/bin/pre-commit"
    )
    # Should return True even without .pre-commit-config.yaml
    assert use_checks()

    # Test explicit disabled (should override config file presence)
    monkeypatch.setenv("GPTME_CHECK", "false")
    config_file = tmp_path / ".pre-commit-config.yaml"
    config_file.touch()
    assert not use_checks()


def test_use_checks_config_file(monkeypatch, tmp_path):
    """Test use_checks with .pre-commit-config.yaml detection."""
    from gptme.util.context import use_checks

    # Change to test directory
    os.chdir(tmp_path)

    # Mock pre-commit being available
    monkeypatch.setattr(
        "gptme.util.context.shutil.which", lambda x: "/usr/bin/pre-commit"
    )

    # No explicit setting, no config file
    monkeypatch.delenv("GPTME_CHECK", raising=False)
    assert not use_checks()

    # No explicit setting, with config file
    config_file = tmp_path / ".pre-commit-config.yaml"
    config_file.touch()
    assert use_checks()

    # Test config file in parent directory
    config_file.unlink()
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    os.chdir(subdir)
    config_file = tmp_path / ".pre-commit-config.yaml"
    config_file.touch()
    assert use_checks()


def test_use_checks_no_precommit(monkeypatch, tmp_path):
    """Test use_checks when pre-commit is not available."""
    from gptme.util.context import use_checks

    os.chdir(tmp_path)

    # Enable checks but no pre-commit available
    monkeypatch.setenv("GPTME_CHECK", "true")
    monkeypatch.setattr("gptme.util.context.shutil.which", lambda x: None)

    assert not use_checks()


def test_git_branch(monkeypatch):
    """Test git_branch function."""
    from gptme.util.context import git_branch

    # Mock git command available and successful
    monkeypatch.setattr("gptme.util.context.shutil.which", lambda x: "/usr/bin/git")

    import subprocess

    def mock_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], returncode=0, stdout="main\n", stderr=""
        )

    monkeypatch.setattr("gptme.util.context.subprocess.run", mock_run)

    result = git_branch()
    assert result == "main"

    # Test git command failure
    def mock_run_fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr("gptme.util.context.subprocess.run", mock_run_fail)

    result = git_branch()
    assert result is None

    # Test git not available
    monkeypatch.setattr("gptme.util.context.shutil.which", lambda x: None)

    result = git_branch()
    assert result is None


def test_parse_prompt_files(tmp_path):
    """Test _parse_prompt_files function."""
    from gptme.util.context import _parse_prompt_files

    os.chdir(tmp_path)

    # Create test files
    text_file = tmp_path / "test.txt"
    text_file.write_text("content")

    image_file = tmp_path / "image.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG header

    binary_file = tmp_path / "binary.bin"
    binary_file.write_bytes(b"\xff\xfe\x80\x81")  # Invalid UTF-8 bytes

    # Test text file
    result = _parse_prompt_files(str(text_file))
    assert result == text_file

    # Test image file
    result = _parse_prompt_files(str(image_file))
    assert result == image_file

    # Test binary file (unsupported)
    result = _parse_prompt_files(str(binary_file))
    assert result is None

    # Test non-existent file
    result = _parse_prompt_files("nonexistent.txt")
    assert result is None


def test_parse_prompt_files_home_expansion(tmp_path, monkeypatch):
    """Test _parse_prompt_files with home directory expansion."""
    from gptme.util.context import _parse_prompt_files
    import pathlib

    # Test home directory expansion
    home_file = tmp_path / "home_test.txt"
    home_file.write_text("content")

    # Mock expanduser to return our test file
    def mock_expanduser(self):
        if str(self).startswith("~/"):
            return tmp_path / str(self)[2:]
        return self

    monkeypatch.setattr(pathlib.Path, "expanduser", mock_expanduser)

    result = _parse_prompt_files("~/home_test.txt")
    assert result == tmp_path / "home_test.txt"
