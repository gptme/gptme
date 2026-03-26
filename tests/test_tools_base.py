"""Tests for gptme/tools/base.py — foundational tool infrastructure.

Covers: find_json_end, _codeblock_char_ranges, derive_type, callable_signature,
ToolSpec properties/methods, ToolUse formatting/parsing, get_path, load_from_file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Union

import pytest

from gptme.tools.base import (
    Parameter,
    ToolSpec,
    ToolUse,
    _codeblock_char_ranges,
    callable_signature,
    derive_type,
    find_json_end,
    get_path,
    get_tool_format,
    load_from_file,
    set_tool_format,
)

# ── find_json_end ──────────────────────────────────────────────────


class TestFindJsonEnd:
    def test_simple_object(self):
        s = '{"key": "value"}'
        assert find_json_end(s, 0) == len(s)

    def test_nested_objects(self):
        s = '{"a": {"b": {"c": 1}}}'
        assert find_json_end(s, 0) == len(s)

    def test_with_preceding_text(self):
        s = 'prefix {"key": "val"} suffix'
        assert find_json_end(s, 7) == 21

    def test_string_with_braces(self):
        s = '{"code": "if (x) { return }"}'
        assert find_json_end(s, 0) == len(s)

    def test_escaped_quotes_in_string(self):
        s = '{"msg": "say \\"hello\\""}'
        assert find_json_end(s, 0) == len(s)

    def test_empty_object(self):
        s = "{}"
        assert find_json_end(s, 0) == 2

    def test_incomplete_json(self):
        s = '{"key": "value"'
        assert find_json_end(s, 0) is None

    def test_closing_brace_before_open(self):
        s = "}"
        assert find_json_end(s, 0) is None


# ── _codeblock_char_ranges ─────────────────────────────────────────


class TestCodeblockCharRanges:
    def test_single_codeblock(self):
        content = "before\n```python\ncode\n```\nafter"
        ranges = _codeblock_char_ranges(content)
        assert len(ranges) == 1
        start, end = ranges[0]
        assert "```python" in content[start:end]
        assert "code" in content[start:end]

    def test_multiple_codeblocks(self):
        content = "```py\na\n```\ntext\n```js\nb\n```"
        ranges = _codeblock_char_ranges(content)
        assert len(ranges) == 2

    def test_no_codeblocks(self):
        assert _codeblock_char_ranges("just regular text") == []

    def test_unclosed_codeblock(self):
        content = "```python\ncode without closing"
        ranges = _codeblock_char_ranges(content)
        assert len(ranges) == 0

    def test_mismatched_fence_lengths(self):
        # ````python should match ```` not ```
        content = "````python\ncode\n````"
        ranges = _codeblock_char_ranges(content)
        assert len(ranges) == 1

    def test_fence_with_extra_text_not_closing(self):
        # A fence with text after backticks is NOT a closing fence
        content = "```python\ncode\n``` extra text\n```"
        ranges = _codeblock_char_ranges(content)
        # The first ``` opens, "``` extra text" is NOT a close (has trailing text),
        # the bare ``` is the actual close
        assert len(ranges) == 1


# ── extract_json ───────────────────────────────────────────────────


class TestExtractJson:
    def test_basic(self):
        content = '@tool(id): {"key": "val"} rest'
        json_start = content.index("{")
        json_end = find_json_end(content, json_start)
        assert json_end is not None
        result = content[json_start:json_end]
        assert result == '{"key": "val"}'


# ── derive_type ────────────────────────────────────────────────────


class TestDeriveType:
    def test_none_value(self):
        assert derive_type(None) == "None"

    def test_string_annotation(self):
        assert derive_type("MyType") == "MyType"

    def test_basic_types(self):
        assert derive_type(str) == "str"
        assert derive_type(int) == "int"
        assert derive_type(bool) == "bool"

    def test_nonetype(self):
        assert derive_type(type(None)) == "None"

    def test_literal_type(self):
        result = derive_type(Literal["a", "b", "c"])
        assert "Literal" in result
        assert '"a"' in result
        assert '"b"' in result
        assert '"c"' in result

    def test_union_type(self):
        result = derive_type(Union[str, int])  # noqa: UP007
        assert "Union" in result
        assert "str" in result
        assert "int" in result

    def test_list_instance(self):
        result = derive_type([str, int])
        assert result == "[str, int]"

    def test_generic_list(self):
        result = derive_type(list[int])
        assert "list" in result
        assert "int" in result

    def test_generic_dict(self):
        result = derive_type(dict[str, int])
        assert "dict" in result
        assert "str" in result
        assert "int" in result


# ── callable_signature ─────────────────────────────────────────────


class TestCallableSignature:
    def test_simple_function(self):
        def greet(name: str) -> str:
            return ""

        sig = callable_signature(greet)
        assert "greet" in sig
        assert "name: str" in sig
        assert "-> str" in sig

    def test_no_return_type(self):
        def no_ret(x: int):
            pass

        sig = callable_signature(no_ret)
        assert sig == "no_ret(x: int)"

    def test_multiple_args(self):
        def multi(a: str, b: int, c: bool) -> None:
            pass

        sig = callable_signature(multi)
        assert "a: str" in sig
        assert "b: int" in sig
        assert "c: bool" in sig
        assert "-> None" in sig

    def test_no_annotations(self):
        def bare():
            pass

        sig = callable_signature(bare)
        assert sig == "bare()"


# ── ToolSpec ───────────────────────────────────────────────────────


class TestToolSpec:
    @pytest.fixture
    def basic_tool(self):
        return ToolSpec(
            name="test_tool",
            desc="A test tool",
            instructions="Use this tool for testing",
        )

    @pytest.fixture
    def tool_with_execute(self):
        def execute(code, args, kwargs):
            pass

        return ToolSpec(
            name="runnable",
            desc="Runnable tool",
            execute=execute,
        )

    def test_repr(self, basic_tool):
        assert repr(basic_tool) == "ToolSpec(test_tool)"

    def test_eq_same_name(self):
        a = ToolSpec(name="x", desc="a")
        b = ToolSpec(name="x", desc="b")
        assert a == b

    def test_eq_different_name(self):
        a = ToolSpec(name="x", desc="a")
        b = ToolSpec(name="y", desc="a")
        assert a != b

    def test_eq_non_toolspec(self, basic_tool):
        assert basic_tool != "not a toolspec"

    def test_lt_by_priority(self):
        low = ToolSpec(name="a", desc="", load_priority=0)
        high = ToolSpec(name="b", desc="", load_priority=10)
        assert low < high

    def test_lt_by_name_same_priority(self):
        a = ToolSpec(name="alpha", desc="")
        b = ToolSpec(name="beta", desc="")
        assert a < b

    def test_lt_non_toolspec(self, basic_tool):
        assert basic_tool.__lt__("string") == NotImplemented

    def test_is_available_bool(self):
        available = ToolSpec(name="t", desc="", available=True)
        unavailable = ToolSpec(name="t", desc="", available=False)
        assert available.is_available is True
        assert unavailable.is_available is False

    def test_is_available_callable(self):
        tool = ToolSpec(name="t", desc="", available=lambda: True)
        assert tool.is_available is True
        tool_no = ToolSpec(name="t", desc="", available=lambda: False)
        assert tool_no.is_available is False

    def test_is_runnable(self, basic_tool, tool_with_execute):
        assert basic_tool.is_runnable is False
        assert tool_with_execute.is_runnable is True

    def test_get_instructions_basic(self, basic_tool):
        result = basic_tool.get_instructions("markdown")
        assert "Use this tool for testing" in result

    def test_get_instructions_format_override(self):
        tool = ToolSpec(
            name="t",
            desc="",
            instructions="general",
            instructions_format={"xml": "xml-specific"},
        )
        assert "xml-specific" in tool.get_instructions("xml")
        assert "general" in tool.get_instructions("markdown")

    def test_get_instructions_with_functions(self):
        def my_func(x: int) -> str:
            """Does something."""
            return ""

        tool = ToolSpec(name="t", desc="", functions=[my_func])
        result = tool.get_instructions("markdown")
        assert "my_func" in result
        assert "Does something" in result

    def test_get_functions_description(self):
        def helper(name: str) -> bool:
            """Checks a name."""
            return True

        tool = ToolSpec(name="t", desc="", functions=[helper])
        desc = tool.get_functions_description()
        assert "helper" in desc
        assert "Checks a name" in desc

    def test_get_functions_description_no_functions(self):
        tool = ToolSpec(name="t", desc="")
        assert tool.get_functions_description() == "None"

    def test_get_examples_string(self):
        tool = ToolSpec(name="t", desc="", examples="example usage")
        assert "example usage" in tool.get_examples()

    def test_get_examples_callable(self):
        tool = ToolSpec(
            name="t",
            desc="",
            examples=lambda fmt: f"example for {fmt}",
        )
        assert "example for markdown" in tool.get_examples("markdown")
        assert "example for xml" in tool.get_examples("xml")

    def test_get_tool_prompt_markdown(self, basic_tool):
        prompt = basic_tool.get_tool_prompt(examples=False, tool_format="markdown")
        assert "## test_tool" in prompt
        assert "A test tool" in prompt
        assert "Use this tool for testing" in prompt

    def test_get_tool_prompt_xml(self, basic_tool):
        prompt = basic_tool.get_tool_prompt(examples=False, tool_format="xml")
        assert '<tool name="test_tool">' in prompt
        assert "A test tool" in prompt
        assert "</tool>" in prompt

    def test_get_doc_with_instructions(self, basic_tool):
        doc = basic_tool.get_doc("Existing doc.")
        assert "Existing doc." in doc
        assert "Instructions" in doc

    def test_get_doc_no_existing(self, basic_tool):
        doc = basic_tool.get_doc()
        assert "Instructions" in doc


# ── ToolUse formatting ─────────────────────────────────────────────


class TestToolUseFormatting:
    def test_to_markdown(self):
        tu = ToolUse("shell", ["bash"], "echo hello", start=0)
        result = tu.to_output("markdown")
        assert result == "```shell bash\necho hello\n```"

    def test_to_markdown_no_args(self):
        tu = ToolUse("ipython", [], "print(1)", start=0)
        result = tu.to_output("markdown")
        assert result == "```ipython\nprint(1)\n```"

    def test_to_xml(self):
        tu = ToolUse("shell", ["bash"], "echo hello", start=0)
        result = tu.to_output("xml")
        assert "<tool-use>" in result
        assert "<shell" in result
        assert "echo hello" in result
        assert "</shell>" in result
        assert "</tool-use>" in result

    def test_to_xml_escapes_content(self):
        tu = ToolUse("shell", [], "echo '<html>'", start=0)
        result = tu.to_output("xml")
        assert "&lt;html&gt;" in result

    def test_to_xml_escapes_args(self):
        tu = ToolUse("save", ['file "name".py'], "content", start=0)
        result = tu.to_output("xml")
        # quoteattr wraps in single-quotes when the value contains double-quotes
        assert "args='file \"name\".py'" in result


# ── ToolUse._iter_from_xml ─────────────────────────────────────────


class TestToolUseXmlParsing:
    def test_gptme_format(self):
        content = """<tool-use>
