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


def test_get_prompt_instructions_only():
    """Test instructions-only mode produces minimal context."""
    prompt_msgs = get_prompt(
        get_tools(), prompt="full", context_mode="instructions-only"
    )
    combined_content = "\n\n".join(msg.content for msg in prompt_msgs)

    # Instructions-only should be much smaller than full
    full_msgs = get_prompt(get_tools(), prompt="full", context_mode="full")
    full_content = "\n\n".join(msg.content for msg in full_msgs)

    # Instructions-only should be significantly smaller
    instructions_tokens = len_tokens(combined_content, "gpt-4")
    full_tokens = len_tokens(full_content, "gpt-4")
    assert (
        instructions_tokens < full_tokens * 0.5
    ), f"Instructions-only ({instructions_tokens}) should be <50% of full ({full_tokens})"


def test_get_prompt_selective_tools():
    """Test selective mode with tools included."""
    # With tools
    with_tools = get_prompt(
        get_tools(),
        prompt="full",
        context_mode="selective",
        context_include=["tools"],
    )
    with_tools_content = "\n\n".join(msg.content for msg in with_tools)

    # Without tools
    without_tools = get_prompt(
        get_tools(),
        prompt="full",
        context_mode="selective",
        context_include=[],
    )
    without_tools_content = "\n\n".join(msg.content for msg in without_tools)

    # With tools should have more content
    assert len_tokens(with_tools_content, "gpt-4") > len_tokens(
        without_tools_content, "gpt-4"
    )


def test_get_prompt_selective_components():
    """Test selective mode filters components correctly."""
    # Empty selective should be minimal
    empty_selective = get_prompt(
        get_tools(),
        prompt="full",
        context_mode="selective",
        context_include=[],
    )

    # Should have at least one message (core prompt)
    assert len(empty_selective) >= 1

    # Full mode should have more content
    full_mode = get_prompt(get_tools(), prompt="full", context_mode="full")
    assert len(full_mode) >= len(empty_selective)
