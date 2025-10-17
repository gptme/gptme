import os

import pytest
from gptme.message import len_tokens
from gptme.prompts import get_prompt
from gptme.tools import get_tools, init_tools


@pytest.fixture(autouse=True)
def init():
    init_tools()


# Extra allowed tokens for user config
user_config_size = 2000 if "CI" not in os.environ else 0


def test_get_prompt_full():
    prompt_msgs = get_prompt(get_tools(), prompt="full")
    # Combine all message contents for token counting
    combined_content = "\n\n".join(msg.content for msg in prompt_msgs)

    # TODO: lower this significantly by selectively removing examples from the full prompt
    # Note: Hook system documentation increased the prompt size, should optimize later
    assert 500 < len_tokens(combined_content, "gpt-4") < 8000 + user_config_size


def test_get_prompt_short():
    prompt_msgs = get_prompt(get_tools(), prompt="short")
    # Combine all message contents for token counting
    combined_content = "\n\n".join(msg.content for msg in prompt_msgs)

    # TODO: make the short prompt shorter
    # Note: Lesson system additions increased prompt size slightly
    assert 500 < len_tokens(combined_content, "gpt-4") < 3500 + user_config_size


def test_get_prompt_custom():
    prompt_msgs = get_prompt([], prompt="Hello world!")
    assert len(prompt_msgs) == 1
    assert prompt_msgs[0].content == "Hello world!"
