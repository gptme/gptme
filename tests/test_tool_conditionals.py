"""Conditional tool docs ({% if tools: ... %}) and companion tools (requires_tools)."""

import pytest

from gptme.tools import get_toolchain, init_tools
from gptme.tools.base import ToolSpec, render_tool_conditionals


def test_render_all_when_loaded_is_none():
    text = "a {% if tools: read %}b{% else %}c{% endif %} d"
    assert render_tool_conditionals(text, None) == "a b d"


def test_render_inline_if_else():
    text = "use {% if tools: read %}read{% else %}shell{% endif %} here"
    assert render_tool_conditionals(text, {"read"}) == "use read here"
    assert render_tool_conditionals(text, {"shell"}) == "use shell here"


def test_render_requires_all_listed_tools():
    text = "{% if tools: read, browser %}both{% elif tools: read %}read{% endif %}"
    assert render_tool_conditionals(text, {"read", "browser"}) == "both"
    assert render_tool_conditionals(text, {"read"}) == "read"
    assert render_tool_conditionals(text, {"browser"}) == ""


def test_render_whole_line_markers_take_their_line():
    text = "- one\n{% if tools: read %}\n- two\n{% endif %}\n- three\n"
    assert render_tool_conditionals(text, {"read"}) == "- one\n- two\n- three\n"
    assert render_tool_conditionals(text, set()) == "- one\n- three\n"


def test_render_text_without_markers_is_untouched():
    assert render_tool_conditionals("plain {text}", set()) == "plain {text}"


@pytest.mark.parametrize(
    "text",
    [
        "{% if tools: a %}x",
        "x{% endif %}",
        "{% if tools: a %}{% if tools: b %}{% endif %}{% endif %}",
    ],
)
def test_render_rejects_malformed_blocks(text):
    with pytest.raises(ValueError, match="tools"):
        render_tool_conditionals(text, set())


def test_instructions_render_against_loaded_tools():
    spec = ToolSpec(
        name="probe",
        desc="probe",
        instructions="Fetch with {% if tools: read %}`read`{% else %}`shell`{% endif %}.",
    )
    init_tools(allowlist=["shell"])
    assert spec.get_instructions("markdown") == "Fetch with `shell`."
    init_tools(allowlist=["shell", "read"])
    assert spec.get_instructions("markdown") == "Fetch with `read`."


def test_vision_docs_do_not_name_read_when_read_is_off():
    init_tools(allowlist=["shell", "ipython", "vision"])
    from gptme.tools import get_tool

    vision = get_tool("vision")
    assert vision is not None
    instructions = vision.get_instructions("markdown")
    assert "`read`" not in instructions
    assert "`screenshot`" not in instructions


def test_doc_rendering_assumes_every_tool_loaded():
    spec = ToolSpec(
        name="probe",
        desc="probe",
        instructions="{% if tools: read %}after read{% endif %}",
    )
    assert "after read" in spec.get_doc("")


def test_requires_tools_loads_companion_even_if_disabled_by_default():
    tools = get_toolchain(["shell", "hashline_edit"])
    names = {t.name for t in tools}
    assert "hashline_edit" in names
    assert "read" in names, "hashline_edit requires read; loader must add it"


def test_requires_tools_does_not_load_read_without_hashline():
    tools = get_toolchain(["shell"])
    assert "read" not in {t.name for t in tools}
