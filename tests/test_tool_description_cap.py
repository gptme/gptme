"""Provider-independent cap on tool descriptions.

Regression: the 1024-char cap only existed in the two OpenAI request paths
(#1697/#1700), so an oversized description was capped on OpenAI but unbounded
everywhere else — including the system-prompt tool renderer, which runs for
every provider. Descriptions are attacker-controlled text when a tool comes
from an MCP server, so the cap belongs at the shared rendering/serialization
layer, not per provider.
"""

from gptme.llm.llm_anthropic import _spec2tool as _anthropic_spec2tool
from gptme.llm.llm_openai import _spec2tool as _openai_spec2tool
from gptme.llm.models.types import ModelMeta
from gptme.llm.openai_responses import _tool_spec_to_responses_tool
from gptme.tools.base import (
    MAX_TOOL_DESCRIPTION_LENGTH,
    ToolFormat,
    ToolSpec,
    truncate_tool_description,
)

LONG = "A" * (MAX_TOOL_DESCRIPTION_LENGTH + 500)


def _openai_model() -> ModelMeta:
    return ModelMeta(provider="openai", model="gpt-4o", context=4096)


def _long_desc_spec() -> ToolSpec:
    """A tool whose description is far past the cap, as an MCP server can supply."""
    return ToolSpec(name="mcp_demo", desc=LONG)


class TestTruncateToolDescription:
    def test_short_description_unchanged(self):
        assert truncate_tool_description("short", "demo") == "short"

    def test_at_limit_unchanged(self):
        exact = "B" * MAX_TOOL_DESCRIPTION_LENGTH
        assert truncate_tool_description(exact, "demo") == exact

    def test_long_description_truncated_to_limit(self):
        result = truncate_tool_description(LONG, "demo")
        assert len(result) == MAX_TOOL_DESCRIPTION_LENGTH
        assert result == LONG[:MAX_TOOL_DESCRIPTION_LENGTH]

    def test_custom_limit(self):
        assert truncate_tool_description(LONG, "demo", limit=10) == LONG[:10]


class TestOpenAISchemaCap:
    def test_tool_format_capped(self):
        result = _openai_spec2tool(_long_desc_spec(), _openai_model())
        assert len(result["function"]["description"]) <= MAX_TOOL_DESCRIPTION_LENGTH

    def test_responses_format_capped(self):
        result = _tool_spec_to_responses_tool(_long_desc_spec())
        assert len(result["description"]) <= MAX_TOOL_DESCRIPTION_LENGTH


class TestAnthropicSchemaCap:
    def test_long_instructions_capped(self):
        spec = ToolSpec(name="demo", desc="A tool", instructions=LONG)
        result = _anthropic_spec2tool(spec)
        assert len(result["description"]) <= MAX_TOOL_DESCRIPTION_LENGTH


class TestSystemPromptRendererCap:
    def test_tool_format_capped(self):
        prompt = _long_desc_spec().get_tool_prompt(examples=False, tool_format="tool")
        assert LONG not in prompt
        assert LONG[:MAX_TOOL_DESCRIPTION_LENGTH] in prompt

    def test_markdown_format_capped(self):
        prompt = _long_desc_spec().get_tool_prompt(
            examples=False, tool_format="markdown"
        )
        assert LONG not in prompt

    def test_xml_format_capped(self):
        prompt = _long_desc_spec().get_tool_prompt(examples=False, tool_format="xml")
        assert LONG not in prompt
        assert LONG[:MAX_TOOL_DESCRIPTION_LENGTH] in prompt

    def test_short_desc_not_truncated(self):
        spec = ToolSpec(name="demo", desc="Executes shell commands")
        formats: tuple[ToolFormat, ...] = ("tool", "markdown", "xml")
        for tool_format in formats:
            prompt = spec.get_tool_prompt(examples=False, tool_format=tool_format)
            assert "Executes shell commands" in prompt
