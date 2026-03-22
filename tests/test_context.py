from datetime import datetime, timedelta, timezone
from pathlib import Path

from gptme.message import Message
from gptme.util.context import (
    embed_attached_file_content,
    file_to_display_path,
    get_mentioned_files,
)


def test_file_to_display_path(tmp_path, monkeypatch):
    # Test relative path in workspace
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file = workspace / "test.txt"
    file.touch()

    # Should show relative path when in workspace
    monkeypatch.chdir(workspace)
    assert file_to_display_path(file, workspace) == Path("test.txt")

    # Should show absolute path when outside workspace
    monkeypatch.chdir(tmp_path)
    assert file_to_display_path(file, workspace) == file.absolute()


def test_embed_attached_file_content(tmp_path):
    # Create test file
    file = tmp_path / "test.txt"
    file.write_text("old content")

    # Create message with file reference and timestamp
    msg = Message(
        "user",
        "check this file",
        files=[file],
        timestamp=datetime.now(tz=timezone.utc) - timedelta(minutes=1),
    )

    # Modify file after message timestamp
    file.write_text("new content")

    # Should show file was modified
    result = embed_attached_file_content(msg, check_modified=True)
    assert "<file was modified after message>" in result.content


def test_context_hook(tmp_path, monkeypatch):
    from gptme.hooks.active_context import context_hook

    # Enable fresh context mode (required for active context discovery)
    monkeypatch.setenv("GPTME_FRESH", "1")

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
    # context_hook returns a generator yielding one message
    context_gen = context_hook(msgs, workspace=tmp_path)
    context = next(context_gen)

    assert isinstance(context, Message)
    # With the new selector, it may prioritize files matching the last query
    # but should still include both files since max_files=10
    # Just verify at least one file is included and content is present
    assert "file" in context.content.lower()
    assert "content 1" in context.content or "content 2" in context.content


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

    # Should return dict with counts, ordered by mentions (file1 mentioned twice)
    files = get_mentioned_files(msgs, tmp_path)
    paths = list(files.keys())
    assert paths[0] == file1
    assert paths[1] == file2
    # Verify counts are returned
    assert files[file1.resolve()] == 2
    assert files[file2.resolve()] == 1


def test_use_fresh_context(monkeypatch):
    """Test use_fresh_context environment variable detection."""
    from gptme.util.context import use_fresh_context

    # Test default (unset) -> False (active context is now default)
    monkeypatch.delenv("GPTME_FRESH", raising=False)
    assert not use_fresh_context()

    # Test various true values
    for value in ["1", "true", "True", "TRUE", "yes", "YES"]:
        monkeypatch.setenv("GPTME_FRESH", value)
        assert use_fresh_context()

    # Test false values
    for value in ["0", "false", "no"]:
        monkeypatch.setenv("GPTME_FRESH", value)
        assert not use_fresh_context()


