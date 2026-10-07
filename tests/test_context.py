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


def test_file_to_display_path_deleted_cwd(tmp_path, monkeypatch):
    """Should not crash when CWD has been deleted mid-session (ENOENT)."""
    import pathlib

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file = workspace / "test.txt"
    file.touch()

    def raise_enoent():
        raise FileNotFoundError("[Errno 2] No such file or directory")

    monkeypatch.setattr(pathlib.Path, "cwd", staticmethod(raise_enoent))

    # Should not raise; file is already absolute so returns as-is
    result = file_to_display_path(file, workspace)
    assert result == file

    # Relative paths must not crash either (f.absolute() would need the CWD)
    rel = Path("rel/test.txt")
    assert file_to_display_path(rel, workspace) == rel


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


def test_resource_to_codeblock_does_not_slurp_huge_file(tmp_path, monkeypatch):
    """A file far above the size cap is read in bounded form, not via read_text()."""
    import pathlib

    from gptme.constants import CONTENT_SIZE_WARN_THRESHOLD
    from gptme.util.context import _resource_to_codeblock

    big = tmp_path / "big.log"
    big.write_text("A" * (CONTENT_SIZE_WARN_THRESHOLD * 20))

    def no_read_text(self, *args, **kwargs):
        raise AssertionError("read_text() loads the whole file before truncating")

    monkeypatch.setattr(pathlib.Path, "read_text", no_read_text)

    result = _resource_to_codeblock(str(big))
    assert result is not None
    assert "truncated" in result.lower()
    assert f"{big.stat().st_size:,} bytes" in result
    assert result.count("A") <= CONTENT_SIZE_WARN_THRESHOLD


def test_attached_text_file_embedding_is_capped(tmp_path):
    """Attached text files (msg.files) are truncated like path-in-prompt files."""
    from gptme.constants import CONTENT_SIZE_WARN_THRESHOLD
    from gptme.message import Message
    from gptme.util.context import embed_attached_file_content

    big = tmp_path / "big.log"
    big.write_text("C" * (CONTENT_SIZE_WARN_THRESHOLD * 20))

    msg = embed_attached_file_content(Message("user", "see file", files=[big]))
    assert "truncated" in msg.content.lower()
    assert msg.content.count("C") <= CONTENT_SIZE_WARN_THRESHOLD


def test_resource_to_codeblock_file_at_cap_not_truncated(tmp_path):
    """A file exactly at the cap is included whole, with no truncation note."""
    from gptme.constants import CONTENT_SIZE_WARN_THRESHOLD
    from gptme.util.context import _resource_to_codeblock

    f = tmp_path / "exact.txt"
    f.write_text("B" * CONTENT_SIZE_WARN_THRESHOLD)

    result = _resource_to_codeblock(str(f))
    assert result is not None
    assert result.count("B") == CONTENT_SIZE_WARN_THRESHOLD
    assert "truncated" not in result.lower()


def test_stored_attachment_content_is_capped(tmp_path, monkeypatch):
    """Content-addressed copies of attachments are capped too (not read whole)."""
    import pathlib
    from types import SimpleNamespace

    from gptme.constants import CONTENT_SIZE_WARN_THRESHOLD
    from gptme.util.context import embed_attached_file_content
    from gptme.util.file_storage import store_file

    logdir = tmp_path / "log"
    logdir.mkdir()
    big = tmp_path / "big.log"
    big.write_text("D" * (CONTENT_SIZE_WARN_THRESHOLD * 20))
    file_hash, _ = store_file(logdir, big)
    # Make fallback observably wrong: only the stored snapshot contains Ds.
    big.write_text("fallback content")

    monkeypatch.setattr(
        "gptme.logmanager.LogManager.get_current_log",
        staticmethod(lambda: SimpleNamespace(logdir=logdir)),
    )

    def no_read_text(self, *args, **kwargs):
        raise AssertionError("read_text() loads the whole file before truncating")

    monkeypatch.setattr(pathlib.Path, "read_text", no_read_text)

    msg = embed_attached_file_content(
        Message("user", "see file", files=[big], file_hashes={str(big): file_hash})
    )
    assert "truncated" in msg.content.lower()
    assert msg.content.count("D") <= CONTENT_SIZE_WARN_THRESHOLD


