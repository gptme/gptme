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


def test_info_flag_shows_dataset_stats(monkeypatch):
    """--info prints dataset statistics without running evaluation."""
    from click.testing import CliRunner

    from gptme.eval.swebench.main import main

    fake_instances = {
        "django__django-11099": {"repo": "django/django", "version": "3.0"},
        "django__django-11100": {"repo": "django/django", "version": "3.0"},
        "flask__flask-5000": {"repo": "pallets/flask", "version": "2.0"},
    }
    monkeypatch.setattr(
        "gptme.eval.swebench.main.load_instances",
        lambda *a, **kw: fake_instances,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["--info"])
    assert result.exit_code == 0
    assert "Total instances: 3" in result.output
    assert "django/django" in result.output
    assert "pallets/flask" in result.output
    # Should NOT try to run evaluation
    assert "Running SWE-bench evaluation" not in result.output


def test_info_flag_with_instance_filter(monkeypatch):
    """--info -i filters to specific instances."""
    from click.testing import CliRunner

    from gptme.eval.swebench.main import main

    fake_instances = {
        "django__django-11099": {"repo": "django/django", "version": "3.0"},
        "flask__flask-5000": {"repo": "pallets/flask", "version": "2.0"},
    }
    monkeypatch.setattr(
        "gptme.eval.swebench.main.load_instances",
        lambda *a, **kw: fake_instances,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["--info", "-i", "django__django-11099"])
    assert result.exit_code == 0
    assert "Total instances: 1" in result.output
    assert "django__django-11099" in result.output
    assert "flask__flask-5000" not in result.output


def test_info_flag_missing_instance_warns(monkeypatch):
    """--info -i warns when instance ID is not found."""
    from click.testing import CliRunner

    from gptme.eval.swebench.main import main

    fake_instances = {
        "django__django-11099": {"repo": "django/django", "version": "3.0"},
    }
    monkeypatch.setattr(
        "gptme.eval.swebench.main.load_instances",
        lambda *a, **kw: fake_instances,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["--info", "-i", "nonexistent__repo-99999"])
    assert result.exit_code == 0
    assert "not found" in result.output