<shell>
echo hello
</shell>
</tool-use>"""
        uses = list(ToolUse._iter_from_xml(content))
        assert len(uses) == 1
        assert uses[0].tool == "shell"
        assert uses[0].content is not None
        assert "echo hello" in uses[0].content

    def test_haiku_format(self):
        content = """<function_calls>
<invoke name="shell">
echo world
</invoke>
</function_calls>"""
        uses = list(ToolUse._iter_from_xml(content))
        assert len(uses) == 1
        assert uses[0].tool == "shell"
        assert uses[0].content is not None
        assert "echo world" in uses[0].content

    def test_no_xml_tags(self):
        content = "just regular text"
        uses = list(ToolUse._iter_from_xml(content))
        assert len(uses) == 0

    def test_multiple_tools_gptme_format(self):
        content = """<tool-use>
<shell>ls</shell>
<ipython>print(1)</ipython>
</tool-use>"""
        uses = list(ToolUse._iter_from_xml(content))
        assert len(uses) == 2
        names = {u.tool for u in uses}
        assert "shell" in names
        assert "ipython" in names

    def test_haiku_missing_name(self):
        content = """<function_calls>
<invoke>
no name
</invoke>
</function_calls>"""
        uses = list(ToolUse._iter_from_xml(content))
        assert len(uses) == 0


# ── ToolUse.iter_from_content (tool format) ────────────────────────


class TestToolUseToolFormat:
    def test_tool_call_parsing(self):
        content = '@shell(call-1): {"command": "echo hi"}'
        uses = list(ToolUse.iter_from_content(content, tool_format_override="tool"))
        # Should find at least the tool-format call
        tool_calls = [u for u in uses if u._format == "tool"]
        assert len(tool_calls) == 1
        assert tool_calls[0].tool == "shell"
        assert tool_calls[0].call_id == "call-1"
        assert tool_calls[0].kwargs == {"command": "echo hi"}

    def test_tool_call_inside_codeblock_skipped(self):
        content = '```example\n@shell(id-1): {"cmd": "test"}\n```'
        uses = list(ToolUse.iter_from_content(content, tool_format_override="tool"))
        tool_calls = [u for u in uses if u._format == "tool"]
        assert len(tool_calls) == 0

    def test_multiple_tool_calls(self):
        content = '@shell(c1): {"cmd": "ls"}\nsome text\n@ipython(c2): {"code": "1+1"}'
        uses = list(ToolUse.iter_from_content(content, tool_format_override="tool"))
        tool_calls = [u for u in uses if u._format == "tool"]
        assert len(tool_calls) == 2

    def test_incomplete_json_stops(self):
        content = '@shell(c1): {"cmd": "ls"'
        uses = list(ToolUse.iter_from_content(content, tool_format_override="tool"))
        tool_calls = [u for u in uses if u._format == "tool"]
        assert len(tool_calls) == 0


# ── get_path ───────────────────────────────────────────────────────


class TestGetPath:
    def test_from_args(self):
        path = get_path("content", ["save test.py"], None)
        assert path == Path("test.py")

    def test_from_args_with_space(self):
        path = get_path("content", ["save my file.py"], None)
        assert path == Path("my file.py")

    def test_from_kwargs(self):
        path = get_path(None, None, {"path": "/tmp/test.py"})
        assert path == Path("/tmp/test.py")

    def test_no_path_raises(self):
        with pytest.raises(ValueError, match="No filename"):
            get_path(None, None, None)

    def test_tilde_expansion(self):
        path = get_path(None, None, {"path": "~/test.py"})
        assert "~" not in str(path)

    def test_append_prefix(self):
        path = get_path("content", ["append log.txt"], None)
        assert path == Path("log.txt")

    def test_patch_prefix(self):
        path = get_path("diff content", ["patch src/main.py"], None)
        assert path == Path("src/main.py")


# ── load_from_file ─────────────────────────────────────────────────


class TestLoadFromFile:
    def test_nonexistent_file(self):
        with pytest.raises(ValueError, match="does not exist"):
            load_from_file(Path("/nonexistent/tool.py"))

    def test_directory_not_file(self, tmp_path):
        with pytest.raises(ValueError, match="not a file"):
            load_from_file(tmp_path)

    def test_non_py_extension(self, tmp_path):
        txt_file = tmp_path / "tool.txt"
        txt_file.write_text("x = 1")
        with pytest.raises(ValueError, match="must be a .py file"):
            load_from_file(txt_file)

    def test_load_valid_tool(self, tmp_path):
        tool_file = tmp_path / "my_tool.py"
        tool_file.write_text(
            """
