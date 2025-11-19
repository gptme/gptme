"""Test XML tool format parsing for both gptme and Haiku formats."""

from gptme.tools.base import ToolUse


def test_gptme_xml_format():
    """Test original gptme XML format: <tool-use><toolname>...</toolname></tool-use>"""
    content = """
<tool-use>
<complete>
</complete>
</tool-use>
"""
    tools = list(ToolUse._iter_from_xml(content))
    assert len(tools) == 1
    assert tools[0].tool == "complete"
    assert tools[0].content == ""


def test_haiku_xml_format():
    """Test Haiku 4.5 XML format: <function_calls><invoke name="toolname">...</invoke></function_calls>"""
    content = """
<function_calls>
<invoke name="complete">
</invoke>
</function_calls>
"""
    tools = list(ToolUse._iter_from_xml(content))
    assert len(tools) == 1
    assert tools[0].tool == "complete"
    assert tools[0].content == ""


def test_both_xml_formats_coexist():
    """Test that both gptme and Haiku XML formats can coexist in same content."""
    content = """
<tool-use>
<shell>
echo "test1"
</shell>
</tool-use>

<function_calls>
<invoke name="complete">
</invoke>
</function_calls>
"""
    tools = list(ToolUse._iter_from_xml(content))
    assert len(tools) == 2
    assert tools[0].tool == "shell"
    assert tools[0].content == 'echo "test1"'
    assert tools[1].tool == "complete"
    assert tools[1].content == ""


def test_haiku_format_with_content():
    """Test Haiku format with actual content."""
    content = """
<function_calls>
<invoke name="ipython">
print("Hello, world!")
</invoke>
</function_calls>
"""
    tools = list(ToolUse._iter_from_xml(content))
    assert len(tools) == 1
    assert tools[0].tool == "ipython"
    assert tools[0].content == 'print("Hello, world!")'
