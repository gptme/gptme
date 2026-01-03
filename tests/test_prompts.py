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


def test_glob_path_traversal_protection(tmp_path):
    """Test that glob patterns cannot traverse outside the workspace.

    Issue #1036 Finding #2: Glob patterns like '../../etc/passwd' should be
    rejected to prevent path traversal attacks via gptme.toml configuration.
    """
    from gptme.prompts import prompt_workspace

    # Create a temp workspace with a file
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Test")

    # Create a file outside workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret data")

    # Create a gptme.toml with path traversal attempt
    (workspace / "gptme.toml").write_text(
        """
[prompt]
files = ["../outside/secret.txt", "README.md"]
"""
    )

    # Get workspace content
    msgs = list(prompt_workspace(workspace))
    content = "\n".join(msg.content for msg in msgs)

    # Should include README but NOT secret file
    assert "# Test" in content, "README.md should be included"
    assert "secret data" not in content, "secret.txt should be blocked (path traversal)"
