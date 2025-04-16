import pytest
from gptme.prompts import prompt_tools
from gptme.tools import ToolFormat, init_tools


@pytest.mark.parametrize(
    "tool_format, example, expected, not_expected",
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
                "the `shell` tool",
                "aliases",
                """@shell: {
  "command": "cat file.txt"
}""",
                "### Examples",
            ],
            [],
        ),
        (
            "tool",
            False,
            [
                "the `shell` tool",
                "aliases",
            ],
            [
                """@shell: {
  "command": "cat file.txt"
}""",
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
    tools = init_tools(allowlist=["shell", "read"])
    prompt = next(prompt_tools(tools, tool_format, example)).content

    for expect in expected:
        assert expect in prompt

    for not_expect in not_expected:
        assert not_expect not in prompt
