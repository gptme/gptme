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
    OperationKind,
    ParseError,
    _apply_operations,
    _parse_operations,
    _resolve_block_end,
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

    def test_same_coordinate_ordinary_inserts_remain_supported(self):
        """Register conflict checks do not reject existing ordinary edits."""
        ops = [
            HashlineOp(kind="insert_before", start=1, end=1, text="first"),
            HashlineOp(kind="insert_before", start=1, end=1, text="second"),
        ]
        assert _apply_operations("", ops) == "second\nfirst"

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
# Register operations (CUT @name / PUT @name)
# ---------------------------------------------------------------------------


class TestRegisterOperations:
    """Tests for CUT @name (capture) and PUT @name (paste) register operations."""

    # ---- Parser tests ----

    def test_parse_cut_with_register(self):
        block = "[f.py#A1B2C3D4]\nCUT 3.=5 @fn\n"
        _, _, ops = _parse_operations(block)
        assert len(ops) == 1
        assert ops[0].kind == "delete"
        assert ops[0].start == 3
        assert ops[0].end == 5
        assert ops[0].register_name == "fn"
        assert ops[0].text is None

    def test_parse_put_before_register(self):
        block = "[f.py#A1B2C3D4]\nPUT <10 @fn\n"
        _, _, ops = _parse_operations(block)
        assert len(ops) == 1
        assert ops[0].kind == "insert_before"
        assert ops[0].start == 10
        assert ops[0].register_name == "fn"
        assert ops[0].text is None

    def test_parse_put_after_register(self):
        block = "[f.py#A1B2C3D4]\nPUT >20 @fn\n"
        _, _, ops = _parse_operations(block)
        assert len(ops) == 1
        assert ops[0].kind == "insert_after"
        assert ops[0].start == 20
        assert ops[0].register_name == "fn"
        assert ops[0].text is None

    def test_parse_put_range_register(self):
        block = "[f.py#A1B2C3D4]\nPUT 5.=7: @fn\n"
        _, _, ops = _parse_operations(block)
        assert len(ops) == 1
        assert ops[0].kind == "replace"
        assert ops[0].start == 5
        assert ops[0].end == 7
        assert ops[0].register_name == "fn"
        assert ops[0].text is None

    # ---- Apply-engine tests ----

    def test_cut_and_paste_after(self):
        """CUT @r captures lines; PUT >N @r pastes them after the destination."""
        content = "a\nb\nc\nd\ne\n"
        ops = [
            HashlineOp(kind="delete", start=2, end=3, register_name="chunk", text=None),
            HashlineOp(
                kind="insert_after", start=5, end=5, register_name="chunk", text=None
            ),
        ]
        # After: lines 2-3 (b,c) deleted; b,c inserted after original line 5 (e)
        result = _apply_operations(content, ops)
        assert result == "a\nd\ne\nb\nc\n"

    def test_cut_and_paste_before(self):
        """CUT @r captures lines; PUT <N @r pastes them before the destination."""
        content = "a\nb\nc\nd\ne\n"
        ops = [
            HashlineOp(kind="delete", start=1, end=1, register_name="x", text=None),
            HashlineOp(
                kind="insert_before", start=4, end=4, register_name="x", text=None
            ),
        ]
        # After: line 1 (a) deleted; a inserted before original line 4 (d)
        result = _apply_operations(content, ops)
        assert result == "b\nc\na\nd\ne\n"

    def test_cut_and_replace_range(self):
        """CUT @r captures lines; PUT N.=M: @r replaces a range with the captured content."""
        content = "old1\nold2\nkeep\nreplace_me\n"
        ops = [
            HashlineOp(kind="delete", start=1, end=2, register_name="src", text=None),
            HashlineOp(kind="replace", start=4, end=4, register_name="src", text=None),
        ]
        result = _apply_operations(content, ops)
        assert result == "keep\nold1\nold2\n"

    @pytest.mark.parametrize(
        ("content", "start", "end", "destination", "expected"),
        [
            # A register containing only one blank line must not become empty.
            ("a\n\nb", 2, 2, 3, "a\nb\n"),
            # A trailing blank in a multi-line capture must remain trailing.
            ("a\nb\n\nc", 1, 3, 4, "c\na\nb\n"),
        ],
    )
    def test_register_preserves_trailing_blank_lines(
        self,
        content: str,
        start: int,
        end: int,
        destination: int,
        expected: str,
    ):
        """A register preserves blank lines at the end of its captured range."""
        ops = [
            HashlineOp(
                kind="delete", start=start, end=end, register_name="x", text=None
            ),
            HashlineOp(
                kind="insert_after",
                start=destination,
                end=destination,
                register_name="x",
                text=None,
            ),
        ]
        assert _apply_operations(content, ops) == expected

    @pytest.mark.parametrize(
        "put",
        [
            HashlineOp(
                kind="insert_before", start=3, end=3, register_name="chunk", text=None
            ),
            HashlineOp(
                kind="insert_after", start=2, end=2, register_name="chunk", text=None
            ),
            HashlineOp(
                kind="replace", start=3, end=4, register_name="chunk", text=None
            ),
        ],
    )
    def test_overlapping_register_put_raises(self, put: HashlineOp):
        """A PUT cannot address coordinates mutated by its register's CUT."""
        content = "a\nb\nc\nd\ne\n"
        ops = [
            HashlineOp(kind="delete", start=2, end=3, register_name="chunk", text=None),
            put,
        ]
        with pytest.raises(ValueError, match="overlap its CUT lines 2-3"):
            _apply_operations(content, ops)

    def test_duplicate_register_capture_raises(self):
        """Each register has one unambiguous source range."""
        content = "a\nb\nc\nd\ne\n"
        ops = [
            HashlineOp(kind="delete", start=1, end=1, register_name="x", text=None),
            HashlineOp(kind="delete", start=4, end=4, register_name="x", text=None),
            HashlineOp(
                kind="insert_after", start=2, end=2, register_name="x", text=None
            ),
        ]
        with pytest.raises(ValueError, match="Register @x is captured more than once"):
            _apply_operations(content, ops)

    @pytest.mark.parametrize(
        "other",
        [
            HashlineOp(kind="replace", start=4, end=4, text="replacement"),
            HashlineOp(kind="delete", start=4, end=4, text=None),
            HashlineOp(kind="insert_before", start=4, end=4, text="before"),
            HashlineOp(kind="insert_after", start=4, end=4, text="after"),
        ],
    )
    def test_register_put_with_equal_start_operation_raises(self, other: HashlineOp):
        """A register PUT cannot share a mutable coordinate with another edit."""
        content = "a\nb\nc\nd\ne\n"
        ops = [
            HashlineOp(kind="delete", start=1, end=1, register_name="x", text=None),
            HashlineOp(
                kind="insert_after", start=4, end=4, register_name="x", text=None
            ),
            other,
        ]
        with pytest.raises(ValueError, match="Multiple operations start at line 4"):
            _apply_operations(content, ops)

    @pytest.mark.parametrize("kind", ["delete", "replace"])
    def test_register_put_inside_other_mutation_range_raises(self, kind: OperationKind):
        """Another range mutation cannot consume a register PUT destination."""
        content = "a\nb\nc\nd\ne\n"
        ops = [
            HashlineOp(kind="delete", start=5, end=5, register_name="x", text=None),
            HashlineOp(
                kind="insert_before", start=3, end=3, register_name="x", text=None
            ),
            HashlineOp(
                kind=kind,
                start=2,
                end=4,
                text=None if kind == "delete" else "replacement",
            ),
        ]
        with pytest.raises(ValueError, match=f"PUT lines 3-3 overlap {kind} lines 2-4"):
            _apply_operations(content, ops)

    @pytest.mark.parametrize(
        "kind", ["delete", "replace", "insert_before", "insert_after"]
    )
    def test_register_put_range_containing_other_mutation_raises(
        self, kind: OperationKind
    ):
        """A register PUT range cannot contain another mutation either."""
        content = "a\nb\nc\nd\ne\nf\n"
        ops = [
            HashlineOp(kind="delete", start=6, end=6, register_name="x", text=None),
            HashlineOp(kind="replace", start=2, end=4, register_name="x", text=None),
            HashlineOp(
                kind=kind,
                start=3,
                end=3,
                text=None if kind == "delete" else "other",
            ),
        ]
        with pytest.raises(ValueError, match=f"PUT lines 2-4 overlap {kind} lines 3-3"):
            _apply_operations(content, ops)

    def test_undefined_register_raises(self):
        """Pasting from an undefined register raises ValueError."""
        content = "a\nb\nc\n"
        ops = [
            HashlineOp(
                kind="insert_after", start=2, end=2, register_name="nope", text=None
            ),
        ]
        with pytest.raises(ValueError, match="@nope"):
            _apply_operations(content, ops)

    def test_register_captures_original_lines(self):
        """Register content is based on original line positions, not post-mutation ones."""
        content = "a\nb\nc\nd\n"
        # CUT line 1, then paste after line 3 (which will shift to line 2 after cut)
        # The result should be: b, c, d, a (a moved to end)
        ops = [
            HashlineOp(kind="delete", start=1, end=1, register_name="first", text=None),
            HashlineOp(
                kind="insert_after", start=4, end=4, register_name="first", text=None
            ),
        ]
        result = _apply_operations(content, ops)
        assert result == "b\nc\nd\na\n"

    def test_cut_without_register_plain_delete(self):
        """CUT without @name still works as a plain delete (no register_name)."""
        content = "a\nb\nc\n"
        ops = [HashlineOp(kind="delete", start=2, end=2, text=None)]
        assert _apply_operations(content, ops) == "a\nc\n"

    # ---- Full execution tests ----

    def test_execute_cut_capture_and_paste(self, tmp_path: Path):
        """End-to-end: CUT @fn to capture function, PUT >N @fn to paste at end."""
        f = tmp_path / "module.txt"
        f.write_text("# header\ndef foo():\n    pass\n# footer\n")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nCUT 2.=3 @fn\nPUT >4 @fn\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "applied" in msgs[0].lower(), msgs
        result = f.read_text()
        # The function should be moved after the footer
        assert result == "# header\n# footer\ndef foo():\n    pass\n"

    def test_execute_paste_before_register(self, tmp_path: Path):
        """End-to-end: CUT @r followed by PUT <N @r (paste before)."""
        f = tmp_path / "f.txt"
        f.write_text("a\nb\nc\nd\n")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nCUT 1.=1 @first\nPUT <4 @first\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "applied" in msgs[0].lower(), msgs
        assert f.read_text() == "b\nc\na\nd\n"

    def test_parse_cut_register_in_mixed_block(self):
        """Register CUT and plain PUT ops can coexist in the same edit block."""
        block = "[f.py#A1B2C3D4]\nCUT 3.=5 @fn\nPUT 1.=1:\n+new header\n\nPUT >10 @fn\n"
        _, _, ops = _parse_operations(block)
        assert len(ops) == 3
        assert ops[0].kind == "delete" and ops[0].register_name == "fn"
        assert ops[1].kind == "replace" and ops[1].register_name is None
        assert ops[2].kind == "insert_after" and ops[2].register_name == "fn"


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
        """Phase 2: clean 3-way merge when file changed externally since read."""
        f = tmp_path / "f.txt"
        original = "alpha\nbeta\n"
        f.write_text(original)
        tag = store_snapshot(str(f.resolve()), original)
        # Externally mutate the file (add a new line that doesn't conflict with the edit)
        f.write_text("alpha\nbeta\nextra-line\n")
        # Edit targets a different line — merge should be clean
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+ALPHA\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        # Phase 2: clean merge succeeds and notes the recovery
        assert "applied" in msgs[0].lower(), msgs
        assert "3-way merge" in msgs[0].lower(), msgs
        # Merged result preserves both the edit and the external change
        assert f.read_text() == "ALPHA\nbeta\nextra-line\n"

    def test_content_mismatch_via_merge_recovery(self, tmp_path: Path):
        """Phase 2: content mismatch triggers merge; non-conflicting result is written.

        Injects the snapshot store directly to simulate a 4-byte SHA-256 prefix
        collision scenario: the stored tag matches the edit-block tag, but the
        live file content differs from the captured snapshot. Phase 2 attempts a
        3-way merge rather than rejecting unconditionally.
        """
        from gptme.tools._hashline_snapshot import _store, compute_tag

        f = tmp_path / "f.txt"
        original = "alpha\nbeta\n"
        tag = compute_tag(original)
        # Inject the store so tag maps to original content
        _store[str(f.resolve())] = (tag, original)
        # Live file has a non-conflicting extra line (different from the edited line)
        changed = "alpha\nbeta\ngamma\n"
        f.write_text(changed)
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+ALPHA\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        # Phase 2 succeeds via merge (edit targets line 1; external change added line 3)
        assert "applied" in msgs[0].lower(), msgs
        assert "3-way merge" in msgs[0].lower(), msgs
        assert f.read_text() == "ALPHA\nbeta\ngamma\n"


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
# Block resolution (_resolve_block_end)
# ---------------------------------------------------------------------------


