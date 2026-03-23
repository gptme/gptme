import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gptme.config import get_config
from gptme.eval import execute, tests
from gptme.eval.agents import Agent, GPTMe
from gptme.eval.main import main, results_to_json
from gptme.eval.run import ProcessError, SyncedDict, act_process
from gptme.eval.suites import suites, tests_map
from gptme.eval.types import CaseResult, EvalResult, ModelConfig

# importlib.import_module returns the actual module object from sys.modules,
# not the 'main' Click-command attribute exposed via gptme/eval/__init__.py.
# 'import gptme.eval.main as x' would also get the Click command via IMPORT_FROM.
_eval_main_module = importlib.import_module("gptme.eval.main")

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def test_no_duplicate_test_names():
    """Ensure all eval test names are unique across suites.

    Note: gptme.eval.suites raises ValueError at import time if a duplicate
    exists, so if this module imports successfully the runtime guard has already
    passed. This test is retained as explicit documentation and a structural
    fallback in case the runtime guard is ever removed.

    Duplicate names cause silent shadowing in tests_map (dict comprehension).
    See: cce683d25 which fixed a 'write-tests' name collision.
    """
    seen: dict[str, str] = {}
    for suite_name, suite_tests in suites.items():
        for test in suite_tests:
            name = test["name"]
            assert name not in seen, (
                f"Duplicate test name '{name}' in suite '{suite_name}' "
                f"(already in '{seen[name]}')"
            )
            seen[name] = suite_name

    # Verify tests_map has all tests (no shadowing occurred)
    total_tests = sum(len(t) for t in suites.values())
    assert len(tests_map) == total_tests, (
        f"tests_map has {len(tests_map)} entries but {total_tests} tests exist "
        f"— some names are duplicated and being shadowed"
    )


def test_suite_autodiscovery():
    """Verify auto-discovery finds all practical suites and preserves ordering."""
    # All practical suites should be discovered
    practical_suites = [name for name in suites if name.startswith("practical")]
    assert len(practical_suites) >= 14, (
        f"Expected at least 14 practical suites, got {len(practical_suites)}: {practical_suites}"
    )

    # Verify numeric ordering for the first 14 suites
    expected_prefix = ["practical"] + [f"practical{i}" for i in range(2, 15)]
    assert practical_suites[:14] == expected_prefix, (
        f"Practical suites not in expected order.\n"
        f"  Expected prefix: {expected_prefix}\n"
        f"  Got:             {practical_suites}"
    )
    # Any suites beyond 14 should also follow the same numeric pattern
    for i, name in enumerate(practical_suites[14:], start=15):
        assert name == f"practical{i}", f"Unexpected suite at position {i}: {name!r}"

    # Core suites should always be present
    for core_suite in ("basic", "init_projects", "browser"):
        assert core_suite in suites, f"Core suite '{core_suite}' missing"

    # Every suite should have at least one test
    for name, suite_tests in suites.items():
        assert len(suite_tests) > 0, f"Suite '{name}' has no tests"

    # Verify total test count matches flattened list
    total_from_suites = sum(len(t) for t in suites.values())
    assert len(tests) == total_from_suites, (
        f"Flattened tests ({len(tests)}) != sum of suites ({total_from_suites})"
    )


def test_list_tests():
    """Test that --list prints available suites and tests."""
    runner = CliRunner()
    result = runner.invoke(main, ["--list"])
    assert result.exit_code == 0
    assert "Available eval suites:" in result.output
    assert "basic" in result.output
    assert "hello *" in result.output
    assert "Total:" in result.output
    assert "Default suite:" in result.output