def test_read_text_capped_keeps_large_pdf_as_attachment(tmp_path, monkeypatch):
    """A PDF is blocked by MIME type before any content is read, so even one
    with an all-decodable prefix stays an attachment for provider handling."""
    from gptme.constants import CONTENT_SIZE_WARN_THRESHOLD
    from gptme.util.context import include_paths

    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    monkeypatch.delenv("GPTME_FRESH", raising=False)
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.7\n" + b"A" * (CONTENT_SIZE_WARN_THRESHOLD * 2) + b"\xff")

    msg = include_paths(Message("user", str(pdf)))
    assert msg.files == [pdf]
    assert "%PDF-1.7" not in msg.content


def test_read_text_capped_large_prefix_then_invalid_utf8_is_text(tmp_path):
    """A large text file whose capped prefix is decodable (no NUL) is
    truncated, not rejected: the invalid bytes beyond the cap are never read.

    This exercises the bounded-read path itself, not the MIME pre-check —
    under a whole-file read this would raise UnicodeDecodeError instead.
    """
    from gptme.constants import CONTENT_SIZE_WARN_THRESHOLD
    from gptme.util.context import _read_text_capped

    f = tmp_path / "events.txt"
    f.write_bytes(b"L" * (CONTENT_SIZE_WARN_THRESHOLD * 2) + b"\xff\xff")
    result = _read_text_capped(f)
    assert "truncated" in result.lower()
    assert len(result) <= CONTENT_SIZE_WARN_THRESHOLD


def test_read_text_capped_treats_nul_prefix_as_binary(tmp_path):
    """A truncated prefix containing NUL is binary, not text."""
    import pytest

    from gptme.constants import CONTENT_SIZE_WARN_THRESHOLD
    from gptme.util.context import _read_text_capped

    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00" + b"E" * (CONTENT_SIZE_WARN_THRESHOLD * 2))
    with pytest.raises(UnicodeDecodeError):
        _read_text_capped(f)


def test_read_text_capped_json_file_is_not_binary(tmp_path):
    """JSON files (application/json MIME) should be read as text, not rejected."""
    from gptme.util.context import _read_text_capped

    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}')
    result = _read_text_capped(f)
    assert "value" in result


def test_read_text_capped_svg_file_is_not_binary(tmp_path):
    """SVG files (image/svg+xml) are a text format and must not be rejected."""
    from gptme.util.context import _read_text_capped

    svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>'
    f = tmp_path / "icon.svg"
    f.write_text(svg_content)
    result = _read_text_capped(f)
    assert "circle" in result


def test_read_text_capped_survives_path_removed_after_read(tmp_path, monkeypatch):
    """The size comes from the open handle, so rotation after read can't raise."""
    import pathlib

    from gptme.constants import CONTENT_SIZE_WARN_THRESHOLD
    from gptme.util.context import _read_text_capped

    f = tmp_path / "rotated.log"
    f.write_text("F" * (CONTENT_SIZE_WARN_THRESHOLD * 2))

    def no_stat(self, *args, **kwargs):
        raise FileNotFoundError(str(self))

    monkeypatch.setattr(pathlib.Path, "stat", no_stat)
    assert "truncated" in _read_text_capped(f).lower()


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


def test_resource_to_codeblock_skips_unreadable_fallback_word(tmp_path, monkeypatch):
    """Permission errors while scanning words in prose must not escape."""
    from unittest.mock import patch

    from gptme.util.context import _resource_to_codeblock

    inaccessible = "/private/file.txt"
    prompt = f"cat {inaccessible}"
    monkeypatch.chdir(tmp_path)

    def exists(path: Path) -> bool:
        if str(path) == inaccessible:
            raise PermissionError
        return False

    monkeypatch.setattr(Path, "exists", exists)
    with patch("gptme.util.context.logger.warning") as warning:
        assert _resource_to_codeblock(prompt) == ""
    warning.assert_called_once_with("Skipping unreadable file: %s", inaccessible)