class TestResolveBlockEnd:
    def test_python_function(self):
        content = "def foo():\n    x = 1\n    return x\n\ndef bar():\n    pass\n"
        lines = content.splitlines()
        # Block at line 1 (def foo) should end at line 3 (return x)
        assert _resolve_block_end(lines, 1) == 3

    def test_single_line_block(self):
        content = "x = 1\ny = 2\n"
        lines = content.splitlines()
        # Line 1 has no indented body — block is just line 1
        assert _resolve_block_end(lines, 1) == 1

    def test_nested_blocks(self):
        content = "class Foo:\n    def method(self):\n        return 1\n\n    def other(self):\n        return 2\n"
        lines = content.splitlines()
        # Block at line 1 (class Foo) ends at line 6 (last method body)
        assert _resolve_block_end(lines, 1) == 6

    def test_blank_lines_inside_body_absorbed(self):
        content = (
            "def foo():\n    x = 1\n\n    y = 2\n    return y\n\ndef bar():\n    pass\n"
        )
        lines = content.splitlines()
        # Blank line (line 3) is inside the body; block ends at line 5
        assert _resolve_block_end(lines, 1) == 5

    def test_if_block(self):
        content = "if x:\n    do_a()\n    do_b()\ndo_c()\n"
        lines = content.splitlines()
        assert _resolve_block_end(lines, 1) == 3

    def test_last_function_in_file(self):
        content = "def foo():\n    return 1\n"
        lines = content.splitlines()
        assert _resolve_block_end(lines, 1) == 2

    def test_out_of_range_raises(self):
        lines = ["a", "b"]
        with pytest.raises(ValueError, match="out of range"):
            _resolve_block_end(lines, 5)

    def test_if_elif_else_absorbed(self):
        content = "if x:\n    do_a()\nelif y:\n    do_b()\nelse:\n    do_c()\ndo_d()\n"
        lines = content.splitlines()
        # Block at line 1 (if x) must absorb elif/else through line 6
        assert _resolve_block_end(lines, 1) == 6

    def test_try_except_finally_absorbed(self):
        content = (
            "try:\n"
            "    risky()\n"
            "except ValueError as e:\n"
            "    handle(e)\n"
            "finally:\n"
            "    cleanup()\n"
            "after()\n"
        )
        lines = content.splitlines()
        assert _resolve_block_end(lines, 1) == 6

    def test_same_indent_non_continuation_not_absorbed(self):
        # A same-indentation line that is not elif/else/except/finally still
        # terminates the block (e.g. an unrelated statement after an if).
        content = "if x:\n    do_a()\nelifish_var = 1\n"
        lines = content.splitlines()
        assert _resolve_block_end(lines, 1) == 2

    def test_multiline_function_header(self):
        content = (
            "def foo(\n"
            "    first: tuple[int, str],\n"
            "    second: str = 'ignore ) in strings',\n"
            "):\n"
            "    return first\n"
            "after()\n"
        )
        assert _resolve_block_end(content.splitlines(), 1) == 5

    def test_multiline_if_header(self):
        content = "if (\n    first\n    and second\n):\n    act()\nafter()\n"
        assert _resolve_block_end(content.splitlines(), 1) == 5

    def test_comment_bracket_does_not_hold_header_open(self):
        content = "def foo(  # unmatched ]\n    value,\n):\n    return value\nafter()\n"
        assert _resolve_block_end(content.splitlines(), 1) == 4

    def test_non_python_else_section_not_absorbed(self):
        content = "commands:\n  - run\nelse:\n  - cleanup\n"
        assert _resolve_block_end(content.splitlines(), 1) == 2

    def test_comment_between_clauses_does_not_strand_else(self):
        # A same-indentation comment before else/except/etc. must not end the
        # block early — the else clause is still part of the same statement.
        content = "if x:\n    do_a()\n# comment\nelse:\n    do_b()\ndo_c()\n"
        lines = content.splitlines()
        assert _resolve_block_end(lines, 1) == 5

    def test_multiple_comments_between_clauses_absorbed(self):
        content = (
            "try:\n"
            "    risky()\n"
            "# note 1\n"
            "# note 2\n"
            "except ValueError:\n"
            "    handle()\n"
            "after()\n"
        )
        lines = content.splitlines()
        assert _resolve_block_end(lines, 1) == 6

    def test_comment_with_no_following_continuation_not_absorbed(self):
        # A trailing same-indentation comment with no continuation clause after
        # it must not be pulled into the block, nor extend past it.
        content = "if x:\n    do_a()\n# trailing comment\nprint('done')\n"
        lines = content.splitlines()
        assert _resolve_block_end(lines, 1) == 2

    def test_unclosed_bracket_header_raises_instead_of_eating_file(self):
        # A header whose bracket never closes must NOT absorb to EOF — that
        # would make PUT N*: silently truncate the rest of the file.
        content = "def foo(\n    x,\nunrelated = 1\nmore = 2\n"
        with pytest.raises(ValueError, match="unterminated bracket"):
            _resolve_block_end(content.splitlines(), 1)

    def test_unmatched_paren_in_prose_raises(self):
        # hashline_edit edits any file, not just Python: an unmatched paren in
        # plain text must not swallow the remainder of the document.
        content = "- item (see note\n- item two\n- item three\n"
        with pytest.raises(ValueError, match="unterminated bracket"):
            _resolve_block_end(content.splitlines(), 1)

    def test_unterminated_triple_quote_raises(self):
        content = 'x = """\nline a\nline b\n'
        with pytest.raises(ValueError, match="unterminated string"):
            _resolve_block_end(content.splitlines(), 1)

    def test_multiline_string_header_absorbs_string_body(self):
        # The header opens a triple-quoted string: the block runs to its close,
        # not just the opening line.
        content = 'x = """\nline a\nline b\n"""\ny = 2\n'
        assert _resolve_block_end(content.splitlines(), 1) == 4

    def test_dedented_string_body_does_not_end_block(self):
        # A left-aligned line inside a triple-quoted string in the body has
        # indent <= header indent, but is still part of the block.
        content = (
            'def foo():\n    x = """\ntext at column 0\n"""\n    return 1\nafter()\n'
        )
        assert _resolve_block_end(content.splitlines(), 1) == 5

    def test_indented_docstring_still_resolves(self):
        content = 'def foo():\n    """Doc\n    more\n    """\n    return 1\nafter()\n'
        assert _resolve_block_end(content.splitlines(), 1) == 5

    def test_brace_delimited_block_resolves(self):
        # C-style braces close the header scan at the matching brace.
        content = "if (x) {\n    foo();\n}\nafter();\n"
        assert _resolve_block_end(content.splitlines(), 1) == 3


