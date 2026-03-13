import os

import pytest
from click.testing import CliRunner

from gptme.config import get_config
from gptme.eval import execute, tests
from gptme.eval.agents import GPTMe
from gptme.eval.main import main
from gptme.eval.suites import suites, tests_map


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
