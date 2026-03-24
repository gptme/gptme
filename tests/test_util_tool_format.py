"""Tests for gptme.util.tool_format — tool formatting utilities.

Tests cover:
- tool_to_dict: JSON serialization of ToolSpec
- format_tool_summary: one-line tool summary with status icons
- format_tools_list: multi-tool listing with availability grouping
- format_tool_info: detailed tool information display
- format_langtags: language tag listing
"""

from typing import Any
from unittest.mock import patch

from gptme.tools.base import ToolSpec
from gptme.util.tool_format import (
    format_langtags,
    format_tool_info,
    format_tool_summary,
    format_tools_list,
    tool_to_dict,
)

# ──────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────


def _tool(
    name: str = "shell",
    desc: str = "Execute shell commands",
    available: bool = True,
    disabled_by_default: bool = False,
    block_types: list[str] | None = None,
    execute: Any = None,
    functions: list[Any] | None = None,
    commands: dict[str, Any] | None = None,
    is_mcp: bool = False,
    instructions: str = "",
    examples: str = "",
) -> ToolSpec:
    """Create a ToolSpec for testing."""
    return ToolSpec(
        name=name,
        desc=desc,
        available=available,
        disabled_by_default=disabled_by_default,
        block_types=block_types or [],
        execute=execute,
        functions=functions,
        commands=commands or {},
        is_mcp=is_mcp,
        instructions=instructions,
        examples=examples,
    )


# ──────────────────────────────────────────────
# tool_to_dict
# ──────────────────────────────────────────────


class TestToolToDict:
    def test_basic_tool(self):
        d = tool_to_dict(_tool())
        assert d["name"] == "shell"
        assert d["desc"] == "Execute shell commands"
        assert d["available"] is True
        assert d["disabled_by_default"] is False
        assert d["is_mcp"] is False

    def test_has_execute(self):
        d = tool_to_dict(_tool(execute=lambda *a: None))
        assert d["has_execute"] is True

    def test_no_execute(self):
        d = tool_to_dict(_tool(execute=None))
        assert d["has_execute"] is False

    def test_functions_list(self):
        def my_func():
            pass

        d = tool_to_dict(_tool(functions=[my_func]))
        assert d["functions"] == ["my_func"]

    def test_no_functions(self):
        d = tool_to_dict(_tool(functions=None))
        assert d["functions"] == []

    def test_commands_dict(self):
        d = tool_to_dict(_tool(commands={"run": lambda: None, "test": lambda: None}))
        assert sorted(d["commands"]) == ["run", "test"]

    def test_no_commands(self):
        d = tool_to_dict(_tool(commands=None))
        assert d["commands"] == []

    def test_block_types(self):
        d = tool_to_dict(_tool(block_types=["python", "py"]))
        assert d["block_types"] == ["python", "py"]

    def test_unavailable_tool(self):
        d = tool_to_dict(_tool(available=False))
        assert d["available"] is False

    def test_mcp_tool(self):
        d = tool_to_dict(_tool(is_mcp=True))
        assert d["is_mcp"] is True

    def test_disabled_by_default(self):
        d = tool_to_dict(_tool(disabled_by_default=True))
        assert d["disabled_by_default"] is True


# ──────────────────────────────────────────────
# format_tool_summary
# ──────────────────────────────────────────────