# ---------------------------------------------------------------------------
# PUT N*: block-aware replace
# ---------------------------------------------------------------------------


class TestBlockReplace:
    def test_parse_put_block_op(self):
        code = "[foo.py#A1B2C3D4]\nPUT 1*:\n+def foo():\n+    pass\n"
        _, _, ops = _parse_operations(code)
        assert len(ops) == 1
        assert ops[0].kind == "block_replace"
        assert ops[0].start == 1
        assert ops[0].end == -1  # sentinel; resolved at apply time

    def test_apply_block_replace_function(self):
        content = "def foo():\n    x = 1\n    return x\n\ndef bar():\n    pass\n"
        ops = [
            HashlineOp(
                kind="block_replace", start=1, end=-1, text="def foo():\n    return 42"
            )
        ]
        result = _apply_operations(content, ops)
        assert "def foo():\n    return 42\n\ndef bar():" in result
        assert "x = 1" not in result

    def test_apply_block_replace_unclosed_bracket_is_rejected(self):
        # Regression: PUT N*: on a header with an unclosed bracket used to
        # resolve to EOF and delete the remainder of the file.
        content = "def foo(\n    x,\nkeep_me = 1\nkeep_me_too = 2\n"
        ops = [HashlineOp(kind="block_replace", start=1, end=-1, text="def foo(x):")]
        with pytest.raises(ValueError, match="unterminated bracket"):
            _apply_operations(content, ops)

    def test_execute_put_block_unclosed_bracket_leaves_file_intact(
        self, tmp_path: Path
    ):
        f = tmp_path / "broken.py"
        original = "def foo(\n    x,\nkeep_me = 1\nkeep_me_too = 2\n"
        f.write_text(original)
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nPUT 1*:\n+def foo(x):\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "unterminated bracket" in msgs[0]
        assert f.read_text() == original  # nothing truncated

    def test_apply_block_replace_single_line(self):
        content = "x = 1\ny = 2\n"
        ops = [HashlineOp(kind="block_replace", start=1, end=-1, text="x = 99")]
        result = _apply_operations(content, ops)
        assert result == "x = 99\ny = 2\n"

    def test_execute_put_block(self, tmp_path: Path):
        f = tmp_path / "example.py"
        f.write_text(
            "def greet(name):\n    print('Hello')\n    return name\n\ndef bye():\n    pass\n"
        )
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nPUT 1*:\n+def greet(name):\n+    print(f'Hi, {{name}}')\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "applied" in msgs[0].lower()
        result = f.read_text()
        assert "Hi, {name}" in result or "Hi," in result
        assert "Hello" not in result
        assert "def bye" in result  # other function untouched

    def test_put_block_in_instructions(self):
        assert "PUT N*:" in tool.instructions

    def test_apply_block_replace_if_elif_else(self):
        content = "if x:\n    do_a()\nelif y:\n    do_b()\nelse:\n    do_c()\ndo_d()\n"
        ops = [
            HashlineOp(kind="block_replace", start=1, end=-1, text="if z:\n    do_z()")
        ]
        result = _apply_operations(content, ops)
        assert result == "if z:\n    do_z()\ndo_d()\n"
        assert "elif" not in result
        assert "else" not in result

    def test_apply_block_replace_multiline_header_with_adjacent_cut(self):
        content = (
            "before = True\n"
            "def foo(\n"
            "    first,\n"
            "    second,\n"
            "):\n"
            "    return first + second\n"
            "obsolete = True\n"
            "after = True\n"
        )
        ops = [
            HashlineOp(
                kind="block_replace", start=2, end=-1, text="def foo():\n    return 42"
            ),
            HashlineOp(kind="delete", start=7, end=7, text=None),
        ]
        assert _apply_operations(content, ops) == (
            "before = True\ndef foo():\n    return 42\nafter = True\n"
        )

    def test_apply_block_replace_comment_between_clauses(self):
        content = "if x:\n    do_a()\n# comment\nelse:\n    do_b()\ndo_c()\n"
        ops = [
            HashlineOp(kind="block_replace", start=1, end=-1, text="if z:\n    do_z()")
        ]
        result = _apply_operations(content, ops)
        assert result == "if z:\n    do_z()\ndo_c()\n"
        assert "# comment" not in result
        assert "else" not in result