def test_resource_to_codeblock_keeps_readable_fallback_word(tmp_path, monkeypatch):
    """An unreadable path must not hide another readable path in the prompt."""
    from unittest.mock import patch

    from gptme.util.context import _resource_to_codeblock

    inaccessible = "/private/file.txt"
    readable = tmp_path / "readable.txt"
    readable.write_text("readable content")
    prompt = f"{inaccessible} {readable}"

    original_exists = Path.exists

    def exists(path: Path) -> bool:
        if str(path) in (prompt, inaccessible):
            raise PermissionError
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", exists)
    with patch("gptme.util.context.logger.warning"):
        result = _resource_to_codeblock(prompt)

    assert result is not None
    assert "readable content" in result


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
    """git ls-files listings are truncated after the known total is computed."""
    from unittest.mock import MagicMock, patch

    from gptme.util.context import _dir_to_listing

    bigdir = tmp_path / "big"
    bigdir.mkdir()
    files = [f"file_{i:03d}.txt" for i in range(60)]
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "\n".join(files) + "\n"

    with patch("subprocess.run", return_value=mock_result):
        result = _dir_to_listing(bigdir, str(bigdir), max_entries=50)
    assert result is not None
    assert "10 more files" in result
    assert "file_000.txt" in result
    assert "file_049.txt" in result
    assert "file_059.txt" not in result


def test_dir_to_listing_bounds_rglob_walk(tmp_path):
    """Fallback must stop once max_entries accepted files are collected."""
    from unittest.mock import MagicMock, patch

    from gptme.util.context import _dir_to_listing

    bigdir = tmp_path / "big"
    bigdir.mkdir()
    for i in range(80):
        (bigdir / f"file_{i:03d}.txt").write_text("x")
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with patch("subprocess.run", return_value=mock_result):
        result = _dir_to_listing(bigdir, str(bigdir), max_entries=50)

    assert result is not None
    assert "listing truncated at 50 files" in result
    assert result.count(".txt") == 50


def test_dir_to_listing_bounds_directory_heavy_walk(tmp_path):
    """Visit budget stops a tree of directories with almost no files."""
    from unittest.mock import MagicMock, patch

    from gptme.util.context import _dir_to_listing

    tree = tmp_path / "dirs"
    tree.mkdir()
    cursor = tree
    for i in range(80):
        cursor = cursor / f"d{i:02d}"
        cursor.mkdir()
    (cursor / "deep.txt").write_text("late")
    (tree / "early.txt").write_text("early")

    scandir_calls: list[str] = []
    real_scandir = __import__("os").scandir

    def spy_scandir(path):
        scandir_calls.append(str(path))
        if len(scandir_calls) > 40:
            raise AssertionError("visit budget did not bound directory traversal")
        return real_scandir(path)

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with (
        patch("subprocess.run", return_value=mock_result),
        patch("os.scandir", side_effect=spy_scandir),
    ):
        result = _dir_to_listing(
            tree, str(tree), max_entries=50, max_visited=12, max_seconds=2.0
        )

    assert result is not None
    assert "deep.txt" not in result
    assert len(scandir_calls) < 40


def test_dir_to_listing_prunes_git_before_recursion(tmp_path):
    """`.git` is counted as an excluded entry and never descended into."""
    from unittest.mock import MagicMock, patch

    from gptme.util.context import _dir_to_listing

    project = tmp_path / "repo"
    project.mkdir()
    (project / "readme.txt").write_text("ok")
    git_objects = project / ".git" / "objects" / "aa"
    git_objects.mkdir(parents=True)
    for i in range(80):
        (git_objects / f"{i:02d}").write_text("blob")

    scandir_paths: list[str] = []
    real_scandir = __import__("os").scandir

    def spy_scandir(path):
        scandir_paths.append(str(path))
        return real_scandir(path)

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with (
        patch("subprocess.run", return_value=mock_result),
        patch("os.scandir", side_effect=spy_scandir),
    ):
        result = _dir_to_listing(project, str(project), max_entries=50, max_visited=20)

    assert "readme.txt" in result
    assert ".git/" not in result
    assert not any(".git" in path.split("/") for path in scandir_paths)


