"""Tests for SWE-bench eval utilities."""

import json
import textwrap


def test_write_predictions_jsonl(tmp_path):
    """write_predictions_jsonl produces valid JSONL in the expected format."""
    from gptme.eval.swebench.utils import (
        KEY_INSTANCE_ID,
        KEY_MODEL,
        KEY_PATCH,
        write_predictions_jsonl,
    )

    predictions = [
        {
            KEY_INSTANCE_ID: "django__django-11099",
            KEY_MODEL: "gptme/claude-3-5-sonnet",
            KEY_PATCH: "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n",
        },
        {
            KEY_INSTANCE_ID: "sympy__sympy-20590",
            KEY_MODEL: "gptme/claude-3-5-sonnet",
            KEY_PATCH: "",  # empty patch is valid (agent made no changes)
        },
    ]

    out_path = tmp_path / "predictions.jsonl"
    result = write_predictions_jsonl(predictions, out_path)

    assert result == out_path
    assert out_path.exists()

    lines = out_path.read_text().strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first[KEY_INSTANCE_ID] == "django__django-11099"
    assert first[KEY_MODEL] == "gptme/claude-3-5-sonnet"
    assert "diff" in first[KEY_PATCH]

    second = json.loads(lines[1])
    assert second[KEY_PATCH] == ""


def test_write_predictions_jsonl_creates_parent_dir(tmp_path):
    """write_predictions_jsonl creates missing parent directories."""
    from gptme.eval.swebench.utils import write_predictions_jsonl

    nested = tmp_path / "runs" / "swebench" / "model" / "predictions.jsonl"
    write_predictions_jsonl([], nested)
    assert nested.exists()


def test_file_coverage_heuristic_pass():
    """Heuristic returns True when patch touches all expected files."""
    from gptme.eval.swebench.evaluate import _file_coverage_heuristic

    instance: dict = {
        "expected_spans": {"django/db/models/fields.py": [], "django/utils/text.py": []}
    }
    patch = textwrap.dedent("""\
        diff --git a/django/db/models/fields.py b/django/db/models/fields.py
        --- a/django/db/models/fields.py
        +++ b/django/db/models/fields.py
        @@ -1 +1 @@
        -old
        +new
        diff --git a/django/utils/text.py b/django/utils/text.py
        --- a/django/utils/text.py
        +++ b/django/utils/text.py
        @@ -1 +1 @@
        -old
        +new
    """)
    assert _file_coverage_heuristic(instance, patch) is True


def test_file_coverage_heuristic_fail():
    """Heuristic returns False when patch misses an expected file."""
    from gptme.eval.swebench.evaluate import _file_coverage_heuristic

    instance: dict = {
        "expected_spans": {"django/db/models/fields.py": [], "django/utils/text.py": []}
    }
    # Only touches one of the two expected files
    patch = textwrap.dedent("""\
        diff --git a/django/db/models/fields.py b/django/db/models/fields.py
        --- a/django/db/models/fields.py
        +++ b/django/db/models/fields.py
        @@ -1 +1 @@
        -old
        +new
    """)
    assert _file_coverage_heuristic(instance, patch) is False


def test_file_coverage_heuristic_no_expected_spans():
    """Heuristic returns True when expected_spans is absent (no data to check)."""
    from gptme.eval.swebench.evaluate import _file_coverage_heuristic

    instance: dict = {}  # no expected_spans
    assert _file_coverage_heuristic(instance, "diff --git a/foo.py ...") is True


def test_key_constants():
    """Key constants match SWE-bench's official field names."""
    from gptme.eval.swebench.utils import KEY_INSTANCE_ID, KEY_MODEL, KEY_PATCH

    assert KEY_INSTANCE_ID == "instance_id"
    assert KEY_MODEL == "model_name_or_path"
    assert KEY_PATCH == "model_patch"
