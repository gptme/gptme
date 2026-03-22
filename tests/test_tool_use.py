from typing import cast

import json_repair
import pytest

from gptme.tools import init_tools
from gptme.tools.base import (
    ToolFormat,
    ToolUse,
    extract_json,
    set_tool_format,
    toolcall_re,
)


@pytest.mark.parametrize(
    ("tool_format", "args", "content", "kwargs", "expected"),
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


def test_tool_use_read_content_not_mapped_to_start_line():
    """Content should not leak into unrelated parameters like start_line.

    Regression test for https://github.com/gptme/gptme/issues/1645
    """
    init_tools(allowlist=["read"])

    # Content should NOT become start_line
    result = ToolUse("read", ["hello.py"], "hello.py")._to_params()
    assert result == {"path": "hello.py"}
    assert "start_line" not in result

    # Empty content should also not leak
    result = ToolUse("read", ["hello.py"], "")._to_params()
    assert result == {"path": "hello.py"}

    # With explicit start_line and end_line in args, they should map correctly
    result = ToolUse("read", ["hello.py", "5", "9"], "")._to_params()
    assert result == {"path": "hello.py", "start_line": "5", "end_line": "9"}


def test_tool_use_save_content_maps_correctly():
    """Content should correctly map to the content parameter for save tool.

    Ensures the fix for #1645 doesn't break tools where content IS the body.
    """
    init_tools(allowlist=["save"])

    result = ToolUse("save", ["hello.py"], 'print("Hello")')._to_params()
    assert result == {"path": "hello.py", "content": 'print("Hello")'}


def test_tool_use_shell_content_maps_to_command():
    """Content should map to command when it's the only parameter."""
    init_tools(allowlist=["shell"])

    result = ToolUse("shell", [], "ls -la")._to_params()
    assert result == {"command": "ls -la"}


@pytest.mark.parametrize(
    ("content", "expected_tool", "expected_json"),
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
        '```\n@shell(uid): {"cmd": "ls"}\n```',  # inside codeblock
    ],
)
def test_toolcall_regex_invalid(content):
    # No ToolUse should be created for invalid content
    set_tool_format("tool")
    tool_uses = list(ToolUse.iter_from_content(content))
    assert len(tool_uses) == 0


def test_toolcall_inside_codeblock_skipped():
    """Tool calls inside markdown fenced code blocks should not be parsed."""
    set_tool_format("tool")

    # Single tool call inside a codeblock
    content = '```\n@shell(uid): {"cmd": "ls"}\n```'
    tool_uses = list(ToolUse.iter_from_content(content))
    assert len(tool_uses) == 0

    # Tool call inside a codeblock with language tag
    content = '```example\n@shell(uid): {"cmd": "ls"}\n```'
    tool_uses = list(ToolUse.iter_from_content(content))
    assert len(tool_uses) == 0

    # Real tool call outside codeblock should still work
    content = '@shell(uid): {"cmd": "ls"}'
    tool_uses = list(ToolUse.iter_from_content(content))
    assert len(tool_uses) == 1

    # Mix: tool call in codeblock + real tool call outside
    content = (
        '```\n@shell(uid1): {"cmd": "example"}\n```\n@shell(uid2): {"cmd": "real"}'
    )
    tool_uses = list(ToolUse.iter_from_content(content))
    assert len(tool_uses) == 1
    assert tool_uses[0].kwargs == {"cmd": "real"}
    assert tool_uses[0].call_id == "uid2"


def test_parse_tool_use_ipython_kimi_k2():
    """Kimi K2 thinking uses this callstyle"""
    set_tool_format("tool")
    call = '@ipython(ipython:0): {"code": "2 + 2"}'
    tooluses = list(ToolUse.iter_from_content(call))
    assert tooluses

    call = """@ipython(functions.ipython:0): {"code": "import numpy as np\nimport pandas as pd\n\n# Create a simple dataset\ndata = {\n    'name': ['Alice', 'Bob', 'Charlie', 'Diana'],\n    'age': [25, 30, 35, 28],\n    'salary': [50000, 60000, 75000, 55000]\n}\ndf = pd.DataFrame(data)\n\n# Display the dataframe\nprint(\"Employee Data:\")\nprint(df)\n\n# Calculate some statistics\nprint(\"\\nStatistics:\")\nprint(f\"Average age: {df['age'].mean()}\")\nprint(f\"Average salary: ${df['salary'].mean():,.2f}\")\nprint(f\"Salary range: ${df['salary'].min():,.0f} - ${df['salary'].max():,.0f}\")"}"""
    tooluses = list(ToolUse.iter_from_content(call))
    assert tooluses


def test_no_tooluse_repr_in_examples():
    """ToolUse objects used directly in f-strings (without .to_output()) produce
    repr strings containing 'ToolUse(...)' which leak into the system prompt.

    This test ensures all tool examples render as proper tool call syntax,
    not raw Python repr strings.

    Regression test for https://github.com/gptme/gptme/issues/1645
    (discovered via @TimeToBuildBob's mention in the issue)
    """
    tools = init_tools()
    for tool in tools:
        for tool_format in ("markdown", "xml", "tool"):
            tool_format_typed = cast(ToolFormat, tool_format)
            examples = tool.get_examples(tool_format=tool_format_typed)
            if examples:
                assert "ToolUse(" not in examples, (
                    f"Tool '{tool.name}' examples contain raw ToolUse repr "
                    f"(format={tool_format!r}). Use .to_output() in the f-string."
                )


def test_parse_multiple_tool_calls():
    """Multiple tool calls in a single message (e.g., from native OpenAI tool calling)."""
    set_tool_format("tool")
    # Simulate two tool calls in one assistant message, as would come from
    # OpenAI's native tool calling API when the model makes multiple calls.
    content = '@shell(call_id1): {"cmd": "ls"}\n@shell(call_id2): {"cmd": "pwd"}'
    tooluses = list(ToolUse.iter_from_content(content))
    assert len(tooluses) == 2
    assert tooluses[0].call_id == "call_id1"
    assert tooluses[1].call_id == "call_id2"
    assert tooluses[0].kwargs == {"cmd": "ls"}
    assert tooluses[1].kwargs == {"cmd": "pwd"}
    # Check ordering by start position
    assert tooluses[0].start is not None and tooluses[1].start is not None
    assert tooluses[0].start < tooluses[1].start