def test_dir_to_listing_bounds_excluded_entries(tmp_path):
    """Directory entries consume the visit budget even when they yield no files."""
    from gptme.util.context import _fallback_dir_listing

    tree = tmp_path / "mixed"
    tree.mkdir()
    cursor = tree
    for i in range(30):
        cursor = cursor / f"dir_{i:02d}"
        cursor.mkdir()
    (cursor / "late.txt").write_text("late")
    (tree / "only.txt").write_text("one")

    entries, truncated = _fallback_dir_listing(
        tree, max_entries=50, max_visited=10, max_seconds=2.0
    )
    assert truncated
    assert "late.txt" not in entries
    assert all(not name.endswith("late.txt") for name in entries)


def test_dir_to_listing_time_budget(tmp_path):
    """A zero-second deadline stops the walk without collecting the tree."""
    from unittest.mock import MagicMock, patch

    from gptme.util.context import _dir_to_listing

    tree = tmp_path / "timed"
    tree.mkdir()
    nested = tree / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "late.txt").write_text("late")

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with patch("subprocess.run", return_value=mock_result):
        result = _dir_to_listing(
            tree, str(tree), max_entries=50, max_visited=1000, max_seconds=0.0
        )

    assert result is not None
    assert "late.txt" not in result


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

    # Ensure path expansion is not suppressed (may be set in autonomous/CI environments)
    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)

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


def test_extract_urls_basic():
    """Test that extract_urls finds HTTP/HTTPS URLs in content."""
    from gptme.util.context import extract_urls

    content = "Check out https://example.com and http://foo.bar/baz for details."
    urls = extract_urls(content)
    assert "https://example.com" in urls
    assert "http://foo.bar/baz" in urls


def test_extract_urls_ignores_code_blocks():
    """URLs inside code blocks should not be extracted."""
    from gptme.util.context import extract_urls

    content = (
        "```\nhttps://inside.codeblock.com\n```\nBut https://outside.com is found."
    )
    urls = extract_urls(content)
    assert "https://inside.codeblock.com" not in urls
    assert "https://outside.com" in urls


def test_extract_urls_empty():
    from gptme.util.context import extract_urls

    assert extract_urls("no urls here") == []
    assert extract_urls("") == []


def test_include_paths_pre_confirmed_urls_allows(tmp_path, monkeypatch):
    """pre_confirmed_urls lets the caller pre-approve specific URLs."""
    from unittest.mock import patch

    from gptme.message import Message
    from gptme.util.context import include_paths

    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    msg = Message("user", "See https://example.com for details")
    fake_content = "Example page content"

    with (
        patch("gptme.util.context._resource_to_codeblock", return_value=fake_content),
        patch("gptme.util.context.use_fresh_context", return_value=False),
    ):
        result = include_paths(
            msg, tmp_path, pre_confirmed_urls=["https://example.com"]
        )

    # URL was pre-confirmed, so content should be attached
    assert fake_content in result.content


def test_include_paths_pre_confirmed_urls_empty_skips_all(tmp_path, monkeypatch):
    """pre_confirmed_urls=[] skips all URL fetching without calling _confirm_urls."""
    from unittest.mock import patch

    from gptme.message import Message
    from gptme.util.context import include_paths

    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    msg = Message("user", "See https://example.com for details")

    with patch("gptme.util.context._confirm_urls") as mock_confirm:
        result = include_paths(msg, tmp_path, pre_confirmed_urls=[])

    # _confirm_urls should NOT be called — caller already decided
    mock_confirm.assert_not_called()
    # URL should not be fetched — result content is unchanged (just the original text)
    assert result.content == msg.content


def test_is_too_broad_directory_root_and_home():
    from pathlib import Path

    from gptme.util.context import _is_too_broad_directory

    assert _is_too_broad_directory(Path("/"))
    assert _is_too_broad_directory(Path.home())


def test_is_too_broad_directory_temp_roots():
    """Temp roots are blocked by resolved identity, not path depth.

    On macOS /tmp resolves to /private/tmp (three parts), which the old
    len(parts) < 3 heuristic treated as a project directory.
    """
    import tempfile
    from pathlib import Path

    from gptme.util.context import _is_too_broad_directory

    assert _is_too_broad_directory(Path("/tmp"))
    assert _is_too_broad_directory(Path("/private/tmp"))
    assert _is_too_broad_directory(Path("/var/tmp"))
    assert _is_too_broad_directory(Path(tempfile.gettempdir()))