from gptme.tools.base import ToolSpec

my_tool = ToolSpec(name="my_test_tool", desc="A test tool")
"""
        )
        tools = load_from_file(tool_file)
        assert len(tools) == 1
        assert tools[0].name == "my_test_tool"

    def test_load_file_no_tools(self, tmp_path):
        tool_file = tmp_path / "empty_tool.py"
        tool_file.write_text("x = 42\n")
        tools = load_from_file(tool_file)
        assert len(tools) == 0

    def test_load_multiple_tools(self, tmp_path):
        tool_file = tmp_path / "multi_tool.py"
        tool_file.write_text(
            """
from gptme.tools.base import ToolSpec

tool_a = ToolSpec(name="tool_a", desc="First")
tool_b = ToolSpec(name="tool_b", desc="Second")
"""
        )
        tools = load_from_file(tool_file)
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "tool_a" in names
        assert "tool_b" in names


# ── set/get tool_format ────────────────────────────────────────────


@pytest.fixture()
def restore_tool_format():
    original = get_tool_format()
    yield
    set_tool_format(original)


class TestToolFormat:
    def test_set_and_get(self, restore_tool_format):
        set_tool_format("xml")
        assert get_tool_format() == "xml"
        set_tool_format("tool")
        assert get_tool_format() == "tool"


# ── Parameter ──────────────────────────────────────────────────────


class TestParameter:
    def test_basic(self):
        p = Parameter(
            name="path", type="string", description="File path", required=True
        )
        assert p.name == "path"
        assert p.type == "string"
        assert p.required is True

    def test_defaults(self):
        p = Parameter(name="x", type="int")
        assert p.description is None
        assert p.enum is None
        assert p.required is False

    def test_with_enum(self):
        p = Parameter(name="mode", type="string", enum=["read", "write"])
        assert p.enum == ["read", "write"]


# ── ToolUse._to_params ────────────────────────────────────────────


class TestToolUseToParams:
    def test_kwargs_passthrough(self):
        tu = ToolUse("shell", None, None, kwargs={"cmd": "ls"}, start=0)
        assert tu._to_params() == {"cmd": "ls"}

    def test_no_args_no_kwargs(self):
        tu = ToolUse("shell", None, None, start=0)
        assert tu._to_params() == {}


# ── ToolSpec.get_tool_prompt edge cases ────────────────────────────


class TestToolPromptEdgeCases:
    def test_no_desc(self):
        tool = ToolSpec(name="t", desc="")
        prompt = tool.get_tool_prompt(examples=False, tool_format="markdown")
        assert "## t" in prompt
        assert "Description" not in prompt

    def test_no_instructions(self):
        tool = ToolSpec(name="t", desc="desc")
        prompt = tool.get_tool_prompt(examples=False, tool_format="markdown")
        assert "Instructions" not in prompt

    def test_xml_no_desc(self):
        tool = ToolSpec(name="t", desc="")
        prompt = tool.get_tool_prompt(examples=False, tool_format="xml")
        assert "<description>" not in prompt
        assert '<tool name="t">' in prompt
