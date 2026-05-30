"""Tests for patch_many: atomic multi-file patch tool."""

import json
from pathlib import Path

import pytest

from gptme.tools import clear_tools, get_tool, init_tools
from gptme.tools.patch import Patch
from gptme.tools.patch_many import (
    _parse_patches_from_content,
    _parse_patches_from_kwargs,
    execute_patch_many_impl,
)


def test_basic_parse():
    """Test parsing patches from codeblock content."""
    paths = [Path("/tmp/a.txt"), Path("/tmp/b.txt")]
    pairs = _parse_patches_from_content(
        """
<<<<<<< ORIGINAL
a content
=======
a modified
>>>>>>> UPDATED

<<<<<<< ORIGINAL
b content
=======
b modified
>>>>>>> UPDATED
""".strip(),
        paths,
    )
    assert len(pairs) == 2


def test_path_count_mismatch():
    """Test that mismatched path/patch counts raise."""
    paths = [Path("/tmp/a.txt")]
    with pytest.raises(ValueError, match="2 patch\\(es\\) but 1 file"):
        _parse_patches_from_content(
            """
<<<<<<< ORIGINAL
a content
=======
a modified
>>>>>>> UPDATED

<<<<<<< ORIGINAL
b content
=======
b modified
>>>>>>> UPDATED
""".strip(),
            paths,
        )


def test_atomicity(tmp_path):
    """Test that if one patch fails, NO files are written."""
    f1 = tmp_path / "file1.py"
    f2 = tmp_path / "file2.py"
    f1.write_text("original content")
    f2.write_text("original content")

    patches = [
        (f1, Patch("original content", "modified content")),
        (f2, Patch("nonexistent content", "modified content")),
    ]

    messages = list(execute_patch_many_impl(patches))
    assert messages
    assert "aborted" in messages[0].content
    assert "patch failed" in messages[0].content.lower()
    assert f1.read_text() == "original content"
    assert f2.read_text() == "original content"


def test_atomic_success(tmp_path):
    """Test that all files are patched when all patches match."""
    f1 = tmp_path / "file1.py"
    f2 = tmp_path / "file2.py"
    f1.write_text("original a")
    f2.write_text("original b")

    patches = [
        (f1, Patch("original a", "modified a")),
        (f2, Patch("original b", "modified b")),
    ]

    messages = list(execute_patch_many_impl(patches))
    assert messages
    assert "atomically" in messages[0].content.lower()
    assert "2" in messages[0].content
    assert f1.read_text() == "modified a"
    assert f2.read_text() == "modified b"


def test_atomic_missing_file(tmp_path):
    """Test that a missing file aborts the entire batch."""
    f1 = tmp_path / "exists.py"
    f2 = tmp_path / "missing.py"
    f1.write_text("original")

    patches = [
        (f1, Patch("original", "modified")),
        (f2, Patch("original", "modified")),
    ]

    messages = list(execute_patch_many_impl(patches))
    assert messages
    assert "aborted" in messages[0].content
    assert "file not found" in messages[0].content.lower()
    assert f1.read_text() == "original"


def test_atomic_single_file(tmp_path):
    """Test patch_many with a single file."""
    f = tmp_path / "single.py"
    f.write_text("single file content")

    messages = list(
        execute_patch_many_impl(
            [(f, Patch("single file content", "single file modified"))]
        )
    )
    assert messages
    assert "atomically" in messages[0].content.lower()
    assert f.read_text() == "single file modified"


def test_apply_updates_segment(tmp_path):
    """Test patching only part of a file via patch_many."""
    f = tmp_path / "module.py"
    f.write_text(
        """
def old_func():
    return 1

def untouched():
    return 2
""".strip()
    )

    patches = [
        (
            f,
            Patch(
                "def old_func():\n    return 1",
                "def new_func():\n    return 42",
            ),
        ),
    ]

    list(execute_patch_many_impl(patches))
    content = f.read_text()
    assert "new_func" in content
    assert "untouched" in content
    assert "old_func" not in content


def test_kwargs_parse_simple():
    """Test parsing patches from kwargs format."""
    entries = _parse_patches_from_kwargs(
        {
            "patches": [
                {
                    "path": "/tmp/x.txt",
                    "patch": "<<<<<<< ORIGINAL\nx\n=======\ny\n>>>>>>> UPDATED",
                },
                {
                    "path": "/tmp/y.txt",
                    "patch": "<<<<<<< ORIGINAL\ny\n=======\nz\n>>>>>>> UPDATED",
                },
            ]
        }
    )
    assert len(entries) == 2
    assert entries[0][0] == Path("/tmp/x.txt")
    assert isinstance(entries[1][1], str)


def test_kwargs_json_string():
    """Test parsing patches from a JSON string in kwargs."""
    entries = _parse_patches_from_kwargs(
        {
            "patches": json.dumps(
                [
                    {
                        "path": "/tmp/z.txt",
                        "patch": "<<<<<<< ORIGINAL\nz\n=======\nw\n>>>>>>> UPDATED",
                    },
                ]
            )
        }
    )
    assert len(entries) == 1
    assert entries[0][0] == Path("/tmp/z.txt")


def test_kwargs_missing_field():
    """Test that missing 'patches' in kwargs raises."""
    with pytest.raises(ValueError, match="Missing 'patches'"):
        _parse_patches_from_kwargs({"wrong_key": "value"})


def test_kwargs_multi_hunk_patch(tmp_path):
    """Test that kwargs patches support multiple hunks for a single file."""
    f = tmp_path / "module.py"
    f.write_text(
        """
def old_func():
    return 1

def old_name():
    return 2
""".strip()
    )

    patch = """
<<<<<<< ORIGINAL
def old_func():
    return 1
=======
def new_func():
    return 42
>>>>>>> UPDATED

<<<<<<< ORIGINAL
def old_name():
    return 2
=======
def new_name():
    return 3
>>>>>>> UPDATED
""".strip()

    list(execute_patch_many_impl([(f, patch)]))
    content = f.read_text()
    assert "new_func" in content
    assert "new_name" in content
    assert "old_func" not in content
    assert "old_name" not in content


def test_kwargs_rejects_path_traversal(tmp_path, monkeypatch):
    """Test that relative paths cannot escape the current working directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    with pytest.raises(ValueError, match="Path traversal detected"):
        _parse_patches_from_kwargs(
            {
                "patches": [
                    {
                        "path": "../escape.txt",
                        "patch": "<<<<<<< ORIGINAL\nx\n=======\ny\n>>>>>>> UPDATED",
                    }
                ]
            }
        )


def test_patch_many_tool_is_discoverable():
    """Test that patch_many can be loaded through tool discovery."""
    clear_tools()
    init_tools(allowlist=["patch_many"])

    tool = get_tool("patch_many")
    assert tool is not None
    assert tool.name == "patch_many"