def test_list_tests_json():
    """Test that --list --json outputs valid JSON with suite info."""
    runner = CliRunner()
    result = runner.invoke(main, ["--list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "suites" in data
    assert "total_tests" in data
    assert "default_suite" in data
    assert len(data["suites"]) > 0
    # Check suite structure
    suite = data["suites"][0]
    assert "name" in suite
    assert "tests" in suite
    assert len(suite["tests"]) > 0
    # Check test structure
    test = suite["tests"][0]
    assert "name" in test
    assert "default" in test


def test_results_to_json():
    """Test that results_to_json produces valid, well-structured output."""
    config = ModelConfig(model="test-model", tool_format="markdown")
    results = [
        EvalResult(
            name="test-hello",
            status="success",
            results=[
                CaseResult(name="check_output", passed=True, duration=1.5),
                CaseResult(name="check_file", passed=False, duration=0.3),
            ],
            timings={"gen": 5.0, "run": 1.0, "eval": 0.5},
            gen_stdout="output",
            gen_stderr="",
            run_stdout="result",
            run_stderr="",
            log_dir=Path("/tmp/log"),
            workspace_dir=Path("/tmp/ws"),
        ),
    ]
    data = results_to_json({config: results}, commit_hash="abc123")

    assert data["commit"] == "abc123"
    assert "timestamp" in data
    assert len(data["models"]) == 1

    model_data = data["models"][0]
    assert model_data["model"] == "test-model"
    assert model_data["tool_format"] == "markdown"
    assert model_data["total"] == 1
    assert model_data["passed"] == 0  # not all cases passed
    assert model_data["pass_rate"] == 0.0

    result_data = model_data["results"][0]
    assert result_data["name"] == "test-hello"
    assert result_data["status"] == "success"
    assert result_data["passed"] is False  # one case failed
    assert len(result_data["cases"]) == 2
    assert result_data["cases"][0]["passed"] is True
    assert result_data["timings"]["gen"] == 5.0

    # Verify JSON-serializable
    json_str = json.dumps(data)
    assert json.loads(json_str) == data


def test_results_to_json_all_passing():
    """Test pass rate calculation when all cases pass."""
    config = ModelConfig(model="good-model", tool_format="tool")
    results = [
        EvalResult(
            name="test-1",
            status="success",
            results=[CaseResult(name="c1", passed=True, duration=1.0)],
            timings={"gen": 1.0, "run": 0.5, "eval": 0.1},
            gen_stdout="",
            gen_stderr="",
            run_stdout="",
            run_stderr="",
            log_dir=Path("/tmp/log"),
            workspace_dir=Path("/tmp/ws"),
        ),
        EvalResult(
            name="test-2",
            status="success",
            results=[CaseResult(name="c1", passed=True, duration=1.0)],
            timings={"gen": 1.0, "run": 0.5, "eval": 0.1},
            gen_stdout="",
            gen_stderr="",
            run_stdout="",
            run_stderr="",
            log_dir=Path("/tmp/log"),
            workspace_dir=Path("/tmp/ws"),
        ),
    ]
    data = results_to_json({config: results})
    assert data["models"][0]["pass_rate"] == 1.0
    assert data["models"][0]["passed"] == 2
    assert data["models"][0]["total"] == 2


def test_eval_module_loading(tmp_path):
    """Test that --eval-module loads and registers tests from an external module."""
    # Write a minimal eval module with a tests list
    module_file = tmp_path / "my_feature_eval.py"
    module_file.write_text(
        """\
def check_file_exists(ctx):
    return "main.py" in ctx.files

FEATURE = "my-feature"
PROMPT = "Create a main.py file."
CHECKS = [check_file_exists]
tests = [
    {
        "name": FEATURE or "spec-kit-eval",
        "files": {},
        "run": "python main.py",
        "prompt": PROMPT,
        "expect": {fn.__name__: fn for fn in CHECKS},
    }
]
"""
    )

    captured_evals: list = []

    def fake_run_evals(evals, *args, **kwargs):
        captured_evals.extend(evals)
        return {}

    runner = CliRunner()
    saved_path = list(sys.path)
    modules_before = frozenset(sys.modules)
    try:
        with (
            # Use patch.object to avoid mock._importer resolving "gptme.eval.main"
            # via getattr(gptme.eval, "main") which returns the Click command instead
            # of the submodule (gptme/eval/__init__.py overrides the attribute).
            patch.object(_eval_main_module, "run_evals", side_effect=fake_run_evals),
            runner.isolated_filesystem(),
        ):
            result = runner.invoke(
                main,
                ["--eval-module", str(module_file), "--model", "anthropic"],
                catch_exceptions=False,
            )
    finally:
        # Restore global state mutated by the module loader (sys.path / sys.modules).
        # Only remove newly added modules — clearing all of sys.modules is too
        # aggressive and can corrupt pytest's import state in xdist workers.
        sys.path[:] = saved_path
        for key in list(sys.modules):
            if key not in modules_before:
                del sys.modules[key]
    # Module loaded without error and the test was passed to run_evals
    assert result.exit_code == 0, f"Unexpected exit: {result.output}"
    assert len(captured_evals) == 1, f"Expected 1 eval, got {captured_evals}"
    assert captured_evals[0]["name"] == "my-feature"
    # Key: should NOT fail with "module must define a 'tests' list"
    assert "must define a 'tests' list" not in (result.output or "")


def _detect_model():
    # detect which model is configured (manual since init() hasn't run in tests)
    config = get_config()
    if model := config.get_env("MODEL"):
        return model
    if config.get_env("OPENAI_API_KEY"):
        return "openai"
    if config.get_env("ANTHROPIC_API_KEY"):
        return "anthropic"
    pytest.skip("No API key found for OpenAI or Anthropic")


@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.skipif(
    "glm" in os.getenv("MODEL", "").lower(),
    reason="GLM models don't reliably call the complete tool",
)
def test_eval_cli():
    model = _detect_model()
    runner = CliRunner()
    test_set = ["hello"]
    result = runner.invoke(
        main,
        [
            *test_set,
            "--model",
            model,
        ],
    )
    assert result
    assert result.exit_code == 0
    assert "correct file" in result.output
    assert "correct output" in result.output


# No idea why, but for some reason keeping this leads to better coverage than the above
@pytest.mark.slow
@pytest.mark.requires_api
@pytest.mark.skipif(
    "glm" in os.getenv("MODEL", "").lower(),
    reason="GLM models don't reliably call the complete tool",
)
def test_eval(test):
    """
    This test will be run for each eval in the tests list.
    See pytest_generate_tests() below.
    """
    provider = _detect_model()
    agent = GPTMe(provider)
    result = execute(test, agent, timeout=30, parallel=False)
    assert result.results
    assert all(case.passed for case in result.results)


# Hook to generate tests from the tests list
def pytest_generate_tests(metafunc):
    if "test" in metafunc.fixturenames:
        # for now, only run the hello-patch test (the "hello" test is unreliable with gpt-4o-mini)
        allowlist = ["hello-patch"]
        test_set, test_names = zip(
            *[(test, test["name"]) for test in tests if test["name"] in allowlist]
        )
        metafunc.parametrize("test", test_set, ids=test_names)


class TimeoutAgent(Agent):
    def act(
        self, files: dict[str, str | bytes] | None, prompt: str
    ) -> dict[str, str | bytes]:
        raise subprocess.TimeoutExpired(cmd="claude -p", timeout=7)


class SuccessAgent(Agent):
    def act(
        self, files: dict[str, str | bytes] | None, prompt: str
    ) -> dict[str, str | bytes]:
        return {"out.txt": "done"}


def test_act_process_maps_subprocess_timeout_to_timeout_result():
    sync_dict = cast(SyncedDict, {})
    agent = TimeoutAgent(model="claude-code/test")

    with patch("gptme.eval.run._graceful_killpg"):
        act_process(
            agent=agent,
            test_name="timeout-case",
            prompt="do thing",
            files={},
            sync_dict=sync_dict,
            parallel=True,
            suppress_output=True,
        )

    result = cast(ProcessError, sync_dict["result"])
    assert result["status"] == "timeout"
    assert result["duration"] >= 0
    assert result["message"]


def test_execute_docker_mode_runs_checks_in_docker_env():
    test: EvalSpec = {
        "name": "docker-check",
        "files": {},
        "prompt": "write output",
        "run": "cat out.txt",
        "expect": {"has output": lambda ctx: ctx.files.get("out.txt") == "done"},
    }
    agent = SuccessAgent(model="claude-code/test")

    with patch("gptme.eval.run.DockerExecutionEnv") as MockDockerEnv:
        env = MockDockerEnv.return_value
        env.run.return_value = ("done", "", 0)
        env.download.return_value = {"out.txt": "done"}

        result = execute(
            test=test,
            agent=agent,
            timeout=5,
            parallel=False,
            use_docker=True,
        )

    MockDockerEnv.assert_called_once()
    env.upload.assert_called_once_with({"out.txt": "done"})
    env.run.assert_called_once_with("cat out.txt")
    env.cleanup.assert_called_once()
    assert result.status == "success"
    assert all(case.passed for case in result.results)
    assert result.run_stdout == "done"