class TestFormatToolSummary:
    def test_available_tool_no_color(self):
        result = format_tool_summary(_tool(), use_color=False)
        assert "✓" in result
        assert "shell" in result
        assert "Execute shell commands" in result

    def test_unavailable_tool_no_color(self):
        result = format_tool_summary(_tool(available=False), use_color=False)
        assert "✗" in result

    def test_no_status(self):
        result = format_tool_summary(_tool(), show_status=False, use_color=False)
        assert "✓" not in result
        assert "✗" not in result
        assert "shell" in result

    def test_description_truncation(self):
        # 60 A's + trailing dot stripped = 60 chars > 50 limit
        result = format_tool_summary(_tool(desc="A" * 60 + "."), use_color=False)
        assert "..." in result

    def test_short_description_not_truncated(self):
        result = format_tool_summary(_tool(desc="Short desc."), use_color=False)
        assert "..." not in result
        assert "Short desc" in result

    def test_description_exactly_50_chars(self):
        result = format_tool_summary(_tool(desc="A" * 50), use_color=False)
        assert "..." not in result

    def test_description_51_chars_truncated(self):
        result = format_tool_summary(_tool(desc="A" * 51), use_color=False)
        assert "..." in result

    def test_disabled_by_default_shown(self):
        result = format_tool_summary(
            _tool(disabled_by_default=True), show_default=True, use_color=False
        )
        assert "[+]" in result

    def test_disabled_by_default_hidden(self):
        result = format_tool_summary(
            _tool(disabled_by_default=True),
            show_default=False,
            use_color=False,
        )
        assert "[+]" not in result

    def test_not_disabled_no_suffix(self):
        result = format_tool_summary(
            _tool(disabled_by_default=False),
            show_default=True,
            use_color=False,
        )
        assert "[+]" not in result

    def test_name_padded(self):
        """Name is padded to 12 chars for alignment."""
        result = format_tool_summary(
            _tool(name="ab"), show_status=False, use_color=False
        )
        assert "ab" in result

    def test_trailing_dot_stripped(self):
        result = format_tool_summary(
            _tool(desc="Does something."), show_status=False, use_color=False
        )
        assert not result.rstrip().endswith(".")

    def test_with_color(self):
        """Color mode should still produce valid output (click.style wraps)."""
        result = format_tool_summary(_tool(), use_color=True)
        assert "shell" in result


# ──────────────────────────────────────────────
# format_tools_list
# ──────────────────────────────────────────────


class TestFormatToolsList:
    def _tools(self) -> list[ToolSpec]:
        return [
            _tool(name="shell", desc="Execute shell commands"),
            _tool(name="python", desc="Run Python code"),
            _tool(name="browser", desc="Browse the web", available=False),
        ]

    def test_default_shows_available_only(self):
        result = format_tools_list(self._tools())
        assert "shell" in result
        assert "python" in result
        assert "browser" in result
        assert "Unavailable (1)" in result

    def test_show_all(self):
        result = format_tools_list(self._tools(), show_all=True)
        assert "shell" in result
        assert "browser" in result
        assert "Unavailable tools (1)" in result

    def test_compact_mode(self):
        result = format_tools_list(self._tools(), compact=True)
        assert "Tools [2/3 available]" not in result  # only with show_all
        assert "Tools [2 available]" in result

    def test_compact_with_show_all(self):
        result = format_tools_list(self._tools(), compact=True, show_all=True)
        assert "Tools [2/3 available]" in result

    def test_sorted_by_name(self):
        result = format_tools_list(self._tools(), show_all=True)
        python_pos = result.index("python")
        shell_pos = result.index("shell")
        assert python_pos < shell_pos  # alphabetical

    def test_all_available(self):
        tools = [_tool(name="a", desc="Tool A"), _tool(name="b", desc="Tool B")]
        result = format_tools_list(tools)
        assert "Unavailable" not in result

    def test_all_unavailable(self):
        tools = [_tool(name="x", desc="Tool X", available=False)]
        result = format_tools_list(tools, show_all=True)
        assert "Available tools (0)" in result
        assert "Unavailable tools (1)" in result

    def test_empty_list(self):
        result = format_tools_list([])
        assert "Available tools (0)" in result

    def test_hint_text(self):
        result = format_tools_list(self._tools())
        assert "gptme-util tools info" in result

    def test_compact_no_hint(self):
        result = format_tools_list(self._tools(), compact=True)
        assert "gptme-util" not in result

    def test_non_default_legend(self):
        tools = [_tool(name="morph", desc="Refactoring", disabled_by_default=True)]
        result = format_tools_list(tools)
        assert "[+]" in result
        assert "not loaded by default" in result

    def test_no_non_default_no_legend(self):
        tools = [_tool(name="shell", desc="Shell")]
        result = format_tools_list(tools)
        assert "not loaded by default" not in result

    def test_unavailable_hint(self):
        """When not show_all, unavailable tools are listed by name as a hint."""
        tools = [
            _tool(name="a", desc="A"),
            _tool(name="b", desc="B", available=False),
            _tool(name="c", desc="C", available=False),
        ]
        result = format_tools_list(tools)
        assert "b, c" in result
        assert "Use --all" in result


# ──────────────────────────────────────────────
# format_tool_info
# ──────────────────────────────────────────────


