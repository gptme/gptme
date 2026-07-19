"""Test XML tool format parsing for both gptme and Haiku formats."""

import pytest

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


def test_gptme_xml_format_with_angle_bracket_content():
    """Test that <toolname> content with angle-bracket tokens is not truncated.

    Regression test: etree.HTMLParser treats <filename> as a child element,
    so child.text only returns text before the first such tag. Using itertext()
    preserves the full content.
    """
    content = """
<tool-use>
<save args="/workspace/scrub.py">
#!/usr/bin/env python3
import sys

def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <filename>", file=sys.stderr)
        return 1
    print(sys.argv[1])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
</save>
</tool-use>
"""
    tools = list(ToolUse._iter_from_xml(content))
    assert len(tools) == 1
    assert tools[0].tool == "save"
    # The <filename> token causes the HTML parser to create a child element,
    # but itertext() ensures text AFTER it (in tail) is still captured.
    # The tag name itself is consumed (acceptable), but the surrounding code is preserved.
    assert tools[0].content is not None
    assert "raise SystemExit(main())" in tools[0].content


def test_haiku_xml_format_with_angle_bracket_content():
    """Test Haiku format with angle-bracket tokens in content is not truncated."""
    content = """
<function_calls>
<invoke name="save" args="/workspace/script.py">
#!/usr/bin/env python3
import sys

def usage():
    print("Usage: script.py <input> <output>")

if __name__ == "__main__":
    usage()
</invoke>
</function_calls>
"""
    tools = list(ToolUse._iter_from_xml(content))
    assert len(tools) == 1
    assert tools[0].tool == "save"
    # <input> and <output> are HTML void elements so they're consumed by the HTML parser,
    # but itertext() ensures code AFTER them is still captured (tail text preserved).
    assert tools[0].content is not None
    assert 'if __name__ == "__main__":' in tools[0].content


# --- Tests that explicitly exercise the regex fallback (lxml-independent) ---


def test_regex_fallback_gptme_format():
    """Regex fallback parses gptme XML format correctly."""
    content = """
<tool-use>
<shell>
echo "hello"
</shell>
</tool-use>
"""
    tools = list(ToolUse._iter_from_xml_regex(content))
    assert len(tools) == 1
    assert tools[0].tool == "shell"
    assert tools[0].content == 'echo "hello"'


def test_regex_fallback_haiku_format():
    """Regex fallback parses Haiku function_calls format correctly."""
    content = """
<function_calls>
<invoke name="ipython">
print("Hello, world!")
</invoke>
</function_calls>
"""
    tools = list(ToolUse._iter_from_xml_regex(content))
    assert len(tools) == 1
    assert tools[0].tool == "ipython"
    assert tools[0].content == 'print("Hello, world!")'


def test_regex_fallback_both_formats():
    """Regex fallback handles both formats coexisting in content."""
    content = """
<tool-use>
<shell>
ls -la
</shell>
</tool-use>

<function_calls>
<invoke name="complete">
</invoke>
</function_calls>
"""
    tools = list(ToolUse._iter_from_xml_regex(content))
    assert len(tools) == 2
    assert tools[0].tool == "shell"
    assert tools[0].content == "ls -la"
    assert tools[1].tool == "complete"
    assert tools[1].content == ""


def test_regex_fallback_angle_bracket_content():
    """Regex fallback preserves angle-bracket tokens in content verbatim."""
    content = """
<tool-use>
<save args="/workspace/scrub.py">
def main():
    print(f"Usage: {sys.argv[0]} <filename>")
raise SystemExit(main())
</save>
</tool-use>
"""
    tools = list(ToolUse._iter_from_xml_regex(content))
    assert len(tools) == 1
    assert tools[0].tool == "save"
    assert tools[0].content is not None
    # Regex captures raw content including <filename> tag text (better than lxml HTMLParser)
    assert "raise SystemExit(main())" in tools[0].content
    assert "<filename>" in tools[0].content


def test_regex_fallback_args_attribute():
    """Regex fallback extracts args from gptme format attributes."""
    content = """
<tool-use>
<save args="/tmp/test.py">
x = 1
</save>
</tool-use>
"""
    tools = list(ToolUse._iter_from_xml_regex(content))
    assert len(tools) == 1
    assert tools[0].args == ["/tmp/test.py"]


@pytest.mark.parametrize("parser", ["lxml", "regex"])
def test_parser_consistency(parser):
    """Both parsers yield consistent results for well-formed input."""
    from gptme.tools.base import _LXML_AVAILABLE

    if parser == "lxml" and not _LXML_AVAILABLE:
        pytest.skip("lxml not installed")

    content = """
<function_calls>
<invoke name="shell">
echo hello
</invoke>
</function_calls>
"""
    if parser == "lxml":
        tools = list(ToolUse._iter_from_xml_lxml(content))
    else:
        tools = list(ToolUse._iter_from_xml_regex(content))

    assert len(tools) == 1
    assert tools[0].tool == "shell"
    assert tools[0].content == "echo hello"
