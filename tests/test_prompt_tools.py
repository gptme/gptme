import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.prompts import (
    _xml_section,
    prompt_gptme,
    prompt_project,
    prompt_systeminfo,
    prompt_timeinfo,
    prompt_tools,
    prompt_user,
)
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
                "<tools>",
                '<tool name="shell">',
                "<description>Executes shell commands",
                "<instructions>",
                "<examples>",
                "<tool-use>\n<shell>\nls\n</shell>\n</tool-use>",
                "</tool>",
                "</tools>",
            ],
            ["# Tools Overview", "## shell", "### Examples"],
        ),
        (
            "xml",
            False,
            [
                "<tools>",
                '<tool name="shell">',
                "<description>Executes shell commands",
                "</tools>",
            ],
            [
                "<tool-use>\n<shell>\nls\n</shell>\n</tool-use>",
                "<examples>",
                "# Tools Overview",
            ],
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
    assert "<examples>" in prompt
    assert "<description>Executes shell commands" in prompt


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


# --- Tests for XML-sectioned system prompt sections ---


def test_xml_section_helper():
    """_xml_section wraps content in the given tag."""
    result = _xml_section("role", "Hello world")
    assert result == "<role>\nHello world\n</role>"


def test_xml_section_strips_whitespace():
    """_xml_section strips leading/trailing whitespace from content."""
    result = _xml_section("tag", "  padded  \n")
    assert result == "<tag>\npadded\n</tag>"


def test_prompt_gptme_markdown_default():
    """Default (markdown) prompt_gptme should NOT contain XML role tags."""
    msgs = list(prompt_gptme(interactive=True))
    content = msgs[0].content
    assert "<role>" not in content
    assert "</role>" not in content
    assert "You are" in content


def test_prompt_gptme_xml_wraps_in_role():
    """XML format prompt_gptme should wrap content in <role> tags and be valid XML."""
    msgs = list(prompt_gptme(interactive=True, tool_format="xml"))
    content = msgs[0].content
    assert content.startswith("<role>")
    assert content.endswith("</role>")
    assert "You are" in content
    # Validate well-formed XML — catches unescaped <thinking> tags
    ET.fromstring(content)


def test_prompt_gptme_xml_interactive_vs_non_interactive():
    """Both interactive and non-interactive modes should be wrapped in <role>."""
    interactive_content = list(prompt_gptme(interactive=True, tool_format="xml"))[
        0
    ].content
    non_interactive_content = list(prompt_gptme(interactive=False, tool_format="xml"))[
        0
    ].content

    assert "<role>" in interactive_content
    assert "<role>" in non_interactive_content
    assert "interactive mode" in interactive_content
    assert "non-interactive mode" in non_interactive_content


def test_prompt_user_markdown():
    """Default prompt_user uses markdown headers."""
    msgs = list(prompt_user())
    assert len(msgs) == 1
    content = msgs[0].content
    assert "# About" in content
    assert "<user>" not in content


def test_prompt_user_xml():
    """XML prompt_user wraps in <user> with structured sub-tags."""
    msgs = list(prompt_user(tool_format="xml"))
    assert len(msgs) == 1
    content = msgs[0].content
    assert content.startswith("<user>")
    assert content.endswith("</user>")
    assert "<name>" in content
    assert "<about>" in content
    assert "<response-preferences>" in content
    # Should NOT have markdown headers
    assert "# About" not in content


def test_prompt_systeminfo_markdown():
    """Default prompt_systeminfo uses markdown headers."""
    msgs = list(prompt_systeminfo())
    assert len(msgs) == 1
    content = msgs[0].content
    assert "## System Information" in content
    assert "<system-info>" not in content


def test_prompt_systeminfo_xml():
    """XML prompt_systeminfo wraps in <system-info> with sub-tags."""
    msgs = list(prompt_systeminfo(tool_format="xml"))
    assert len(msgs) == 1
    content = msgs[0].content
    assert content.startswith("<system-info>")
    assert content.endswith("</system-info>")
    assert "<os>" in content
    assert "<working-directory>" in content
    assert "## System Information" not in content


def test_prompt_timeinfo_markdown():
    """Default prompt_timeinfo uses markdown."""
    msgs = list(prompt_timeinfo())
    assert len(msgs) == 1
    content = msgs[0].content
    assert "## Current Date" in content
    assert "<current-date>" not in content


def test_prompt_timeinfo_xml():
    """XML prompt_timeinfo wraps in <current-date>."""
    msgs = list(prompt_timeinfo(tool_format="xml"))
    assert len(msgs) == 1
    content = msgs[0].content
    assert content.startswith("<current-date>")
    assert content.endswith("</current-date>")
    assert "## Current Date" not in content


def test_xml_sections_no_markdown_headers():
    """When tool_format='xml', none of the standard sections should use markdown headers."""
    all_content = ""
    for gen in [
        prompt_gptme(interactive=True, tool_format="xml"),
        prompt_user(tool_format="xml"),
        prompt_systeminfo(tool_format="xml"),
        prompt_timeinfo(tool_format="xml"),
    ]:
        for msg in gen:
            all_content += msg.content + "\n"

    # No markdown headers should appear in XML mode
    assert "# About" not in all_content
    assert "## System Information" not in all_content
    assert "## Current Date" not in all_content
    # But XML tags should be present
    assert "<role>" in all_content
    assert "<user>" in all_content
    assert "<system-info>" in all_content
    assert "<current-date>" in all_content


def test_prompt_project_xml_escapes_project_info(tmp_path: Path):
    """project_info with XML special chars must be escaped in XML mode to avoid malformed output."""
    project_dir = tmp_path / "my-project"
    project_dir.mkdir()

    mock_config = MagicMock()
    mock_config.prompt = "Support C++ & Python <template> builds"

    with (
        patch("gptme.prompts.templates.get_project_git_dir", return_value=project_dir),
        patch("gptme.prompts.templates.get_project_config", return_value=mock_config),
        patch("gptme.prompts.templates.get_config") as mock_get_config,
    ):
        mock_get_config.return_value.user.prompt.project = None
        msgs = list(prompt_project(tool_format="xml"))

    assert len(msgs) == 1
    content = msgs[0].content
    # Must be valid XML (wrap in root tag since it's a fragment)
    ET.fromstring(content)
    # Raw special chars must not appear unescaped in the project_info portion
    assert "<template>" not in content
    assert "C++ &amp; Python" in content or "&amp;" in content
