import pytest

from gptme.prompts import prompt_tools
from gptme.tools import ToolFormat, clear_tools, init_tools


@pytest.mark.parametrize(
    ("tool_format", "example", "expected", "not_expected"),
    [
        (
            "markdown",
            True,
            [
                "Executes shell commands",
                "```shell\nls",
                "### Examples",
            ],
            [],
        ),
        (
            "markdown",
            False,
            "Executes shell commands",
            ["```shell\nls", "### Examples"],
        ),
        (
            "xml",
            True,
            [
                "Executes shell commands",
                "<tool-use>\n<shell>\nls\n</shell>\n</tool-use>",
                "### Examples",
            ],
            [],
        ),
        (
            "xml",
            False,
            ["Executes shell commands"],
            ["<tool-use>\n<shell>\nls\n</shell>\n</tool-use>", "### Examples"],
        ),
        (
            "tool",
            True,
            [
                "Executes shell commands",
                "### Examples",
            ],
            [],
        ),
        (
            "tool",
            False,
            [
                "Executes shell commands",
            ],
            [
                "### Examples",
            ],
        ),
    ],
    ids=[
        "Markdown with example",
        "Markdown without example",
        "XML with example",
        "XML without example",
        "Tool with example",
        "Tool without example",
    ],
)
def test_prompt_tools(tool_format: ToolFormat, example: bool, expected, not_expected):
    clear_tools()
    tools = init_tools(allowlist=["shell", "read"])
    prompt = next(prompt_tools(tools, tool_format, example)).content

    for expect in expected:
        assert expect in prompt

    for not_expect in not_expected:
        assert not_expect not in prompt


def test_prompt_tools_reasoning_model_skips_examples_native_format():
    """Reasoning models skip examples in native tool-calling format (OpenAI best practice)."""
    clear_tools()
    tools = init_tools(allowlist=["shell", "read"])

    # With native tool-calling format + reasoning model, examples should be skipped
    prompt = next(prompt_tools(tools, "tool", examples=True, model="openai/o3")).content
    assert "### Examples" not in prompt
    # Instructions should still be present
    assert "Executes shell commands" in prompt


def test_prompt_tools_reasoning_model_keeps_examples_markdown():
    """Reasoning models keep examples in markdown format (examples serve as documentation)."""
    clear_tools()
    tools = init_tools(allowlist=["shell", "read"])

    # Markdown format: examples are kept even for reasoning models
    prompt = next(
        prompt_tools(tools, "markdown", examples=True, model="openai/o3")
    ).content
    assert "### Examples" in prompt
    assert "Executes shell commands" in prompt


def test_prompt_tools_reasoning_model_keeps_examples_xml():
    """Reasoning models keep examples in xml format (examples serve as documentation)."""
    clear_tools()
    tools = init_tools(allowlist=["shell", "read"])

    # XML format: examples are kept even for reasoning models
    prompt = next(prompt_tools(tools, "xml", examples=True, model="openai/o3")).content
    assert "### Examples" in prompt
    assert "Executes shell commands" in prompt


def test_prompt_tools_non_reasoning_model_includes_examples():
    """Non-reasoning models should still get tool examples."""
    clear_tools()
    tools = init_tools(allowlist=["shell", "read"])

    prompt_without_reasoning = next(
        prompt_tools(tools, "tool", examples=True, model="openai/gpt-4o")
    ).content
    assert "### Examples" in prompt_without_reasoning


def test_prompt_tools_no_model_includes_examples():
    """When no model is specified, examples should be included by default."""
    clear_tools()
    tools = init_tools(allowlist=["shell", "read"])

    prompt_no_model = next(
        prompt_tools(tools, "tool", examples=True, model=None)
    ).content
    assert "### Examples" in prompt_no_model


def test_prompt_tools_reasoning_model_respects_explicit_no_examples():
    """When examples=False is explicitly set, reasoning model check is irrelevant."""
    clear_tools()
    tools = init_tools(allowlist=["shell", "read"])

    prompt = next(
        prompt_tools(tools, "tool", examples=False, model="openai/o3")
    ).content
    assert "### Examples" not in prompt