# ---------------------------------------------------------------------------
# Phase 2 — 3-way merge recovery
# ---------------------------------------------------------------------------


class TestMergeRecovery:
    """Phase 2: 3-way merge when the live file changed since the snapshot."""

    def test_clean_merge_preserves_concurrent_change(self, tmp_path: Path):
        """Non-conflicting external change is preserved alongside the model's edit."""
        f = tmp_path / "code.py"
        original = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        f.write_text(original)
        tag = store_snapshot(str(f.resolve()), original)
        # External change: add a line to bar() — doesn't conflict with our edit
        f.write_text("def foo():\n    pass\n\ndef bar():\n    return 1\n")
        # Model edit: replace foo body
        block = f"[{f.resolve()}#{tag}]\nPUT 2.=2:\n+    return 'foo'\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "applied" in msgs[0].lower(), msgs
        assert "3-way merge" in msgs[0].lower(), msgs
        result = f.read_text()
        assert "return 'foo'" in result  # our edit applied
        assert "return 1" in result  # external change preserved

    def test_conflicting_merge_reports_markers(self, tmp_path: Path):
        """When both sides edit the same lines, conflict markers are reported."""
        f = tmp_path / "conflict.txt"
        original = "line1\nline2\n"
        f.write_text(original)
        tag = store_snapshot(str(f.resolve()), original)
        # External change: also modifies line 1
        f.write_text("EXTERNAL_CHANGE\nline2\n")
        # Model edit: replace line 1 with something different
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+MODEL_CHANGE\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        # Conflict is reported
        assert "conflict" in msgs[0].lower(), msgs
        # File is left unchanged so the user can resolve manually
        assert f.read_text() == "EXTERNAL_CHANGE\nline2\n"

    def test_merge_recovery_note_absent_on_clean_apply(self, tmp_path: Path):
        """When file is unchanged, success message has no merge-recovery note."""
        f = tmp_path / "f.txt"
        f.write_text("a\nb\n")
        tag = store_snapshot(str(f.resolve()), f.read_text())
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+A\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "applied" in msgs[0].lower(), msgs
        assert "3-way merge" not in msgs[0].lower()

    def test_merge_snapshot_updated_after_recovery(self, tmp_path: Path):
        """After a successful merge recovery, the snapshot reflects the new content."""
        f = tmp_path / "f.txt"
        original = "a\nb\nc\n"
        f.write_text(original)
        tag = store_snapshot(str(f.resolve()), original)
        # External change at end — non-conflicting
        f.write_text("a\nb\nc\nextra\n")
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+A\n"
        msgs = _msgs(execute_hashline_edit(block, [str(f)], None))
        assert "applied" in msgs[0].lower(), msgs
        new_content = f.read_text()
        assert new_content == "A\nb\nc\nextra\n"
        # Snapshot should reflect the merged content
        new_tag = get_stored_tag(str(f.resolve()))
        from gptme.tools._hashline_snapshot import compute_tag

        assert new_tag == compute_tag(new_content)

    def test_operational_merge_failure_high_exit_code(self, tmp_path: Path):
        """git merge-file returning exit > 127 is reported as an operational error."""
        from subprocess import CompletedProcess
        from unittest.mock import patch

        f = tmp_path / "f.txt"
        original = "alpha\nbeta\n"
        f.write_text(original)
        tag = store_snapshot(str(f.resolve()), original)
        # Trigger merge-recovery path by changing the live file.
        f.write_text("alpha\nbeta\nextra\n")
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+ALPHA\n"

        # Simulate git merge-file exiting with 128 (git internal error — e.g. git not
        # found at runtime, or repository state corruption).  returncode > 127 is the
        # reliable signal for an operational failure; conflict counts are 1-127.
        fake_result = CompletedProcess(
            args=["git", "merge-file", "-p"],
            returncode=128,
            stdout="",
            stderr="error: could not read repository",
        )
        with patch("subprocess.run", return_value=fake_result):
            msgs = _msgs(execute_hashline_edit(block, [str(f)], None))

        # The error should be surfaced, not silently treated as a merge conflict.
        assert any("git merge-file failed" in m or "repository" in m for m in msgs), (
            msgs
        )

    def test_conflict_with_stderr_warning_shows_markers(self, tmp_path: Path):
        """A real conflict that also emits a warning to stderr shows conflict markers, not an error."""
        from subprocess import CompletedProcess
        from unittest.mock import patch

        f = tmp_path / "f.txt"
        original = "alpha\nbeta\n"
        f.write_text(original)
        tag = store_snapshot(str(f.resolve()), original)
        # Trigger merge-recovery path.
        f.write_text("alpha\nbeta\nextra\n")
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+ALPHA\n"

        # Simulate git merge-file finding a conflict (returncode=1 = 1 conflict section)
        # while also writing a warning to stderr.  git CAN do this in edge cases
        # (e.g. truncation warnings on very large files).  The old code treated any
        # stderr as an operational failure, which would suppress the conflict markers.
        conflict_output = "<<<<<<< your edit (via hashline_edit)\nALPHA\n=======\nalpha\n>>>>>>> current file\nbeta\nextra\n"
        fake_result = CompletedProcess(
            args=["git", "merge-file", "-p"],
            returncode=1,
            stdout=conflict_output,
            stderr="warning: too many conflicts",
        )
        with patch("subprocess.run", return_value=fake_result):
            msgs = _msgs(execute_hashline_edit(block, [str(f)], None))

        # Conflict markers must be shown — not an error about git failing.
        assert any("conflict" in m.lower() for m in msgs), msgs
        assert not any("git merge-file failed" in m for m in msgs), msgs
        # File should be left untouched so the user can resolve.
        assert f.read_text() == "alpha\nbeta\nextra\n"

    def test_git_not_found_raises_informative_error(self, tmp_path: Path):
        """FileNotFoundError from git being absent surfaces a clear diagnostic, not a traceback."""
        from unittest.mock import patch

        f = tmp_path / "f.txt"
        original = "alpha\nbeta\n"
        f.write_text(original)
        tag = store_snapshot(str(f.resolve()), original)
        # Trigger merge-recovery path by changing the live file.
        f.write_text("alpha\nbeta\nextra\n")
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+ALPHA\n"

        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            msgs = _msgs(execute_hashline_edit(block, [str(f)], None))

        # Should surface a human-readable message, not a raw traceback.
        assert any("git" in m.lower() or "read" in m.lower() for m in msgs), msgs
        # File must be left untouched.
        assert f.read_text() == "alpha\nbeta\nextra\n"

    def test_signal_killed_git_is_treated_as_error(self, tmp_path: Path):
        """git merge-file killed by a signal (negative returncode) must not write partial output."""
        from subprocess import CompletedProcess
        from unittest.mock import patch

        f = tmp_path / "f.txt"
        original = "alpha\nbeta\n"
        f.write_text(original)
        tag = store_snapshot(str(f.resolve()), original)
        # Trigger merge-recovery path by changing the live file.
        f.write_text("alpha\nbeta\nextra\n")
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+ALPHA\n"

        # Simulate OOM-kill: returncode -9 (SIGKILL), with some partial stdout.
        # Before the fix, this partial content was written as a "clean" merge.
        fake_result = CompletedProcess(
            args=["git", "merge-file", "-p"],
            returncode=-9,
            stdout="partial\noutput\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=fake_result):
            msgs = _msgs(execute_hashline_edit(block, [str(f)], None))

        # Must surface an error, not silently write the partial content.
        assert any(
            "git merge-file failed" in m or "failed" in m.lower() for m in msgs
        ), msgs
        # File must be left untouched.
        assert f.read_text() == "alpha\nbeta\nextra\n"

    def test_edit_confirmation_race_aborts_on_concurrent_change(self, tmp_path: Path):
        """EDIT confirmation path must abort when the live file changes during the dialog."""
        from unittest.mock import patch

        from gptme.hooks.confirm import ConfirmationResult

        f = tmp_path / "f.txt"
        original = "alpha\nbeta\n"
        f.write_text(original)
        tag = store_snapshot(str(f.resolve()), original)
        # Trigger merge recovery: external change at the bottom (non-conflicting)
        f.write_text("alpha\nbeta\nextra\n")
        # Model edit: replace alpha (top) — no conflict with the external change
        block = f"[{f.resolve()}#{tag}]\nPUT 1.=1:\n+ALPHA\n"

        # The confirmation returns EDIT with some edited content.  While the
        # dialog is "open", a second concurrent process changes the file again.
        concurrent_content = "CONCURRENT CHANGE\nbeta\nextra\n"

        def fake_confirm(**kwargs):
            # Simulate another process modifying the file mid-dialog.
            f.write_text(concurrent_content)
            return ConfirmationResult.edit("ALPHA\nbeta\nextra\nUSER_EDIT\n")

        with patch("gptme.hooks.get_confirmation", side_effect=fake_confirm):
            msgs = _msgs(execute_hashline_edit(block, [str(f)], None))

        # The race must be detected; the operation is aborted.
        assert any("changed" in m.lower() or "read" in m.lower() for m in msgs), msgs
        # The concurrent write must be preserved (file not overwritten by the edit).
        assert f.read_text() == concurrent_content


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
