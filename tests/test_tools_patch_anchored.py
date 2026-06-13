"""Tests for the view_anchored and patch_anchored tools."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from gptme.tools._anchored import snapshot_text
from gptme.tools.patch_anchored import (
    _parse_ops,
    _render_anchored,
    execute_patch_anchored,
    execute_view_anchored,
    tool_patch_anchored,
    tool_view_anchored,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect(gen) -> list[str]:
    """Collect message content strings from a generator."""
    return [msg.content for msg in gen]


# ---------------------------------------------------------------------------
# _render_anchored
# ---------------------------------------------------------------------------


class TestRenderAnchored:
    def test_each_line_has_anchor_prefix(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.py"
        f.write_text("def hello():\n    pass\n")
        content = f.read_text()
        rendered = _render_anchored(f, content)
        anchors = snapshot_text(content)

        for a in anchors:
            assert a.anchor in rendered
            assert a.text in rendered

    def test_separator_present(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("line one\nline two\n")
        rendered = _render_anchored(f, f.read_text())
        assert "│" in rendered

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("")
        rendered = _render_anchored(f, "")
        assert "empty file" in rendered

    def test_anchor_format_in_output(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("alpha\nbeta\n")
        rendered = _render_anchored(f, f.read_text())
        # Every anchor is <16-hex>:<ordinal> — check colon presence per line
        body_lines = [ln for ln in rendered.splitlines() if "│" in ln]
        assert len(body_lines) == 2
        for line in body_lines:
            anchor_part = line.split("│")[0].strip()
            digest, ordinal = anchor_part.split(":")
            assert len(digest) == 16
            assert ordinal.isdigit()


# ---------------------------------------------------------------------------
# _parse_ops
# ---------------------------------------------------------------------------


class TestParseOps:
    def test_valid_replace(self) -> None:
        ops = _parse_ops('[{"anchor": "abc:1", "op": "replace", "text": "new"}]')
        assert len(ops) == 1
        assert ops[0].op == "replace"
        assert ops[0].text == "new"

    def test_valid_delete(self) -> None:
        ops = _parse_ops('[{"anchor": "abc:1", "op": "delete"}]')
        assert ops[0].op == "delete"
        assert ops[0].text is None

    def test_valid_with_expected(self) -> None:
        ops = _parse_ops(
            '[{"anchor": "abc:1", "op": "replace", "text": "X", "expected": "old"}]'
        )
        assert ops[0].expected == "old"

    def test_multiple_ops(self) -> None:
        raw = json.dumps(
            [
                {"anchor": "a:1", "op": "replace", "text": "X"},
                {"anchor": "b:1", "op": "delete"},
            ]
        )
        ops = _parse_ops(raw)
        assert len(ops) == 2

    def test_invalid_json(self) -> None:
        with pytest.raises(ValueError, match="Invalid JSON"):
            _parse_ops("not json")

    def test_not_an_array(self) -> None:
        with pytest.raises(ValueError, match="JSON array"):
            _parse_ops('{"anchor": "a:1", "op": "replace"}')

    def test_missing_anchor(self) -> None:
        with pytest.raises(ValueError, match="anchor"):
            _parse_ops('[{"op": "replace", "text": "x"}]')

    def test_missing_op(self) -> None:
        with pytest.raises(ValueError, match="op"):
            _parse_ops('[{"anchor": "a:1", "text": "x"}]')


# ---------------------------------------------------------------------------
# execute_view_anchored
# ---------------------------------------------------------------------------


class TestExecuteViewAnchored:
    def test_renders_file_with_anchors(self, tmp_path: Path) -> None:
        f = tmp_path / "sample.py"
        f.write_text("alpha\nbeta\ngamma\n")
        msgs = _collect(execute_view_anchored("", ["str(f)"], None))
        # Use kwargs interface for reliability
        msgs = _collect(execute_view_anchored("", None, {"path": str(f)}))
        assert len(msgs) == 1
        rendered = msgs[0]
        assert "alpha" in rendered
        assert "beta" in rendered
        assert "│" in rendered

    def test_missing_path(self) -> None:
        msgs = _collect(execute_view_anchored("", None, None))
        assert "no path" in msgs[0].lower()

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        msgs = _collect(
            execute_view_anchored("", None, {"path": str(tmp_path / "nope.txt")})
        )
        assert "not found" in msgs[0].lower()

    def test_directory_rejected(self, tmp_path: Path) -> None:
        msgs = _collect(execute_view_anchored("", None, {"path": str(tmp_path)}))
        assert "not a file" in msgs[0].lower()

    def test_anchor_count_matches_line_count(self, tmp_path: Path) -> None:
        f = tmp_path / "three.txt"
        f.write_text("one\ntwo\nthree\n")
        msgs = _collect(execute_view_anchored("", None, {"path": str(f)}))
        # three lines → three anchor│ lines in the output
        anchor_lines = [ln for ln in msgs[0].splitlines() if "│" in ln]
        assert len(anchor_lines) == 3

    def test_args_path(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("hello\n")
        msgs = _collect(execute_view_anchored("", [str(f)], None))
        assert "hello" in msgs[0]


# ---------------------------------------------------------------------------
# execute_patch_anchored
# ---------------------------------------------------------------------------


class TestExecutePatchAnchored:
    def _anchors_for(self, text: str) -> list:
        return snapshot_text(text)

    def test_replace_operation(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("alpha\nbeta\ngamma\n")
        anchors = self._anchors_for(f.read_text())
        ops = json.dumps(
            [{"anchor": anchors[1].anchor, "op": "replace", "text": "BETA"}]
        )
        msgs = _collect(execute_patch_anchored(ops, None, {"path": str(f)}))
        assert "applied" in msgs[0].lower()
        assert f.read_text() == "alpha\nBETA\ngamma\n"

    def test_delete_operation(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("alpha\nbeta\ngamma\n")
        anchors = self._anchors_for(f.read_text())
        ops = json.dumps([{"anchor": anchors[1].anchor, "op": "delete"}])
        msgs = _collect(execute_patch_anchored(ops, None, {"path": str(f)}))
        assert "applied" in msgs[0].lower()
        assert f.read_text() == "alpha\ngamma\n"

    def test_insert_before(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("alpha\nbeta\n")
        anchors = self._anchors_for(f.read_text())
        ops = json.dumps(
            [{"anchor": anchors[0].anchor, "op": "insert_before", "text": "zero"}]
        )
        msgs = _collect(execute_patch_anchored(ops, None, {"path": str(f)}))
        assert "applied" in msgs[0].lower()
        assert f.read_text() == "zero\nalpha\nbeta\n"

    def test_insert_after(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("alpha\nbeta\n")
        anchors = self._anchors_for(f.read_text())
        ops = json.dumps(
            [{"anchor": anchors[0].anchor, "op": "insert_after", "text": "one-half"}]
        )
        _collect(execute_patch_anchored(ops, None, {"path": str(f)}))
        assert f.read_text() == "alpha\none-half\nbeta\n"

    def test_atomic_reject_on_unknown_anchor(self, tmp_path: Path) -> None:
        """Stale anchor rejects the whole batch and leaves the file unchanged."""
        f = tmp_path / "file.txt"
        original = "alpha\nbeta\ngamma\n"
        f.write_text(original)
        # Use an anchor computed from a different file state
        other_text = "alpha\ninserted\nbeta\ngamma\n"
        stale_anchor = snapshot_text(other_text)[2].anchor  # "beta" in modified file
        ops = json.dumps([{"anchor": stale_anchor, "op": "replace", "text": "X"}])
        msgs = _collect(execute_patch_anchored(ops, None, {"path": str(f)}))
        assert "failed" in msgs[0].lower() or "rejected" in msgs[0].lower()
        assert f.read_text() == original  # unchanged

    def test_failure_message_includes_rerender(self, tmp_path: Path) -> None:
        """On failure the response includes a re-rendered view for retry."""
        f = tmp_path / "file.txt"
        f.write_text("alpha\nbeta\n")
        ops = json.dumps(
            [{"anchor": "0000000000000000:1", "op": "replace", "text": "X"}]
        )
        msgs = _collect(execute_patch_anchored(ops, None, {"path": str(f)}))
        assert "│" in msgs[0]  # re-render present

    def test_expected_guard_success(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("alpha\nbeta\n")
        anchors = self._anchors_for(f.read_text())
        ops = json.dumps(
            [
                {
                    "anchor": anchors[0].anchor,
                    "op": "replace",
                    "text": "ALPHA",
                    "expected": "alpha",
                }
            ]
        )
        msgs = _collect(execute_patch_anchored(ops, None, {"path": str(f)}))
        assert "applied" in msgs[0].lower()
        assert f.read_text() == "ALPHA\nbeta\n"

    def test_expected_guard_failure_rejects_batch(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        original = "alpha\nbeta\n"
        f.write_text(original)
        anchors = self._anchors_for(original)
        ops = json.dumps(
            [
                {
                    "anchor": anchors[0].anchor,
                    "op": "replace",
                    "text": "ALPHA",
                    "expected": "wrong_text",
                }
            ]
        )
        msgs = _collect(execute_patch_anchored(ops, None, {"path": str(f)}))
        assert "failed" in msgs[0].lower() or "rejected" in msgs[0].lower()
        assert f.read_text() == original

    def test_missing_path(self) -> None:
        msgs = _collect(
            execute_patch_anchored('[{"anchor":"a:1","op":"delete"}]', None, None)
        )
        assert "no path" in msgs[0].lower()

    def test_missing_ops(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("hello\n")
        msgs = _collect(execute_patch_anchored("", None, {"path": str(f)}))
        assert "no operations" in msgs[0].lower()

    def test_invalid_json_ops(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("hello\n")
        msgs = _collect(execute_patch_anchored("not json!", None, {"path": str(f)}))
        assert "invalid json" in msgs[0].lower() or "patch_anchored" in msgs[0].lower()

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        ops = '[{"anchor": "a:1", "op": "delete"}]'
        msgs = _collect(
            execute_patch_anchored(ops, None, {"path": str(tmp_path / "nope.txt")})
        )
        assert "not found" in msgs[0].lower()

    def test_multi_op_batch(self, tmp_path: Path) -> None:
        """Multiple operations in one batch are applied atomically."""
        f = tmp_path / "file.txt"
        f.write_text("alpha\nbeta\ngamma\ndelta\n")
        anchors = self._anchors_for(f.read_text())
        ops = json.dumps(
            [
                {"anchor": anchors[0].anchor, "op": "replace", "text": "ALPHA"},
                {"anchor": anchors[2].anchor, "op": "delete"},
            ]
        )
        _collect(execute_patch_anchored(ops, None, {"path": str(f)}))
        assert f.read_text() == "ALPHA\nbeta\ndelta\n"

    def test_args_path(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("hello\n")
        anchors = self._anchors_for(f.read_text())
        ops = json.dumps(
            [{"anchor": anchors[0].anchor, "op": "replace", "text": "world"}]
        )
        msgs = _collect(execute_patch_anchored(ops, [str(f)], None))
        assert "applied" in msgs[0].lower()


# ---------------------------------------------------------------------------
# ToolSpec metadata
# ---------------------------------------------------------------------------


class TestToolSpecs:
    def test_view_anchored_disabled_by_default(self) -> None:
        assert tool_view_anchored.disabled_by_default is True

    def test_patch_anchored_disabled_by_default(self) -> None:
        assert tool_patch_anchored.disabled_by_default is True

    def test_view_anchored_block_type(self) -> None:
        assert "view_anchored" in tool_view_anchored.block_types

    def test_patch_anchored_block_type(self) -> None:
        assert "patch_anchored" in tool_patch_anchored.block_types

    def test_view_anchored_has_parameters(self) -> None:
        assert any(p.name == "path" for p in tool_view_anchored.parameters)

    def test_patch_anchored_has_parameters(self) -> None:
        names = {p.name for p in tool_patch_anchored.parameters}
        assert "path" in names
        assert "ops" in names
