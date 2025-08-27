import os
from unittest.mock import patch

import pytest
from gptme.util.prompt import (
    PathLexer,
    check_cwd,
    get_input,
    is_valid_path,
)
from pygments.token import Name, Text


@pytest.fixture
def test_dir(tmp_path):
    """Create a test directory with some files."""
    # Create test files and directories
    (tmp_path / "file.txt").touch()
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "subfile.txt").touch()
    (tmp_path / "space dir").mkdir()
    (tmp_path / "space dir" / "file with spaces.txt").touch()
    return tmp_path


def test_is_valid_path(test_dir, monkeypatch):
    """Test path validation function."""
    # Change to test directory
    monkeypatch.chdir(test_dir)

    # Test absolute paths
    assert is_valid_path(str(test_dir / "file.txt"))
    assert is_valid_path(str(test_dir / "dir/subfile.txt"))

    # Test relative paths
    assert is_valid_path("file.txt")
    assert is_valid_path("./file.txt")
    assert is_valid_path("dir/subfile.txt")

    # Test paths with spaces
    assert is_valid_path(str(test_dir / "space dir/file with spaces.txt"))
    assert is_valid_path("'space dir/file with spaces.txt'")
    assert is_valid_path('"space dir/file with spaces.txt"')
    assert is_valid_path(r"space\ dir/file\ with\ spaces.txt")

    # Test non-existent paths with existing parents
    # assert is_valid_path("dir/newfile.txt")
    # assert is_valid_path("space dir/newfile.txt")

    # Test invalid paths
    assert not is_valid_path("nonexistent.txt")
    assert not is_valid_path("/definitely/not/a/real/path")
    assert not is_valid_path("not/a/path/at/all")


# Test cases for path lexer
PATH_LEXER_CASES = [
    pytest.param("Check {}/file.txt exists", Name.Variable, id="absolute_path"),
    # FIXME
    # pytest.param("Look at file.txt here", Name.Variable, id="simple_relative_path"),
    pytest.param("Check ./file.txt", Name.Variable, id="explicit_relative_path"),
    pytest.param("Go to ../other/path", Text, id="nonexistent_path"),
    pytest.param(
        "Open 'space dir/file with spaces.txt'", Name.Variable, id="quoted_path"
    ),
    pytest.param(
        "Edit space\\ dir/file\\ with\\ spaces.txt", Name.Variable, id="escaped_spaces"
    ),
    pytest.param("This is not/a/real/path here", Text, id="invalid_path"),
]


@pytest.mark.parametrize("text_template,expected_token", PATH_LEXER_CASES)
def test_path_lexer(test_dir, text_template, expected_token, monkeypatch):
    """Test path highlighting in the lexer."""
    lexer = PathLexer()
    monkeypatch.chdir(test_dir)

    # Format the text with test_dir if needed
    text = text_template.format(test_dir)

    tokens = list(lexer.get_tokens(text))
    # Find the path-like segment and check its token
    path_token = next(
        (
            token
            for token, text in tokens
            if ("/" in text) and (token in (Name.Variable, Text))
        ),
        None,
    )
    assert path_token == expected_token, f"Failed for: {text}"


def test_path_lexer_caching():
    """Test that path validation results are cached."""
    # Create test paths
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = True

        # First call
        result1 = is_valid_path("/some/path")
        cache_info1 = is_valid_path.cache_info()

        # Second call with same path
        result2 = is_valid_path("/some/path")
        cache_info2 = is_valid_path.cache_info()

        # Verify cache hit
        assert result1 is result2 is True
        assert cache_info2.hits == cache_info1.hits + 1


def test_symlink_handling(test_dir, monkeypatch):
    """Test handling of symlinks."""
    monkeypatch.chdir(test_dir)
    check_cwd()  # we should run this automatically somehow

    # Create a symlink
    source = test_dir / "file.txt"
    link = test_dir / "link.txt"
    os.symlink(source, link)

    # Test symlink validation
    assert is_valid_path("link.txt")
    # Test broken symlink
    os.remove(source)
    check_cwd()
    assert not is_valid_path("link.txt")


def test_cache_clearing_on_chdir(test_dir, tmp_path):
    """Test that cache is cleared when directory changes."""
    with (
        patch("pathlib.Path.cwd", return_value=test_dir),
        patch("time.time", side_effect=[0.0, 0.0, 2.0]),
    ):  # Force cache check
        # First call in test_dir
        is_valid_path("file.txt")
        is_valid_path.cache_info()

        # Change directory
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            # Create a new file in tmp_path
            (tmp_path / "other.txt").touch()

            # Check directory and verify cache was cleared
            check_cwd()
            is_valid_path("other.txt")
            cache_info2 = is_valid_path.cache_info()

            # Verify cache was cleared
            assert cache_info2.hits == 0
            assert cache_info2.currsize == 1


def test_get_input_cache_clearing():
    """Test that get_input clears cache on directory change."""
    time_values = [
        0.0,
        0.0,
        0.5,
        2.0,
        2.5,
    ]  # First two calls in same second, then after interval

    with (
        patch("os.getcwd") as mock_getcwd,
        patch("prompt_toolkit.PromptSession.prompt") as mock_prompt,
        patch("time.time", side_effect=time_values),
    ):
        # First call in /dir1
        mock_getcwd.return_value = "/dir1"
        mock_prompt.return_value = "test"
        get_input("prompt> ")
        cache_info1 = is_valid_path.cache_info()

        # Second call in same directory (within interval)
        get_input("prompt> ")
        cache_info2 = is_valid_path.cache_info()
        assert (
            cache_info2.currsize == cache_info1.currsize
        ), "Cache should not be cleared in same directory"

        # Call in different directory (after interval)
        mock_getcwd.return_value = "/dir2"
        get_input("prompt> ")
        cache_info3 = is_valid_path.cache_info()
        assert (
            cache_info3.currsize == 0
        ), "Cache should be cleared after directory change"