def test_is_too_broad_directory_allows_project_subdir(tmp_path):
    from gptme.util.context import _is_too_broad_directory

    sub = tmp_path / "src"
    sub.mkdir()
    assert not _is_too_broad_directory(sub)
    # Nested temp workspaces must remain attachable.
    assert not _is_too_broad_directory(tmp_path)


def test_include_paths_does_not_scan_root(monkeypatch):
    """A standalone slash must not trigger recursive root listing.

    Do not enumerate the real root filesystem; mock the listing helper.
    """
    from pathlib import Path
    from unittest.mock import patch

    from gptme.message import Message
    from gptme.util.context import include_paths

    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    with patch(
        "gptme.util.context._dir_to_listing",
        return_value="[listing suppressed]",
    ) as listing:
        include_paths(
            Message("user", "also file the rm -rf / false-positive"),
            Path.cwd(),
        )
        called_paths = [Path(c.args[0]).resolve() for c in listing.call_args_list]
        assert Path("/") not in called_paths


def test_include_paths_does_not_scan_root_from_markdown_heading(monkeypatch):
    """Ordinary markdown ' / ' must not attach filesystem root (#3758)."""
    from pathlib import Path
    from unittest.mock import patch

    from gptme.message import Message
    from gptme.util.context import include_paths

    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    with patch(
        "gptme.util.context._dir_to_listing",
        return_value="[listing suppressed]",
    ) as listing:
        include_paths(
            Message("user", "**Still open / not done:**"),
            Path.cwd(),
        )
        assert listing.call_args_list == []


def test_include_paths_does_not_scan_tmp_from_prose(monkeypatch):
    """Prose mentioning /tmp must not attach the temp root (#3758)."""
    from pathlib import Path
    from unittest.mock import patch

    from gptme.message import Message
    from gptme.util.context import include_paths

    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    with patch(
        "gptme.util.context._dir_to_listing",
        return_value="[listing suppressed]",
    ) as listing:
        include_paths(
            Message("user", "rm -rf /tmp,"),
            Path.cwd(),
        )
        called_paths = [Path(c.args[0]).resolve() for c in listing.call_args_list]
        tmp_roots = {Path("/tmp").resolve(), Path("/private/tmp").resolve()}
        assert tmp_roots.isdisjoint(called_paths)


def _unreadable_file(tmp_path: Path) -> Path:
    import os

    import pytest

    f = tmp_path / "noperm.txt"
    f.write_text("secret")
    f.chmod(0o000)
    if os.access(f, os.R_OK):  # running as root: chmod does not restrict reads
        pytest.skip("cannot make a file unreadable as this user")
    return f


def test_include_paths_unreadable_file_is_skipped(tmp_path, monkeypatch):
    """An unreadable file mentioned in a prompt must not crash path inclusion."""
    from gptme.util.context import include_paths

    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    monkeypatch.chdir(tmp_path)
    f = _unreadable_file(tmp_path)
    try:
        msg = include_paths(Message("user", f"look at {f}"), workspace=tmp_path)
    finally:
        f.chmod(0o644)
    assert msg.content == f"look at {f}"
    assert not msg.files


def test_include_paths_inaccessible_parent_is_skipped(tmp_path, monkeypatch):
    """A path below an inaccessible directory must not fail the budget pre-check."""
    from unittest.mock import patch

    from gptme.util.context import include_paths

    path = tmp_path / "private" / "file.txt"
    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    monkeypatch.setattr("gptme.util.context.use_fresh_context", lambda: False)
    monkeypatch.setattr(
        "gptme.util.context._find_potential_paths", lambda _: [str(path)]
    )
    with patch("pathlib.Path.is_file", side_effect=PermissionError) as is_file:
        msg = include_paths(Message("user", f"look at {path}"), workspace=tmp_path)
    is_file.assert_called_once()
    assert msg.content == f"look at {path}"
    assert not msg.files


def test_parse_prompt_files_unreadable_file(tmp_path, monkeypatch):
    from gptme.util.context import _parse_prompt_files

    monkeypatch.chdir(tmp_path)
    f = _unreadable_file(tmp_path)
    try:
        assert _parse_prompt_files(str(f)) is None
    finally:
        f.chmod(0o644)
