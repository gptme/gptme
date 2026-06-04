"""Tests for the hash-anchored editing engine (gptme.tools._anchored).

Ported from the Bob-local prototype tests in
``tests/test_hash_anchor_edit.py``.
"""

from __future__ import annotations

import pytest

from gptme.tools._anchored import (
    EditOperation,
    apply_operations,
    snapshot_text,
)


class TestSnapshotText:
    def test_anchor_survives_unrelated_insertions(self) -> None:
        """Anchors are content-addressed, so prepending lines doesn't break them."""
        original = "alpha\nbeta\ngamma\ndelta\n"
        target_anchor = snapshot_text(original)[2].anchor  # "gamma"

        updated = "header\n" + original
        matching = [item for item in snapshot_text(updated) if item.text == "gamma"]

        assert matching[0].anchor == target_anchor

    def test_duplicate_lines_get_distinct_ordinals(self) -> None:
        """Two identical `dup` lines get different anchor tokens via ordinals."""
        original = "x\ndup\ny\nx\ndup\ny\n"
        duplicate_anchors = [
            item for item in snapshot_text(original) if item.text == "dup"
        ]

        assert len(duplicate_anchors) == 2
        assert duplicate_anchors[0].anchor != duplicate_anchors[1].anchor

    def test_all_lines_get_anchors(self) -> None:
        """Every line, including first and last, gets exactly one anchor."""
        text = "one\ntwo\nthree\n"
        anchors = snapshot_text(text)
        assert len(anchors) == 3
        assert anchors[0].text == "one"
        assert anchors[-1].text == "three"

    def test_empty_text(self) -> None:
        """Empty text produces no anchors."""
        assert snapshot_text("") == []

    def test_single_line(self) -> None:
        """A single line gets one anchor with empty prev/next."""
        anchors = snapshot_text("solo\n")
        assert len(anchors) == 1
        assert anchors[0].text == "solo"
        assert anchors[0].prev_text == ""
        assert anchors[0].next_text == ""

    def test_exact_duplicate_triple_gets_ordinal_2(self) -> None:
        """Two lines with identical prev/text/next triples get ordinals 1 and 2."""
        # To get exact triple duplicates (prev + text + next all identical),
        # we need lines surrounded by identical neighbors:
        text2 = "ab\ntarget\ncd\nab\ntarget\ncd\n"
        anchors2 = snapshot_text(text2)
        target_anchors = [a for a in anchors2 if a.text == "target"]
        assert len(target_anchors) == 2
        assert target_anchors[0].ordinal == 1
        assert target_anchors[1].ordinal == 2
        assert target_anchors[0].anchor != target_anchors[1].anchor

    def test_anchor_format(self) -> None:
        """Anchor token format: <digest>:<ordinal>."""
        anchors = snapshot_text("hello\n")
        parts = anchors[0].anchor.split(":")
        assert len(parts) == 2
        assert len(parts[0]) == 16  # 8-byte blake2s hex
        assert parts[1] == "1"


