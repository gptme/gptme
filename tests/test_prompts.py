import pytest
from gptme.message import len_tokens
from gptme.prompts import get_prompt
from gptme.tools import get_tools, init_tools


@pytest.fixture(autouse=True)
def init():
    init_tools()


def test_get_prompt_full():
    prompt = get_prompt(get_tools(), prompt="full")
    # TODO: lower this significantly by selectively removing examples from the full prompt
    assert 500 < len_tokens(prompt.content, "gpt-4") < 5000


def test_get_prompt_short():
    prompt = get_prompt(get_tools(), prompt="short")
    # TODO: make the short prompt shorter
    assert 500 < len_tokens(prompt.content, "gpt-4") < 3000


def test_get_prompt_custom():
    prompt = get_prompt(get_tools(), prompt="Hello world!")
    assert prompt.content == "Hello world!"
