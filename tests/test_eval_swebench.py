"""Tests for SWE-bench eval utilities."""

import json
import textwrap

import pytest


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


def test_multi_stage_swebench_agent_act_fails_fast(tmp_path):
    """Recovered staged runner should fail loudly instead of silently no-oping."""
    from gptme.eval.agents.swebench import SWEBenchAgent

    agent = SWEBenchAgent()
    instance = {"instance_id": "django__django-11099"}

    with pytest.raises(NotImplementedError, match="intentionally disabled"):
        agent.act(
            model="test-model",
            instance=instance,
            repo_dir="/tmp/repo",
            log_dir=str(tmp_path),
        )

    assert not (tmp_path / "swe_bench_info.json").exists()


def test_multi_stage_swebench_evaluate_instance_fails_before_repo_setup(monkeypatch):
    """The disabled staged runner should stop before repo setup or harness work."""
    from gptme.eval.agents.swebench import SWEBenchAgent

    agent = SWEBenchAgent()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("make_test_spec should not run for the disabled runner")

    monkeypatch.setattr("gptme.eval.agents.swebench.make_test_spec", fail_if_called)

    with pytest.raises(NotImplementedError, match="branch-aware eval logging"):
        agent.evaluate_instance(
            {
                "instance_id": "django__django-11099",
                "problem_statement": "Fix a Django bug",
            },
            model="test-model",
        )


def test_multi_stage_swebench_evaluate_instance_reraises_stage_disable_signal(
    monkeypatch,
    tmp_path,
):
    """Execution-phase NotImplementedError should not be swallowed by the broad catch."""
    from gptme.eval.agents.swebench import SWEBenchAgent

    agent = SWEBenchAgent()

    class DummyTestSpec:
        def setup_repo(self):
            return "/tmp/repo"

        def eval_repo(self):
            raise AssertionError("eval_repo should not run after staged runner failure")

    monkeypatch.setattr(agent, "_raise_stage_runner_unavailable", lambda: None)
    monkeypatch.setattr(
        "gptme.eval.agents.swebench.make_test_spec",
        lambda instance, repo_dir: DummyTestSpec(),
    )
    monkeypatch.setattr(
        "gptme.eval.agents.swebench.get_logs_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "gptme.eval.agents.swebench.generate_conversation_id",
        lambda prefix, logs_dir: "disabled-runner-test",
    )
    monkeypatch.setattr(
        "gptme.eval.agents.swebench.SWEBenchAgent.act",
        lambda self, **kwargs: (_ for _ in ()).throw(
            NotImplementedError("staged runner unavailable")
        ),
    )

    with pytest.raises(NotImplementedError, match="staged runner unavailable"):
        agent.evaluate_instance(
            {
                "instance_id": "django__django-11099",
                "problem_statement": "Fix a Django bug",
            },
            model="test-model",
        )


def test_run_swe_extra_exits_with_helpful_message(monkeypatch):
    """run_swe_extra should surface the staged-runner blocker without traceback spam."""
    from gptme.eval.swe_extra import run_swe_extra

    monkeypatch.setattr(run_swe_extra, "init_tools", lambda: None)
    monkeypatch.setattr(
        run_swe_extra,
        "load_top_50_easiest_task_instances",
        lambda: [{"instance_id": "django__django-11099"}],
    )

    def disabled_runner(self, instance, model, resume_dir=None):
        raise NotImplementedError("staged runner unavailable")

    monkeypatch.setattr(
        run_swe_extra.SWEBenchAgent,
        "evaluate_instance",
        disabled_runner,
    )

    with pytest.raises(SystemExit, match="staged runner unavailable"):
        run_swe_extra.main(model="test-model")