class TestFormatToolInfo:
    def test_basic_info(self):
        result = format_tool_info(
            _tool(name="shell", desc="Execute shell commands"),
            include_tokens=False,
        )
        assert "# shell" in result
        assert "Execute shell commands" in result
        assert "✓ available" in result

    def test_unavailable_status(self):
        result = format_tool_info(_tool(available=False), include_tokens=False)
        assert "✗ not available" in result

    def test_instructions_section(self):
        result = format_tool_info(
            _tool(instructions="Use this tool to run commands.\nBe careful."),
            include_tokens=False,
        )
        assert "## Instructions" in result
        assert "Use this tool to run commands." in result
        assert "Be careful." in result

    def test_no_instructions(self):
        result = format_tool_info(_tool(instructions=""), include_tokens=False)
        assert "## Instructions" not in result

    def test_examples_section(self):
        result = format_tool_info(
            _tool(examples="$ echo hello\nhello"),
            include_examples=True,
            include_tokens=False,
        )
        assert "## Examples" in result
        assert "echo hello" in result

    def test_examples_excluded(self):
        result = format_tool_info(
            _tool(examples="$ echo hello"),
            include_examples=False,
            include_tokens=False,
        )
        assert "## Examples" not in result

    def test_no_examples(self):
        result = format_tool_info(
            _tool(examples=""),
            include_examples=True,
            include_tokens=False,
        )
        assert "## Examples" not in result

    def test_truncation(self):
        result = format_tool_info(
            _tool(instructions="\n".join(f"Line {i}" for i in range(50))),
            include_tokens=False,
            truncate=True,
            max_lines=10,
        )
        assert "more lines" in result
        assert "Line 9" in result
        assert "Line 10" not in result

    def test_no_truncation_by_default(self):
        result = format_tool_info(
            _tool(instructions="\n".join(f"Line {i}" for i in range(50))),
            include_tokens=False,
            truncate=False,
        )
        assert "more lines" not in result
        assert "Line 49" in result

    def test_example_truncation(self):
        result = format_tool_info(
            _tool(examples="\n".join(f"Example {i}" for i in range(40))),
            include_tokens=False,
            truncate=True,
            max_lines=5,
        )
        assert "## Examples" in result
        assert "more lines" in result

    def test_token_estimates(self):
        """Token display uses len_tokens — mock to avoid model dependency."""
        tool = _tool(instructions="Some instructions", examples="Some examples")
        with patch("gptme.message.len_tokens", return_value=42):
            result = format_tool_info(tool, include_tokens=True)
        assert "Tokens:" in result
        assert "42" in result


# ──────────────────────────────────────────────
# format_langtags
# ──────────────────────────────────────────────


class TestFormatLangtags:
    def test_single_tool_single_tag(self):
        tools = [_tool(name="python", block_types=["python"])]
        result = format_langtags(tools)
        assert "python" in result
        assert "Supported language tags:" in result

    def test_tool_with_aliases(self):
        tools = [_tool(name="python", block_types=["python", "py", "python3"])]
        result = format_langtags(tools)
        assert "python" in result
        assert "aliases: py, python3" in result

    def test_multiple_tools(self):
        tools = [
            _tool(name="python", block_types=["python", "py"]),
            _tool(name="shell", block_types=["bash", "sh"]),
        ]
        result = format_langtags(tools)
        assert "python" in result
        assert "bash" in result

    def test_sorted_by_name(self):
        tools = [
            _tool(name="shell", block_types=["bash"]),
            _tool(name="python", block_types=["python"]),
        ]
        result = format_langtags(tools)
        python_pos = result.index("python")
        bash_pos = result.index("bash")
        assert python_pos < bash_pos  # python tool sorted before shell

    def test_tool_without_block_types(self):
        tools = [
            _tool(name="shell", block_types=["bash"]),
            _tool(name="read", block_types=[]),
        ]
        result = format_langtags(tools)
        assert "bash" in result
        assert "read" not in result  # no block types = not listed

    def test_empty_list(self):
        result = format_langtags([])
        assert "Supported language tags:" in result

    def test_no_aliases_no_parens(self):
        tools = [_tool(name="shell", block_types=["bash"])]
        result = format_langtags(tools)
        assert "aliases" not in result
