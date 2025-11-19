import pytest
from click.testing import CliRunner

from gptme.config import get_config
from gptme.eval import execute, tests
from gptme.eval.agents import GPTMe
from gptme.eval.main import main


def _detect_model():
    # detect which model is configured
    # TODO: isn't there already a get_default_model() helper?
    config = get_config()
    if model := config.get_env("MODEL"):
        return model
    elif config.get_env("OPENAI_API_KEY"):
        return "openai"
    elif config.get_env("ANTHROPIC_API_KEY"):
        return "anthropic"
    else:
        pytest.skip("No API key found for OpenAI or Anthropic")


@pytest.mark.slow
@pytest.mark.requires_api
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
