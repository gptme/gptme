import json_repair
import pytest

from gptme.tools import init_tools
from gptme.tools.base import (
    ToolUse,
    extract_json,
    set_tool_format,
    toolcall_re,
)


@pytest.mark.parametrize(
    "tool_format, args, content, kwargs, expected",
    [
        (
            "markdown",
            ["test.txt"],
            "patch",
            None,
            """```patch test.txt
patch
```""",
        ),
        (
            "markdown",
            ["test.txt"],
            "patch",
            {"patch": "patch", "path": "test.txt"},
            """```patch test.txt
patch
```""",
        ),
        (
            "xml",
            ["test.txt"],
            "patch",
            None,
            """<tool-use>
<patch args="test.txt">
patch
</patch>
</tool-use>""",
        ),
        (
            "tool",
            ["test.txt"],
            "...",
            None,
            """@patch: {
  "path": "test.txt",
  "patch": "..."
}""",
        ),
        (
            "tool",
            ["test.txt"],
            "patch",
            {"path": "test_kwargs.txt", "patch": "..."},
            """@patch: {
  "path": "test_kwargs.txt",
  "patch": "..."
}""",
        ),
    ],
)
def test_tool_use_output_patch(tool_format, args, content, kwargs, expected):
    init_tools(allowlist=["patch"])

    result = ToolUse("patch", args, content, kwargs).to_output(tool_format)

    assert result == expected


@pytest.mark.parametrize(
    "content, expected_tool, expected_json",
    [
        (
            '@tool(tool_uid): {"param": "value"}',
            "tool",
            '{"param": "value"}',
        ),
        (
            '@tool(tool_uid): {"missing": "comma" "key": "value"}',  # json_repair can fix this
            "tool",
            '{"missing": "comma", "key": "value"}',
        ),
        (
            "@tool(tool_uid): {invalid json}",  # json_repair can handle this
            "tool",
            "{}",
        ),
        (
            '@tool(tool_uid): {\n  "param": "value"\n}',
            "tool",
            '{\n  "param": "value"\n}',
        ),
        (
            '@tool(tool_uid): {\n  "param": "value with\nnewline",\n  "another": "value"\n}',
            "tool",
            '{\n  "param": "value with\nnewline",\n  "another": "value"\n}',
        ),
        (
            '@tool(tool_uid): {"param": {"nested": "value"}}',
            "tool",
            '{"param": {"nested": "value"}}',
        ),
        (
            '@tool(tool_uid): {"param": {"deeply": {"nested": "value"}}}',
            "tool",
            '{"param": {"deeply": {"nested": "value"}}}',
        ),
        (
            '@tool(tool_uid): {"text": "a string with } brace"}',
            "tool",
            '{"text": "a string with } brace"}',
        ),
        (
            '@tool(tool_uid): {"text": "a string with \\"quote\\" and } brace"}',
            "tool",
            '{"text": "a string with \\"quote\\" and } brace"}',
        ),
        (
            '@save(tool_uid): {"path": "hello.py", "content": "def main():\n    print(\\"Hello, World!\\")\n    \nif __name__ == \\"__main__\\":\n    main()"}',
            "save",
            '{"path": "hello.py", "content": "def main():\n    print(\\"Hello, World!\\")\n    \nif __name__ == \\"__main__\\":\n    main()"}',
        ),
    ],
)
def test_toolcall_regex(content, expected_tool, expected_json):
    match = toolcall_re.search(content)
    assert match is not None
    assert match.group(1) == expected_tool
    json_str = extract_json(content, match)
    assert json_str is not None
    # Parse both strings with json_repair to compare structure
    expected_dict = json_repair.loads(expected_json)
    actual_dict = json_repair.loads(json_str)
    assert actual_dict == expected_dict


@pytest.mark.parametrize(
    "content",
    [
        "some text @tool: {'param': 'value'}",  # leading characters
        "@tool: {",  # incomplete JSON
        "  @tool: {'param': 'value'}",  # leading whitespace
        '@tool: {"unclosed": "string}',  # unclosed string
        '@tool: {"unclosed": {',  # unclosed nested object
        '@tool: {"mismatched": "quote\'}',  # mismatched quotes
        # TODO: fix these
        # "```\n@tool: {'param': 'value'}\n```",  # inside codeblock
    ],
)
def test_toolcall_regex_invalid(content):
    # No ToolUse should be created for invalid content
    set_tool_format("tool")
    tool_uses = list(ToolUse.iter_from_content(content))
    assert len(tool_uses) == 0


def test_parse_tool_use_ipython_kimi_k2():
    """Kimi K2 thinking uses this callstyle"""
    set_tool_format("tool")
    call = '@ipython(ipython:0): {"code": "2 + 2"}'
    tooluses = list(ToolUse.iter_from_content(call))
    assert tooluses

    call = """@ipython(functions.ipython:0): {"code": "import numpy as np\nimport pandas as pd\n\n# Create a simple dataset\ndata = {\n    'name': ['Alice', 'Bob', 'Charlie', 'Diana'],\n    'age': [25, 30, 35, 28],\n    'salary': [50000, 60000, 75000, 55000]\n}\ndf = pd.DataFrame(data)\n\n# Display the dataframe\nprint(\"Employee Data:\")\nprint(df)\n\n# Calculate some statistics\nprint(\"\\nStatistics:\")\nprint(f\"Average age: {df['age'].mean()}\")\nprint(f\"Average salary: ${df['salary'].mean():,.2f}\")\nprint(f\"Salary range: ${df['salary'].min():,.0f} - ${df['salary'].max():,.0f}\")"}"""
    tooluses = list(ToolUse.iter_from_content(call))
    assert tooluses
