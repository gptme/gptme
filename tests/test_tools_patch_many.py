"""Tests for patch_many: atomic multi-file patch tool."""

import json
import os
from pathlib import Path
from typing import cast

import pytest

import gptme.tools.patch_many as patch_many_tool
from gptme.message import Message
from gptme.tools import clear_tools, get_tool, init_tools
from gptme.tools.patch import DIVIDER, ORIGINAL, UPDATED, Patch
from gptme.tools.patch_many import (
    _CONFIRM_HEADER,
    _parse_patches_from_content,
    _parse_patches_from_kwargs,
    execute_patch_many,
    execute_patch_many_impl,
)


def _patch_text(original: str, updated: str) -> str:
    return f"{ORIGINAL}{original}{DIVIDER}{updated}{UPDATED}".strip()


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


def test_markdown_multi_hunk_error():
    """Test that multi-hunk markdown patches give an actionable error pointing to kwargs."""
    paths = [Path("/tmp/a.txt")]
    with pytest.raises(ValueError, match="kwargs format"):
        _parse_patches_from_content(
            """
<<<<<<< ORIGINAL
first line
=======
first modified
>>>>>>> UPDATED

<<<<<<< ORIGINAL
second line
=======
second modified
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


def test_atomic_preserves_line_endings(tmp_path):
    """Each file keeps its own line endings (CRLF stays CRLF, LF stays LF)."""
    crlf = tmp_path / "crlf.txt"
    lf = tmp_path / "lf.txt"
    crlf.write_bytes(b"a\r\nold\r\nz\r\n")
    lf.write_bytes(b"a\nold\nz\n")

    patches = [
        (crlf, Patch("old\n", "new\n")),
        (lf, Patch("old\n", "new\n")),
    ]

    messages = list(execute_patch_many_impl(patches))
    assert "atomically" in messages[0].content.lower()
    assert crlf.read_bytes() == b"a\r\nnew\r\nz\r\n"
    assert lf.read_bytes() == b"a\nnew\nz\n"


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


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs not supported on Windows")
@pytest.mark.timeout(10)
def test_atomic_refuses_fifo(tmp_path):
    """A FIFO target aborts the batch instead of blocking forever on read."""
    f1 = tmp_path / "exists.py"
    fifo = tmp_path / "pipe.py"
    f1.write_text("original")
    os.mkfifo(fifo)

    patches = [
        (f1, Patch("original", "modified")),
        (fifo, Patch("original", "modified")),
    ]

    messages = list(execute_patch_many_impl(patches))
    assert "non-regular file" in messages[0].content
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


def test_markdown_multihunk_path_header_format(tmp_path):
    """Test multi-hunk markdown using === PATH: ... === headers (no paths in fence)."""
    f = tmp_path / "module.py"
    f.write_text("def old_func():\n    return 1\n\ndef also_old():\n    return 2\n")

    code = (
        f"{_CONFIRM_HEADER}{f} ===\n"
        + _patch_text("def old_func():\n    return 1", "def new_func():\n    return 42")
        + "\n\n"
        + _patch_text("def also_old():\n    return 2", "def also_new():\n    return 3")
    )

    messages = list(execute_patch_many(code, None, None))
    assert messages
    assert "atomically" in messages[0].content.lower()
    content = f.read_text()
    assert "new_func" in content
    assert "also_new" in content
    assert "old_func" not in content
    assert "also_old" not in content


def test_simple_format_placeholder_patch_round_trip(tmp_path):
    """Simple markdown format should accept placeholder-based multi-edit patches."""
    f = tmp_path / "module.py"
    f.write_text(
        """
def hello():
    print("hello")

if __name__ == "__main__":
    hello()
""".strip()
    )

    code = _patch_text(
        """
def hello():
    print("hello")
    # ...
if __name__ == "__main__":
    hello()
""".strip(),
        """
def hello_world():
    print("hello")
    # ...
if __name__ == "__main__":
    hello_world()
""".strip(),
    )

    messages = list(execute_patch_many(code, [str(f)], None))
    assert messages
    assert "atomically" in messages[0].content.lower()
    content = f.read_text()
    assert "def hello_world()" in content
    assert "hello_world()" in content
    assert "def hello():" not in content
    assert "    hello()" not in content


def test_execute_patch_many_markdown_round_trip(tmp_path):
    """Test the markdown entrypoint, including confirmation-hook execution."""
    f1 = tmp_path / "file1.py"
    f2 = tmp_path / "file2.py"
    f1.write_text("original a")
    f2.write_text("original b")

    code = "\n\n".join(
        [
            _patch_text("original a", "modified a"),
            _patch_text("original b", "modified b"),
        ]
    )

    messages = list(execute_patch_many(code, [str(f1), str(f2)], None))
    assert messages
    assert "atomically" in messages[0].content.lower()
    assert f1.read_text() == "modified a"
    assert f2.read_text() == "modified b"


def test_execute_patch_many_kwargs_round_trip(tmp_path):
    """Test the kwargs entrypoint, including confirmation-hook execution."""
    f1 = tmp_path / "file1.py"
    f2 = tmp_path / "file2.py"
    f1.write_text("original a")
    f2.write_text("original b")

    kwargs = {
        "patches": json.dumps(
            [
                {
                    "path": str(f1),
                    "patch": _patch_text("original a", "modified a"),
                },
                {
                    "path": str(f2),
                    "patch": _patch_text("original b", "modified b"),
                },
            ]
        )
    }

    messages = list(execute_patch_many(None, None, kwargs))
    assert messages
    assert "atomically" in messages[0].content.lower()
    assert f1.read_text() == "modified a"
    assert f2.read_text() == "modified b"


def test_execute_patch_many_markdown_uses_confirmation(tmp_path, monkeypatch):
    """Test that markdown patch_many routes through execute_with_confirmation."""
    f = tmp_path / "file.py"
    f.write_text("original")
    captured: dict[str, object] = {}

    def fake_execute_with_confirmation(code, args, tool_kwargs, **helper_kwargs):
        captured["code"] = code
        captured["args"] = args
        captured["tool_kwargs"] = tool_kwargs
        captured["helper_kwargs"] = helper_kwargs
        yield Message("system", "stub")

    monkeypatch.setattr(
        patch_many_tool, "execute_with_confirmation", fake_execute_with_confirmation
    )

    messages = list(
        patch_many_tool.execute_patch_many(
            _patch_text("original", "modified"),
            [str(f)],
            None,
        )
    )

    assert [message.content for message in messages] == ["stub"]
    captured_code = cast(str, captured["code"])
    assert str(f) in captured_code
    assert "ORIGINAL" in captured_code


def test_execute_patch_many_kwargs_uses_confirmation(tmp_path, monkeypatch):
    """Test that kwargs patch_many routes through execute_with_confirmation."""
    f = tmp_path / "file.py"
    f.write_text("original")
    captured: dict[str, object] = {}

    def fake_execute_with_confirmation(code, args, tool_kwargs, **helper_kwargs):
        captured["code"] = code
        captured["args"] = args
        captured["tool_kwargs"] = tool_kwargs
        captured["helper_kwargs"] = helper_kwargs
        yield Message("system", "stub")

    monkeypatch.setattr(
        patch_many_tool, "execute_with_confirmation", fake_execute_with_confirmation
    )

    messages = list(
        patch_many_tool.execute_patch_many(
            None,
            None,
            {
                "patches": json.dumps(
                    [
                        {
                            "path": str(f),
                            "patch": _patch_text("original", "modified"),
                        }
                    ]
                )
            },
        )
    )

    assert [message.content for message in messages] == ["stub"]
    captured_code = cast(str, captured["code"])
    assert str(f) in captured_code
    assert "ORIGINAL" in captured_code


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


def test_atomic_write_failure_rollback(tmp_path, monkeypatch):
    """Test that an OSError during write rolls back already-written files."""
    f1 = tmp_path / "file1.py"
    f2 = tmp_path / "file2.py"
    f1.write_text("original a")
    f2.write_text("original b")

    patches = [
        (f1, Patch("original a", "modified a")),
        (f2, Patch("original b", "modified b")),
    ]

    # Fail at the publication boundary, not the now-staged text writer.
    original_replace = os.replace
    failed = False

    def failing_replace(source, destination):
        nonlocal failed
        if destination == f2 and not failed:
            failed = True
            raise OSError("disk full")
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", failing_replace)

    messages = list(execute_patch_many_impl(patches))
    assert messages
    assert "write error" in messages[0].content.lower()
    assert "rolled back" in messages[0].content.lower()
    # Both files should have their original content
    assert f1.read_text() == "original a"
    assert f2.read_text() == "original b"


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, RuntimeError])
@pytest.mark.parametrize("after_replace", [False, True])
def test_publication_exception_restores_originals(
    tmp_path, monkeypatch, error_type, after_replace
):
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.write_bytes(b"alpha\r\n")
    second.write_bytes(b"beta\r\n")
    original_replace = os.replace
    error = error_type("publication interrupted")
    failed = False

    def interrupted_replace(source, destination):
        nonlocal failed
        if destination == second and not failed:
            failed = True
            if after_replace:
                original_replace(source, destination)
            raise error
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", interrupted_replace)
    with pytest.raises(error_type) as caught:
        list(
            execute_patch_many_impl(
                [(first, Patch("alpha", "ALPHA")), (second, Patch("beta", "BETA"))]
            )
        )
    assert caught.value is error
    assert first.read_bytes() == b"alpha\r\n"
    assert second.read_bytes() == b"beta\r\n"
    assert not list(tmp_path.glob(".patch-many-*"))


@pytest.mark.parametrize("restore_error_type", [OSError, KeyboardInterrupt])
def test_interrupted_rollback_retains_original_and_reports_path(
    tmp_path, monkeypatch, restore_error_type
):
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.write_bytes(b"alpha\n")
    second.write_bytes(b"beta\n")
    original_replace = os.replace
    error = KeyboardInterrupt("publication interrupted")
    failed = False
    retained = []

    def interrupted_replace(source, destination):
        nonlocal failed
        if destination == second and not failed:
            failed = True
            raise error
        if failed and destination == first:
            retained.append(Path(source))
            raise restore_error_type("restoration interrupted")
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", interrupted_replace)
    with pytest.raises(KeyboardInterrupt) as caught:
        list(
            execute_patch_many_impl(
                [(first, Patch("alpha", "ALPHA")), (second, Patch("beta", "BETA"))]
            )
        )
    assert caught.value is error
    assert first.read_bytes() == b"ALPHA\n"
    assert second.read_bytes() == b"beta\n"
    assert len(retained) == 1
    assert retained[0].read_bytes() == b"alpha\n"
    notes = "\n".join(caught.value.__notes__)
    assert "Restoration incomplete" in notes
    assert str(retained[0]) in notes
    assert set(tmp_path.glob(".patch-many-*")) == set(retained)


def test_simple_format_not_misrouted_when_content_contains_path_header(tmp_path):
    """Simple-format patch whose content contains '=== PATH: ' must not be misrouted."""
    f = tmp_path / "Makefile"
    # Content that contains the literal string that used to trigger misrouting
    f.write_text("# old line\n=== PATH: some/file ===\nmore content\n")

    code = _patch_text(
        "# old line",
        "# new line",
    )
    messages = list(execute_patch_many(code, [str(f)], None))
    assert messages
    assert "error" not in messages[0].content.lower(), messages[0].content
    assert "# new line" in f.read_text()


def test_patch_many_tool_is_discoverable():
    """Test that patch_many can be loaded through tool discovery."""
    clear_tools()
    init_tools(allowlist=["patch_many"])

    tool = get_tool("patch_many")
    assert tool is not None
    assert tool.name == "patch_many"


def test_duplicate_paths_accumulate_both_hunks(tmp_path):
    """Two entries for one path must both land, not clobber each other."""
    f = tmp_path / "file.py"
    f.write_text("a = 1\nb = 2\nc = 3\n")

    patches = [
        (f, Patch("a = 1", "a = 10")),
        (f, Patch("b = 2", "b = 20")),
        (f, Patch("c = 3", "c = 30")),
    ]

    messages = list(execute_patch_many_impl(patches))
    assert messages
    assert "atomically" in messages[0].content.lower()
    assert "aborted" not in messages[0].content.lower()
    assert f.read_text() == "a = 10\nb = 20\nc = 30\n"


def test_duplicate_paths_message_counts_surviving_patches(tmp_path):
    """The success message must not claim more patches than were applied."""
    f = tmp_path / "file.py"
    f.write_text("a = 1\nb = 2\n")

    patches = [
        (f, Patch("a = 1", "a = 10")),
        (f, Patch("b = 2", "b = 20")),
    ]

    messages = list(execute_patch_many_impl(patches))
    assert messages
    # 2 hunks applied, but the file is listed once
    assert "2 hunk(s)" in messages[0].content
    assert "1 file(s)" in messages[0].content
    assert messages[0].content.count(str(f)) == 1


def test_duplicate_paths_atomic_when_later_hunk_fails(tmp_path):
    """If a later hunk for a path fails, nothing is written (atomicity holds)."""
    f = tmp_path / "file.py"
    g = tmp_path / "other.py"
    f.write_text("a = 1\n")
    g.write_text("keep me\n")

    patches = [
        (f, Patch("a = 1", "a = 10")),
        (g, Patch("keep me", "changed")),
        (f, Patch("does not exist", "boom")),
    ]

    messages = list(execute_patch_many_impl(patches))
    assert messages
    assert "aborted" in messages[0].content.lower()
    assert "patch failed" in messages[0].content.lower()
    assert f.read_text() == "a = 1\n"
    assert g.read_text() == "keep me\n"


def test_duplicate_paths_via_kwargs_round_trip(tmp_path):
    """The kwargs entrypoint accumulates repeated paths too."""
    f = tmp_path / "file.py"
    f.write_text("first\nsecond\n")

    kwargs = {
        "patches": json.dumps(
            [
                {"path": str(f), "patch": _patch_text("first", "FIRST")},
                {"path": str(f), "patch": _patch_text("second", "SECOND")},
            ]
        )
    }

    messages = list(execute_patch_many(None, None, kwargs))
    assert messages
    assert "atomically" in messages[0].content.lower()
    assert f.read_text() == "FIRST\nSECOND\n"


def test_multi_block_entry_counts_each_hunk(tmp_path):
    """One entry with two ORIGINAL/UPDATED blocks reports 2 hunks, not 1."""
    f = tmp_path / "f.txt"
    f.write_text("alpha\nbeta\n")
    patches = [
        (
            f,
            (
                "<<<<<<< ORIGINAL\nalpha\n=======\nALPHA\n>>>>>>> UPDATED\n"
                "<<<<<<< ORIGINAL\nbeta\n=======\nBETA\n>>>>>>> UPDATED\n"
            ),
        )
    ]

    messages = list(execute_patch_many_impl(patches))
    assert f.read_text() == "ALPHA\nBETA\n"
    assert "2 hunk(s)" in messages[0].content
    assert "1 file(s)" in messages[0].content


def test_hardlink_aliases_refused_before_any_write(tmp_path):
    first = tmp_path / "first.txt"
    alias = tmp_path / "alias.txt"
    first.write_text("alpha\nbeta\n")
    os.link(first, alias)
    messages = list(
        execute_patch_many_impl(
            [
                (first, Patch("alpha", "ALPHA")),
                (alias, Patch("beta", "BETA")),
            ]
        )
    )
    assert first.read_text() == alias.read_text() == "alpha\nbeta\n"
    assert "alias" in messages[0].content.lower()
    assert "aborted" in messages[0].content.lower()


def test_partial_failing_target_is_restored(tmp_path, monkeypatch):
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.write_text("alpha\nbeta\n")
    second.write_text("alpha\nbeta\n")
    original_write = Path.write_text
    failed = False

    def partial_write(path, text, *args, **kwargs):
        nonlocal failed
        if path == second and not failed:
            failed = True
            with path.open("w") as stream:
                stream.write(text[:2])
            raise OSError("partial target write")
        return original_write(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", partial_write)
    messages = list(
        execute_patch_many_impl(
            [
                (first, Patch("alpha", "ALPHA")),
                (second, Patch("beta", "BETA")),
            ]
        )
    )
    if failed:
        assert first.read_text() == second.read_text() == "alpha\nbeta\n"
        assert "failed" in messages[0].content.lower()
    else:
        # Staging avoids the destructive target-write boundary altogether.
        assert first.read_text() == "ALPHA\nbeta\n"
        assert second.read_text() == "alpha\nBETA\n"


@pytest.mark.parametrize("mutate_first", [False, True])
def test_publication_failure_restores_all_targets(tmp_path, monkeypatch, mutate_first):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"alpha\r\nbeta\r\n")
    second.write_bytes(b"alpha\r\nbeta\r\n")
    first.chmod(0o751)
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in (first, second)}
    replace = os.replace
    failed = False

    def fail_publish(source, destination):
        nonlocal failed
        if destination == second and not failed:
            failed = True
            if mutate_first:
                destination.write_bytes(b"partial")
            raise OSError("injected publication failure")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_publish)
    messages = list(
        execute_patch_many_impl(
            [
                (first, Patch("alpha", "ALPHA")),
                (second, Patch("beta", "BETA")),
            ]
        )
    )
    assert failed
    assert "Original contents restored" in messages[0].content
    assert {p: (p.read_bytes(), p.stat().st_mode) for p in before} == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["first", "second"]


def test_restore_failure_retains_original_and_reports_target(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("alpha\nbeta\n")
    second.write_text("alpha\nbeta\n")
    replace = os.replace
    publication_failed = False

    def fail_publish_and_restore(source, destination):
        nonlocal publication_failed
        if destination == second and not publication_failed:
            publication_failed = True
            raise OSError("publish failed")
        if publication_failed and destination == first:
            raise OSError("restore failed")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_publish_and_restore)
    messages = list(
        execute_patch_many_impl(
            [
                (first, Patch("alpha", "ALPHA")),
                (second, Patch("beta", "BETA")),
            ]
        )
    )
    text = messages[0].content
    assert "Restoration incomplete" in text
    assert "No files were modified" not in text
    assert str(first) in text
    retained = [p for p in tmp_path.iterdir() if p not in (first, second)]
    assert len(retained) == 1
    assert retained[0].read_bytes() == b"alpha\nbeta\n"
    assert str(retained[0]) in text
    assert first.read_text() == "ALPHA\nbeta\n"
    assert second.read_text() == "alpha\nbeta\n"


def test_staging_failure_never_publishes(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("alpha")
    second.write_text("beta")
    stage = patch_many_tool._stage_patch_content
    calls = 0

    def fail_stage(path, content, mode):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("staging full")
        return stage(path, content, mode)

    monkeypatch.setattr(patch_many_tool, "_stage_patch_content", fail_stage)
    messages = list(
        execute_patch_many_impl(
            [
                (first, Patch("alpha", "ALPHA")),
                (second, Patch("beta", "BETA")),
            ]
        )
    )
    assert "No files were written" in messages[0].content
    assert first.read_text() == "alpha"
    assert second.read_text() == "beta"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["first", "second"]


def test_symlink_alias_accumulates_and_keeps_link(tmp_path):
    target, alias = tmp_path / "target", tmp_path / "alias"
    target.write_text("alpha\nbeta\n")
    alias.symlink_to(target)
    list(
        execute_patch_many_impl(
            [
                (target, Patch("alpha", "ALPHA")),
                (alias, Patch("beta", "BETA")),
            ]
        )
    )
    assert alias.is_symlink()
    assert target.read_text() == alias.read_text() == "ALPHA\nBETA\n"


def test_staged_success_preserves_permissions_and_line_endings(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"alpha\r\nbeta\r\n")
    target.chmod(0o751)
    list(execute_patch_many_impl([(target, Patch("alpha", "ALPHA"))]))
    assert target.read_bytes() == b"ALPHA\r\nbeta\r\n"
    assert target.stat().st_mode & 0o777 == 0o751
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("mode", [0o444, 0o400])
def test_read_only_target_refused_before_any_publication(tmp_path, mode):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"alpha\r\n")
    second.write_bytes(b"beta\r\n")
    second.chmod(mode)
    try:
        # Privileged callers may legitimately write despite these mode bits.
        try:
            with second.open("r+b"):
                pass
        except PermissionError:
            pass
        else:
            pytest.skip("current credentials can write the read-only target")

        before = {p: (p.read_bytes(), p.stat()) for p in (first, second)}
        messages = list(
            execute_patch_many_impl(
                [
                    (first, Patch("alpha", "ALPHA")),
                    (second, Patch("beta", "BETA")),
                ]
            )
        )
        assert "No files were written" in messages[0].content
        assert str(second) in messages[0].content
        for path, (content, info) in before.items():
            assert path.read_bytes() == content
            assert path.stat().st_ino == info.st_ino
            assert path.stat().st_mode == info.st_mode
        assert sorted(p.name for p in tmp_path.iterdir()) == ["first", "second"]
    finally:
        second.chmod(0o600)


def test_long_target_basename_can_be_patched(tmp_path):
    target = tmp_path / ("x" * 255)
    target.write_text("alpha")
    messages = list(execute_patch_many_impl([(target, Patch("alpha", "ALPHA"))]))
    assert target.read_text() == "ALPHA", messages[0].content


def test_cleanup_failure_does_not_hide_restore_failure(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("alpha")
    second.write_text("beta")
    replace, unlink = os.replace, Path.unlink
    failed = False
    cleanup_attempts = []

    def fail_replace(source, destination):
        nonlocal failed
        if destination == second and not failed:
            failed = True
            raise OSError("publication failed")
        if failed and destination == first:
            raise OSError("restoration failed")
        return replace(source, destination)

    def fail_cleanup(path, *args, **kwargs):
        cleanup_attempts.append(path)
        if path.exists():
            raise PermissionError("cleanup denied")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    messages = list(
        execute_patch_many_impl(
            [
                (first, Patch("alpha", "ALPHA")),
                (second, Patch("beta", "BETA")),
            ]
        )
    )
    assert "Restoration incomplete" in messages[0].content
    assert "original retained at" in messages[0].content
    assert "cleanup" in messages[0].content.lower()
    assert len(cleanup_attempts) == 3