class TestApplyOperations:
    def test_resolves_all_targets_before_mutating(self) -> None:
        """All anchors resolve against the original text, not a partially-mutated buffer."""
        original = "alpha\nbeta\ngamma\ndelta\n"
        anchors = snapshot_text(original)

        updated = apply_operations(
            original,
            [
                EditOperation(anchor=anchors[0].anchor, op="replace", text="ALPHA"),
                EditOperation(
                    anchor=anchors[2].anchor, op="insert_before", text="one\ntwo"
                ),
                EditOperation(anchor=anchors[3].anchor, op="delete"),
            ],
        )

        assert updated == "ALPHA\nbeta\none\ntwo\ngamma\n"

    def test_duplicate_line_replacement(self) -> None:
        """Can target the *second* identical duplicate line via ordinal."""
        original = "x\ndup\ny\nx\ndup\ny\n"
        duplicate_anchors = [
            item for item in snapshot_text(original) if item.text == "dup"
        ]

        updated = apply_operations(
            original,
            [
                EditOperation(
                    anchor=duplicate_anchors[1].anchor, op="replace", text="changed"
                )
            ],
        )

        assert updated == "x\ndup\ny\nx\nchanged\ny\n"

    def test_rejects_unknown_anchor_after_adjacent_change(self) -> None:
        """Adjacent-line changes invalidate anchors — the batch must fail loudly."""
        original = "alpha\nbeta\ngamma\n"
        target_anchor = snapshot_text(original)[1].anchor
        changed = "alpha\ninserted\nbeta\ngamma\n"

        with pytest.raises(ValueError, match="Unknown anchor"):
            apply_operations(
                changed,
                [EditOperation(anchor=target_anchor, op="replace", text="BETA")],
            )

    def test_rejects_duplicate_anchor_in_same_batch(self) -> None:
        """Two ops targeting the same anchor in one batch should fail."""
        original = "alpha\nbeta\ngamma\n"
        anchors = snapshot_text(original)

        with pytest.raises(ValueError, match="Multiple operations target"):
            apply_operations(
                original,
                [
                    EditOperation(anchor=anchors[0].anchor, op="replace", text="A"),
                    EditOperation(anchor=anchors[0].anchor, op="delete"),
                ],
            )

    def test_expected_guard_passes(self) -> None:
        """`expected` guard succeeds when the line matches."""
        original = "alpha\nbeta\ngamma\n"
        anchors = snapshot_text(original)

        updated = apply_operations(
            original,
            [
                EditOperation(
                    anchor=anchors[0].anchor,
                    op="replace",
                    text="ALPHA",
                    expected="alpha",
                )
            ],
        )
        assert updated == "ALPHA\nbeta\ngamma\n"

    def test_expected_guard_fails(self) -> None:
        """`expected` guard fails when the line doesn't match, rejecting the batch."""
        original = "alpha\nbeta\ngamma\n"
        anchors = snapshot_text(original)

        with pytest.raises(ValueError, match="no longer matches expected text"):
            apply_operations(
                original,
                [
                    EditOperation(
                        anchor=anchors[0].anchor,
                        op="replace",
                        text="ALPHA",
                        expected="wrong",
                    )
                ],
            )

    def test_insert_before(self) -> None:
        original = "alpha\nbeta\n"
        anchors = snapshot_text(original)

        updated = apply_operations(
            original,
            [EditOperation(anchor=anchors[0].anchor, op="insert_before", text="zero")],
        )
        assert updated == "zero\nalpha\nbeta\n"

    def test_insert_after(self) -> None:
        original = "alpha\nbeta\n"
        anchors = snapshot_text(original)

        updated = apply_operations(
            original,
            [
                EditOperation(
                    anchor=anchors[0].anchor, op="insert_after", text="one-half"
                )
            ],
        )
        assert updated == "alpha\none-half\nbeta\n"

    def test_delete(self) -> None:
        original = "alpha\nbeta\ngamma\n"
        anchors = snapshot_text(original)

        updated = apply_operations(
            original,
            [EditOperation(anchor=anchors[1].anchor, op="delete")],
        )
        assert updated == "alpha\ngamma\n"

    def test_delete_final_line(self) -> None:
        """Deleting the last line works; trailing-newline preservation is determined
        by the original text's ending."""
        original = "alpha\nbeta\n"
        anchors = snapshot_text(original)
        updated = apply_operations(
            original,
            [EditOperation(anchor=anchors[1].anchor, op="delete")],
        )
        assert updated == "alpha\n"

    def test_preserves_trailing_newline(self) -> None:
        """Trailing newline is preserved through edits."""
        original = "alpha\nbeta\n"
        anchors = snapshot_text(original)
        updated = apply_operations(
            original,
            [EditOperation(anchor=anchors[0].anchor, op="replace", text="ALPHA")],
        )
        assert updated == "ALPHA\nbeta\n"

    def test_empty_operations_list(self) -> None:
        """No-op: empty ops list returns original text unchanged."""
        original = "alpha\nbeta\n"
        assert apply_operations(original, []) == original

    def test_multi_line_replace(self) -> None:
        """Replace a single line with multiple lines."""
        original = "alpha\nbeta\ngamma\n"
        anchors = snapshot_text(original)
        updated = apply_operations(
            original,
            [EditOperation(anchor=anchors[1].anchor, op="replace", text="B1\nB2\nB3")],
        )
        assert updated == "alpha\nB1\nB2\nB3\ngamma\n"

    def test_text_with_trailing_newline_no_spurious_blank(self) -> None:
        """EditOperation.text ending with \\n must not insert a spurious blank line."""
        original = "alpha\nbeta\ngamma\n"
        anchors = snapshot_text(original)
        updated = apply_operations(
            original,
            [EditOperation(anchor=anchors[1].anchor, op="replace", text="B1\nB2\n")],
        )
        assert updated == "alpha\nB1\nB2\ngamma\n"

    def test_replace_with_none_text_raises(self) -> None:
        """replace op with text=None must raise ValueError, not silently delete."""
        original = "alpha\nbeta\ngamma\n"
        anchors = snapshot_text(original)
        with pytest.raises(ValueError, match="requires text"):
            apply_operations(
                original,
                [EditOperation(anchor=anchors[1].anchor, op="replace", text=None)],
            )

    def test_insert_before_with_none_text_raises(self) -> None:
        """insert_before op with text=None must raise ValueError."""
        original = "alpha\nbeta\n"
        anchors = snapshot_text(original)
        with pytest.raises(ValueError, match="requires text"):
            apply_operations(
                original,
                [
                    EditOperation(
                        anchor=anchors[0].anchor, op="insert_before", text=None
                    )
                ],
            )

    def test_insert_after_with_none_text_raises(self) -> None:
        """insert_after op with text=None must raise ValueError."""
        original = "alpha\nbeta\n"
        anchors = snapshot_text(original)
        with pytest.raises(ValueError, match="requires text"):
            apply_operations(
                original,
                [EditOperation(anchor=anchors[0].anchor, op="insert_after", text=None)],
            )

    def test_crlf_line_endings_preserved(self) -> None:
        """CRLF line endings survive a round-trip — empty ops return text unchanged."""
        original = "alpha\r\nbeta\r\ngamma\r\n"
        assert apply_operations(original, []) == original

    def test_crlf_line_endings_preserved_through_edit(self) -> None:
        """CRLF line endings are preserved when an edit is applied."""
        original = "alpha\r\nbeta\r\ngamma\r\n"
        anchors = snapshot_text(original)
        updated = apply_operations(
            original,
            [EditOperation(anchor=anchors[1].anchor, op="replace", text="BETA")],
        )
        assert updated == "alpha\r\nBETA\r\ngamma\r\n"
