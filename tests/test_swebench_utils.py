"""Tests for SWE-bench utility functions (predictions I/O, resume logic)."""

import json

import pytest

from gptme.eval.swebench.utils import (
    KEY_INSTANCE_ID,
    KEY_MODEL,
    KEY_PATCH,
    append_prediction,
    load_existing_predictions,
    write_predictions_jsonl,
)


@pytest.fixture
def predictions_path(tmp_path):
    return tmp_path / "predictions.jsonl"


def _make_prediction(instance_id: str, patch: str = "diff --git a/f.py") -> dict:
    return {
        KEY_INSTANCE_ID: instance_id,
        KEY_MODEL: "test-model",
        KEY_PATCH: patch,
    }


class TestWritePredictions:
    def test_write_creates_file(self, predictions_path):
        preds = [_make_prediction("a"), _make_prediction("b")]
        result = write_predictions_jsonl(preds, predictions_path)
        assert result == predictions_path
        assert predictions_path.exists()

        lines = predictions_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])[KEY_INSTANCE_ID] == "a"

    def test_write_creates_parent_dirs(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "predictions.jsonl"
        write_predictions_jsonl([_make_prediction("x")], deep_path)
        assert deep_path.exists()


class TestAppendPrediction:
    def test_append_creates_file(self, predictions_path):
        append_prediction(_make_prediction("first"), predictions_path)
        assert predictions_path.exists()
        lines = predictions_path.read_text().strip().split("\n")
        assert len(lines) == 1

    def test_append_adds_to_existing(self, predictions_path):
        append_prediction(_make_prediction("first"), predictions_path)
        append_prediction(_make_prediction("second"), predictions_path)

        lines = predictions_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])[KEY_INSTANCE_ID] == "first"
        assert json.loads(lines[1])[KEY_INSTANCE_ID] == "second"


class TestLoadExistingPredictions:
    def test_nonexistent_file_returns_empty(self, predictions_path):
        result = load_existing_predictions(predictions_path)
        assert result == set()

    def test_loads_instance_ids(self, predictions_path):
        preds = [_make_prediction("a"), _make_prediction("b"), _make_prediction("c")]
        write_predictions_jsonl(preds, predictions_path)

        result = load_existing_predictions(predictions_path)
        assert result == {"a", "b", "c"}

    def test_skips_malformed_lines(self, predictions_path):
        predictions_path.write_text(
            json.dumps(_make_prediction("good"))
            + "\n"
            + "not valid json\n"
            + json.dumps(_make_prediction("also_good"))
            + "\n"
        )

        result = load_existing_predictions(predictions_path)
        assert result == {"good", "also_good"}

    def test_skips_lines_missing_instance_id(self, predictions_path):
        predictions_path.write_text(
            json.dumps(_make_prediction("valid"))
            + "\n"
            + json.dumps({"model": "x", "patch": "y"})
            + "\n"
        )

        result = load_existing_predictions(predictions_path)
        assert result == {"valid"}

    def test_skips_empty_lines(self, predictions_path):
        predictions_path.write_text(
            json.dumps(_make_prediction("a"))
            + "\n"
            + "\n"
            + "\n"
            + json.dumps(_make_prediction("b"))
            + "\n"
        )

        result = load_existing_predictions(predictions_path)
        assert result == {"a", "b"}

    def test_roundtrip_with_append(self, predictions_path):
        """Verify that append + load_existing is consistent."""
        for i in range(5):
            append_prediction(_make_prediction(f"inst_{i}"), predictions_path)

        result = load_existing_predictions(predictions_path)
        assert result == {f"inst_{i}" for i in range(5)}
