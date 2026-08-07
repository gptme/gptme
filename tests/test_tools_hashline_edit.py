"""Tests for the hashline_edit tool and its snapshot store integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from gptme.tools._hashline_snapshot import (
    clear as clear_snapshots,
)
from gptme.tools._hashline_snapshot import (
    compute_tag,
    get_stored_tag,
    lookup_snapshot,
    store_snapshot,
)
from gptme.tools.hashline_edit import (
    HashlineOp,
    ParseError,
    _apply_operations,
    _parse_operations,
    execute_hashline_edit,
    tool,
)
from gptme.tools.read import execute_read

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msgs(gen) -> list[str]:
    return [m.content for m in gen]


@pytest.fixture(autouse=True)
def _reset_snapshots():
    """Clear the module-level snapshot store before each test."""
    clear_snapshots()
    yield
    clear_snapshots()


# ---------------------------------------------------------------------------
# Snapshot store
# ---------------------------------------------------------------------------


class TestSnapshotStore:
    def test_compute_tag_is_8_hex_chars(self):
        tag = compute_tag("hello\n")
        assert len(tag) == 8
        assert all(c in "0123456789ABCDEF" for c in tag)

    def test_same_content_same_tag(self):
        assert compute_tag("abc") == compute_tag("abc")

    def test_different_content_different_tag(self):
        assert compute_tag("abc") != compute_tag("def")

    def test_store_and_lookup_success(self, tmp_path: Path):
        path = str(tmp_path / "f.txt")
        tag = store_snapshot(path, "hello\n")
        matched, content = lookup_snapshot(path, tag)
        assert matched
        assert content == "hello\n"

    def test_lookup_wrong_tag(self, tmp_path: Path):
        path = str(tmp_path / "f.txt")
        store_snapshot(path, "hello\n")
        matched, content = lookup_snapshot(path, "DEADBEEF")
        assert not matched
        assert content is None

    def test_lookup_unknown_path(self, tmp_path: Path):
        matched, content = lookup_snapshot(str(tmp_path / "nope.txt"), "00000000")
        assert not matched
        assert content is None

    def test_get_stored_tag(self, tmp_path: Path):
        path = str(tmp_path / "f.txt")
        tag = store_snapshot(path, "hello\n")
        assert get_stored_tag(path) == tag

    def test_get_stored_tag_unknown(self, tmp_path: Path):
        assert get_stored_tag(str(tmp_path / "ghost.txt")) is None

    def test_overwrite_snapshot(self, tmp_path: Path):
        path = str(tmp_path / "f.txt")
        store_snapshot(path, "v1\n")
        new_tag = store_snapshot(path, "v2\n")
        matched, content = lookup_snapshot(path, new_tag)
        assert matched
        assert content == "v2\n"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParser:
    def _make_block(self, path: str, content: str) -> str:
        tag = compute_tag("any")  # we just need a valid 8-hex tag
        return f"[{path}#{tag}]\n{content}"

    def _header(self, path: str = "f.py", content: str = "") -> str:
        tag = store_snapshot(path, content)
        return f"[{path}#{tag}]"

    def test_replace_range(self):
        block = "[f.py#A1B2C3D4]\nPUT 1.=3:\n+new line\n"
        path, tag, ops = _parse_operations(block)
        assert path == "f.py"
        assert tag == "A1B2C3D4"
        assert len(ops) == 1
        assert ops[0].kind == "replace"
        assert ops[0].start == 1
        assert ops[0].end == 3
        assert ops[0].text == "new line"

    def test_insert_before(self):
        block = "[f.py#A1B2C3D4]\nPUT <5:\n+inserted\n"
        _, _, ops = _parse_operations(block)
        assert ops[0].kind == "insert_before"
        assert ops[0].start == 5

    def test_insert_after(self):
        block = "[f.py#A1B2C3D4]\nPUT >2:\n+after\n"
        _, _, ops = _parse_operations(block)
        assert ops[0].kind == "insert_after"
        assert ops[0].start == 2

    def test_cut_range(self):
        block = "[f.py#A1B2C3D4]\nCUT 3.=5\n"
        _, _, ops = _parse_operations(block)
        assert ops[0].kind == "delete"
        assert ops[0].start == 3
        assert ops[0].end == 5
        assert ops[0].text is None

    def test_multiple_ops(self):
        block = "[f.py#A1B2C3D4]\nPUT 1.=1:\n+hello\n\nCUT 3.=3\n"
        _, _, ops = _parse_operations(block)
        assert len(ops) == 2
        assert ops[0].kind == "replace"
        assert ops[1].kind == "delete"

    def test_multiline_content(self):
        block = "[f.py#A1B2C3D4]\nPUT 1.=2:\n+line a\n+line b\n+line c\n"
        _, _, ops = _parse_operations(block)
        assert ops[0].text == "line a\nline b\nline c"

    def test_empty_block_raises(self):
        with pytest.raises(ParseError):
            _parse_operations("")

    def test_missing_header_raises(self):
        with pytest.raises(ParseError, match="snapshot header"):
            _parse_operations("PUT 1.=2:\n+x\n")

    def test_bad_put_range_start_gt_end_raises(self):
        with pytest.raises(ParseError, match="start.*end"):
            _parse_operations("[f.py#A1B2C3D4]\nPUT 5.=2:\n+x\n")

    def test_bad_cut_range_raises(self):
        with pytest.raises(ParseError, match="start.*end"):
            _parse_operations("[f.py#A1B2C3D4]\nCUT 5.=2\n")

    def test_unexpected_line_raises(self):
        with pytest.raises(ParseError, match="Unrecognized"):
            _parse_operations("[f.py#A1B2C3D4]\nGET 1:\n+x\n")

    def test_no_ops_returns_empty_list(self):
        block = "[f.py#A1B2C3D4]\n"
        _, _, ops = _parse_operations(block)
        assert ops == []

    def test_lowercase_tag_normalized(self):
        block = "[f.py#a1b2c3d4]\nCUT 1.=1\n"
        _, tag, _ = _parse_operations(block)
        assert tag == "A1B2C3D4"


# ---------------------------------------------------------------------------
# Apply engine
# ---------------------------------------------------------------------------


class TestApplyOperations:
    def test_replace_single_line(self):
        content = "alpha\nbeta\ngamma\n"
        ops = [HashlineOp(kind="replace", start=2, end=2, text="BETA")]
        assert _apply_operations(content, ops) == "alpha\nBETA\ngamma\n"

    def test_replace_range(self):
        content = "a\nb\nc\nd\n"
        ops = [HashlineOp(kind="replace", start=2, end=3, text="X\nY")]
        assert _apply_operations(content, ops) == "a\nX\nY\nd\n"

    def test_insert_before(self):
        content = "alpha\nbeta\n"
        ops = [HashlineOp(kind="insert_before", start=1, end=1, text="zero")]
        assert _apply_operations(content, ops) == "zero\nalpha\nbeta\n"

    def test_insert_after(self):
        content = "alpha\nbeta\n"
        ops = [HashlineOp(kind="insert_after", start=1, end=1, text="one-half")]
        assert _apply_operations(content, ops) == "alpha\none-half\nbeta\n"

    def test_delete(self):
        content = "alpha\nbeta\ngamma\n"
        ops = [HashlineOp(kind="delete", start=2, end=2, text=None)]
        assert _apply_operations(content, ops) == "alpha\ngamma\n"

    def test_delete_range(self):
        content = "a\nb\nc\nd\n"
        ops = [HashlineOp(kind="delete", start=2, end=3, text=None)]
        assert _apply_operations(content, ops) == "a\nd\n"

    def test_preserves_trailing_newline(self):
        content = "alpha\nbeta\n"
        ops = [HashlineOp(kind="replace", start=1, end=1, text="ALPHA")]
        result = _apply_operations(content, ops)
        assert result.endswith("\n")

    def test_no_trailing_newline_not_added(self):
        content = "alpha\nbeta"
        ops = [HashlineOp(kind="replace", start=1, end=1, text="ALPHA")]
        result = _apply_operations(content, ops)
        assert not result.endswith("\n")

    def test_multiple_ops_applied_bottom_up(self):
        # Both ops reference the ORIGINAL line numbers
        content = "a\nb\nc\nd\n"
        ops = [
            HashlineOp(kind="replace", start=1, end=1, text="A"),
            HashlineOp(kind="delete", start=3, end=3, text=None),
        ]
        assert _apply_operations(content, ops) == "A\nb\nd\n"

    def test_out_of_range_raises(self):
        content = "a\nb\n"
        with pytest.raises(ValueError, match="out of range"):
            _apply_operations(
                content, [HashlineOp(kind="delete", start=5, end=5, text=None)]
            )

    def test_empty_ops_returns_original(self):
        content = "alpha\nbeta\n"
        assert _apply_operations(content, []) == content


# ---------------------------------------------------------------------------
# execute_hashline_edit integration
# ---------------------------------------------------------------------------


class TestExecuteHashlineEdit:
    def test_basic_replace(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("alpha\nbeta\ngamma\n")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nPUT 2.=2:\n+BETA\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "applied" in msgs[0].lower()
        assert f.read_text() == "alpha\nBETA\ngamma\n"

    def test_tag_mismatch_rejected(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("alpha\nbeta\n")
        store_snapshot(str(f.resolve()), f.read_text())
        # Use a wrong tag
        block = f"[{f.resolve()}#DEADBEEF]\nCUT 1.=1\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "mismatch" in msgs[0].lower() or "snapshot" in msgs[0].lower()
        assert f.read_text() == "alpha\nbeta\n"

    def test_no_snapshot_rejected(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("alpha\n")
        # Don't store any snapshot — simulate no prior read
        block = f"[{f.resolve()}#A1B2C3D4]\nCUT 1.=1\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "no snapshot" in msgs[0].lower()
        assert f.read_text() == "alpha\n"

    def test_insert_before(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("alpha\nbeta\n")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nPUT <1:\n+zero\n"
        _msgs(execute_hashline_edit(block, [str(f)], None))
        assert f.read_text() == "zero\nalpha\nbeta\n"

    def test_insert_after(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("alpha\nbeta\n")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nPUT >1:\n+one-half\n"
        _msgs(execute_hashline_edit(block, [str(f)], None))
        assert f.read_text() == "alpha\none-half\nbeta\n"

    def test_cut_delete(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("alpha\nbeta\ngamma\n")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nCUT 2.=2\n"
        _msgs(execute_hashline_edit(block, [str(f)], None))
        assert f.read_text() == "alpha\ngamma\n"

    def test_multiple_ops(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("a\nb\nc\nd\n")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+A\n\nCUT 3.=3\n"
        _msgs(execute_hashline_edit(block, [str(f)], None))
        assert f.read_text() == "A\nb\nd\n"

    def test_snapshot_updated_after_edit(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("alpha\nbeta\n")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+ALPHA\n"
        _msgs(execute_hashline_edit(block, [str(f)], None))
        # After edit, snapshot should reflect new content
        new_tag = get_stored_tag(str(f.resolve()))
        expected_tag = compute_tag(f.read_text())
        assert new_tag == expected_tag

    def test_no_path_error(self):
        msgs = _msgs(execute_hashline_edit("[f.py#A1B2C3D4]\nCUT 1.=1\n", None, None))
        assert "no path" in msgs[0].lower()

    def test_empty_block_error(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("hello\n")
        msgs = _msgs(execute_hashline_edit("", [str(f)], None))
        assert "empty" in msgs[0].lower()

    def test_nonexistent_file(self, tmp_path: Path):
        f = tmp_path / "nope.txt"
        msgs = _msgs(
            execute_hashline_edit("[nope.txt#A1B2C3D4]\nCUT 1.=1\n", [str(f)], None)
        )
        assert "not found" in msgs[0].lower()

    def test_no_ops_error(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("hello\n")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "no operations" in msgs[0].lower()

    def test_kwargs_path(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("hello\n")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+world\n"
        msgs = _msgs(execute_hashline_edit(block, None, {"path": str(f)}))
        assert "applied" in msgs[0].lower()
        assert f.read_text() == "world\n"

    def test_insert_into_empty_file(self, tmp_path: Path):
        """PUT <1: should work on an empty file (P2 fix: empty-file insertion)."""
        f = tmp_path / "empty.txt"
        f.write_text("")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nPUT <1:\n+first line\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "applied" in msgs[0].lower(), msgs
        assert f.read_text() == "first line"

    def test_confirmation_edit_uses_edited_content(self, tmp_path: Path):
        """When confirmation hook returns EDIT, the edited content is written (P1 fix)."""
        from unittest.mock import patch

        from gptme.hooks.confirm import ConfirmationResult

        f = tmp_path / "f.txt"
        f.write_text("alpha\nbeta\n")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+ALPHA\n"
        edited = "EDITED BY HOOK\nbeta\n"
        with patch(
            "gptme.hooks.get_confirmation",
            return_value=ConfirmationResult.edit(edited),
        ):
            msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "applied" in msgs[0].lower(), msgs
        assert f.read_text() == edited

    def test_live_file_changed_after_snapshot(self, tmp_path: Path):
        """Edit must be rejected when the live file has changed since read (P1 fix)."""
        f = tmp_path / "f.txt"
        original = "alpha\nbeta\n"
        f.write_text(original)
        tag = store_snapshot(str(f.resolve()), original)
        # Externally mutate the file after the snapshot was captured
        f.write_text("alpha\nbeta\nextra-line\n")
        # The stored tag still matches the provided tag — but the live file differs
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+ALPHA\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "changed since snapshot" in msgs[0].lower(), msgs
        # File must be untouched
        assert f.read_text() == "alpha\nbeta\nextra-line\n"

    def test_content_mismatch_rejected_even_with_matching_tag(self, tmp_path: Path):
        """Full content comparison catches stale content even when truncated tags match.

        Injects the snapshot store directly to simulate a 4-byte SHA-256 prefix
        collision: the stored tag matches the edit-block tag, but the live file
        content differs from the captured snapshot content. The old tag-only check
        would silently accept this; the content comparison correctly rejects it.
        """
        from gptme.tools._hashline_snapshot import _store, compute_tag

        f = tmp_path / "f.txt"
        original = "alpha\nbeta\n"
        tag = compute_tag(original)
        # Inject the store so tag maps to original content
        _store[str(f.resolve())] = (tag, original)
        # Write DIFFERENT content to disk (simulates the file changing after read,
        # or — in the collision scenario — different content sharing the same tag prefix)
        changed = "alpha\nbeta\ngamma\n"
        f.write_text(changed)
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+ALPHA\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        # Content comparison catches this; tag-only comparison could miss a collision
        assert "changed since snapshot" in msgs[0].lower(), msgs
        assert f.read_text() == changed  # file must be untouched


# ---------------------------------------------------------------------------
# Read tool integration — snapshot populated by read
# ---------------------------------------------------------------------------


class TestReadIntegration:
    def test_read_stores_snapshot(self, tmp_path: Path):
        f = tmp_path / "sample.py"
        f.write_text("def hello():\n    pass\n")
        list(execute_read(None, [str(f)], None))
        tag = get_stored_tag(str(f.resolve()))
        assert tag is not None
        assert len(tag) == 8

    def test_read_tag_in_output(self, tmp_path: Path):
        f = tmp_path / "sample.py"
        content = "def hello():\n    pass\n"
        f.write_text(content)
        msgs = list(execute_read(None, [str(f)], None))
        assert len(msgs) == 1
        expected_tag = compute_tag(content)
        assert f"#{expected_tag}]" in msgs[0].content

    def test_read_then_edit_roundtrip(self, tmp_path: Path):
        f = tmp_path / "greet.py"
        f.write_text("def greet(name):\n    msg = 'Hello, ' + name\n    print(msg)\n")
        # Read to capture snapshot
        list(execute_read(None, [str(f)], None))
        # Extract tag from read output
        tag = get_stored_tag(str(f.resolve()))
        assert tag is not None
        # Apply edit using the tag
        block = f"[{f.resolve()}#{tag}]\nPUT 2.=3:\n+    print(f'Hi, {{name}}')\n"
        edit_msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "applied" in edit_msgs[0].lower()
        result = f.read_text()
        assert "Hi" in result
        assert "msg" not in result

    def test_second_read_updates_snapshot(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("v1\n")
        list(execute_read(None, [str(f)], None))
        tag_v1 = get_stored_tag(str(f.resolve()))

        # Modify file externally
        f.write_text("v2\n")
        list(execute_read(None, [str(f)], None))
        tag_v2 = get_stored_tag(str(f.resolve()))

        assert tag_v1 != tag_v2


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------


class TestToolSpec:
    def test_disabled_by_default(self):
        assert tool.disabled_by_default is True

    def test_block_type(self):
        assert "hashline_edit" in tool.block_types

    def test_has_path_parameter(self):
        assert any(p.name == "path" for p in tool.parameters)

    def test_is_destructive(self):
        assert "destructive" in tool.hints