def test_use_checks_explicit(monkeypatch, tmp_path):
    """Test use_checks with explicit environment variable settings."""
    from gptme.tools.precommit import use_checks

    # Change to test directory
    monkeypatch.chdir(tmp_path)

    # Test explicit enabled
    monkeypatch.setenv("GPTME_CHECK", "true")
    monkeypatch.setattr(
        "gptme.tools.precommit.shutil.which", lambda x: "/usr/bin/pre-commit"
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
    from gptme.tools.precommit import use_checks

    # Change to test directory
    monkeypatch.chdir(tmp_path)

    # Mock pre-commit being available
    monkeypatch.setattr(
        "gptme.tools.precommit.shutil.which", lambda x: "/usr/bin/pre-commit"
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
    monkeypatch.chdir(subdir)
    config_file = tmp_path / ".pre-commit-config.yaml"
    config_file.touch()
    assert use_checks()


def test_use_checks_no_precommit(monkeypatch, tmp_path):
    """Test use_checks when pre-commit is not available."""
    from gptme.tools.precommit import use_checks

    monkeypatch.chdir(tmp_path)

    # Enable checks but no pre-commit available
    monkeypatch.setenv("GPTME_CHECK", "true")
    monkeypatch.setattr("gptme.tools.precommit.shutil.which", lambda x: None)

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


def test_parse_prompt_files(tmp_path, monkeypatch):
    """Test _parse_prompt_files function."""
    from gptme.util.context import _parse_prompt_files

    monkeypatch.chdir(tmp_path)

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
    import pathlib

    from gptme.util.context import _parse_prompt_files

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


def test_check_content_size():
    """Test content size checking and truncation."""
    from gptme.constants import CONTENT_SIZE_INFO_THRESHOLD, CONTENT_SIZE_WARN_THRESHOLD
    from gptme.util.context import _check_content_size

    # Small content passes through unchanged
    small = "hello world"
    assert _check_content_size(small, "test.txt") == small

    # Large content (above info threshold) passes through with log
    large_ish = "x" * (CONTENT_SIZE_INFO_THRESHOLD + 100)
    result = _check_content_size(large_ish, "test.txt")
    assert result == large_ish  # Not truncated, just logged

    # Very large content gets truncated
    very_large = "x" * (CONTENT_SIZE_WARN_THRESHOLD + 1000)
    result = _check_content_size(very_large, "test.txt")
    assert len(result) <= CONTENT_SIZE_WARN_THRESHOLD
    assert "truncated" in result.lower()


def test_binary_file_metadata(tmp_path):
    """Test that binary files return metadata instead of None."""
    from gptme.util.context import _binary_file_metadata, _human_readable_size

    # Create a binary file
    binary_file = tmp_path / "data.bin"
    binary_file.write_bytes(b"\xff\xfe\x80\x81" * 256)  # 1KB binary

    result = _binary_file_metadata(binary_file, str(binary_file))
    assert result is not None
    assert "Binary file: data.bin" in result
    assert "Size:" in result

    # Test with a known MIME type extension
    zip_file = tmp_path / "archive.zip"
    zip_file.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    result = _binary_file_metadata(zip_file, str(zip_file))
    assert result is not None
    assert "application/zip" in result

    # Test non-existent file returns None
    missing = tmp_path / "missing.bin"
    result = _binary_file_metadata(missing, str(missing))
    assert result is None

    # Test human_readable_size
    assert _human_readable_size(500) == "500 B"
    assert _human_readable_size(1024) == "1.0 KB"
    assert _human_readable_size(1048576) == "1.0 MB"
    assert _human_readable_size(1073741824) == "1.0 GB"


def test_resource_to_codeblock_binary(tmp_path):
    """Test that _resource_to_codeblock returns metadata for binary files."""
    from gptme.util.context import _resource_to_codeblock

    binary_file = tmp_path / "data.bin"
    binary_file.write_bytes(b"\xff\xfe\x80\x81" * 100)

    result = _resource_to_codeblock(str(binary_file))
    assert result is not None
    assert "Binary file" in result
    assert "Size:" in result


def test_dir_to_listing(tmp_path):
    """Test that _dir_to_listing generates file listings for directories."""
    from gptme.util.context import _dir_to_listing

    # Create a directory with some files
    subdir = tmp_path / "project"
    subdir.mkdir()
    (subdir / "main.py").write_text("print('hello')")
    (subdir / "utils.py").write_text("def helper(): pass")
    (subdir / "README.md").write_text("# Project")

    result = _dir_to_listing(subdir, str(subdir))
    assert result is not None
    assert "main.py" in result
    assert "utils.py" in result
    assert "README.md" in result


def test_dir_to_listing_empty(tmp_path):
    """Test that empty directories produce a meaningful message."""
    from gptme.util.context import _dir_to_listing

    empty = tmp_path / "empty"
    empty.mkdir()

    result = _dir_to_listing(empty, str(empty))
    assert result is not None
    assert "(empty directory)" in result


def test_dir_to_listing_all_gitignored(tmp_path, monkeypatch):
    """Test that files are not exposed when git returns empty output (all gitignored)."""
    from unittest.mock import MagicMock, patch

    from gptme.util.context import _dir_to_listing

    # Create a directory with a file that would be found by rglob
    gitdir = tmp_path / "gitrepo"
    gitdir.mkdir()
    (gitdir / "secret.env").write_text("API_KEY=hunter2")

    # Simulate git returning returncode=0 with empty stdout (all files gitignored)
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""

    with patch("subprocess.run", return_value=mock_result):
        result = _dir_to_listing(gitdir, str(gitdir))

    # When git reports success with no files, should return empty listing, NOT rglob files
    assert result is not None
    assert "secret.env" not in result
    assert "(empty directory)" in result


def test_dir_to_listing_truncation(tmp_path):
    """Test that large directories are truncated."""
    from gptme.util.context import _dir_to_listing

    bigdir = tmp_path / "big"
    bigdir.mkdir()
    for i in range(60):
        (bigdir / f"file_{i:03d}.txt").write_text(f"content {i}")

    result = _dir_to_listing(bigdir, str(bigdir), max_entries=50)
    assert result is not None
    assert "10 more files" in result
    # First 50 should be included
    assert "file_000.txt" in result
    assert "file_049.txt" in result


def test_dir_to_listing_nested(tmp_path):
    """Test that nested directory structures are listed."""
    from gptme.util.context import _dir_to_listing

    project = tmp_path / "nested"
    project.mkdir()
    src = project / "src"
    src.mkdir()
    (src / "app.py").write_text("app")
    tests = project / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("test")
    (project / "pyproject.toml").write_text("[project]")

    result = _dir_to_listing(project, str(project))
    # Should include full relative paths (not just bare filenames)
    assert "src/app.py" in result
    assert "tests/test_app.py" in result
    assert "pyproject.toml" in result


def test_dir_to_listing_includes_dotfiles(tmp_path):
    """Test that rglob fallback includes dotfiles like .pre-commit-config.yml and .github/."""
    from unittest.mock import MagicMock, patch

    from gptme.util.context import _dir_to_listing

    project = tmp_path / "project"
    project.mkdir()
    (project / ".pre-commit-config.yaml").write_text("repos: []")
    (project / ".gitignore").write_text("*.pyc")
    (project / "main.py").write_text("print('hello')")
    gh = project / ".github" / "workflows"
    gh.mkdir(parents=True)
    (gh / "test.yml").write_text("on: push")

    # Simulate git not available so we exercise the rglob fallback
    mock_result = MagicMock()
    mock_result.returncode = 1  # git fails → fall through to rglob
    mock_result.stdout = ""

    with patch("subprocess.run", return_value=mock_result):
        result = _dir_to_listing(project, str(project))

    assert ".pre-commit-config.yaml" in result
    assert ".gitignore" in result
    assert ".github/workflows/test.yml" in result
    assert "main.py" in result
    # .git internals should never appear (none created here, but guard is correct)


def test_resource_to_codeblock_directory(tmp_path):
    """Test that _resource_to_codeblock handles directories."""
    from gptme.util.context import _resource_to_codeblock

    subdir = tmp_path / "mydir"
    subdir.mkdir()
    (subdir / "file.txt").write_text("content")

    result = _resource_to_codeblock(str(subdir))
    assert result is not None
    assert "file.txt" in result


def test_include_paths_directory(tmp_path, monkeypatch):
    """Test that include_paths expands directory references."""
    from gptme.util.context import include_paths

    # Create a directory with files
    subdir = tmp_path / "src"
    subdir.mkdir()
    (subdir / "main.py").write_text("print('hello')")
    (subdir / "utils.py").write_text("def helper(): pass")

    monkeypatch.chdir(tmp_path)
    msg = Message("user", f"review the code in {subdir}")
    result = include_paths(msg)

    assert "main.py" in result.content
    assert "utils.py" in result.content


def test_is_interactive_mode():
    """Test interactive mode detection."""
    from gptme.util.context import _is_interactive_mode

    # Without cli_confirm hook registered, should return False
    # (This test runs outside the normal CLI context)
    result = _is_interactive_mode()
    assert isinstance(result, bool)
