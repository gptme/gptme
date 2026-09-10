"""Conditional tool docs ({% if tools: ... %}) and companion tools (requires_tools)."""

from unittest.mock import patch

import pytest

from gptme.tools import (
    clear_tools,
    get_toolchain,
    get_tools,
    init_tools,
    load_tool,
    set_session_allowlist,
)
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
        "{% if tools: %}x{% endif %}",
        "{% unknown %}",
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


def test_requires_tools_respects_explicit_allowlist():
    with pytest.raises(ValueError, match="hashline_edit.*requires.*read.*allowlist"):
        get_toolchain(["shell", "hashline_edit"])


def test_requires_tools_loads_explicitly_allowed_companion():
    tools = get_toolchain(["shell", "hashline_edit", "read"])
    names = {t.name for t in tools}
    assert "hashline_edit" in names
    assert "read" in names


def test_requires_tools_does_not_load_read_without_hashline():
    tools = get_toolchain(["shell"])
    assert "read" not in {t.name for t in tools}


def test_load_tool_loads_required_companions():
    companion = ToolSpec(name="companion", desc="companion", disabled_by_default=True)
    primary = ToolSpec(name="primary", desc="primary", requires_tools=["companion"])

    clear_tools()
    set_session_allowlist(None)
    with patch("gptme.tools.get_available_tools", return_value=[primary, companion]):
        loaded = load_tool("primary")

    assert loaded.name == "primary"
    assert {tool.name for tool in get_tools()} == {"primary", "companion"}


def test_explicit_load_can_add_required_companion_outside_session_allowlist():
    companion = ToolSpec(name="companion", desc="companion", disabled_by_default=True)
    primary = ToolSpec(name="primary", desc="primary", requires_tools=["companion"])

    clear_tools()
    set_session_allowlist(["primary"])
    with patch("gptme.tools.get_available_tools", return_value=[primary, companion]):
        loaded = load_tool("primary", allow_required=True)

    assert loaded.name == "primary"
    assert {tool.name for tool in get_tools()} == {"primary", "companion"}


def test_load_tool_rejects_required_companion_outside_allowlist():
    companion = ToolSpec(name="companion", desc="companion", disabled_by_default=True)
    primary = ToolSpec(name="primary", desc="primary", requires_tools=["companion"])

    clear_tools()
    set_session_allowlist(["primary"])
    with (
        patch("gptme.tools.get_available_tools", return_value=[primary, companion]),
        pytest.raises(ValueError, match="primary.*requires.*companion.*allowlist"),
    ):
        load_tool("primary")

    assert get_tools() == []


def test_file_tool_loads_required_companion(tmp_path):
    tool_file = tmp_path / "companion_tool.py"
    tool_file.write_text(
        "from gptme.tools import ToolSpec\n"
        "companion = ToolSpec(name='file_companion', desc='companion')\n"
        "primary = ToolSpec(name='file_primary', desc='primary', "
        "requires_tools=['file_companion'])\n"
    )

    clear_tools()
    tools = init_tools(allowlist=[str(tool_file)])

    assert {tool.name for tool in tools} == {"file_primary", "file_companion"}


def test_file_tool_can_require_explicitly_allowed_builtin(tmp_path):
    tool_file = tmp_path / "read_consumer.py"
    tool_file.write_text(
        "from gptme.tools import ToolSpec\n"
        "tool = ToolSpec(name='read_consumer', desc='consumer', "
        "requires_tools=['read'])\n"
    )

    clear_tools()
    tools = init_tools(allowlist=[str(tool_file), "read"], include_mcp=False)

    assert {tool.name for tool in tools} == {"read_consumer", "read"}


def test_file_tool_cannot_require_unlisted_builtin(tmp_path):
    tool_file = tmp_path / "read_consumer.py"
    tool_file.write_text(
        "from gptme.tools import ToolSpec\n"
        "tool = ToolSpec(name='read_consumer', desc='consumer', "
        "requires_tools=['read'])\n"
    )

    clear_tools()
    with pytest.raises(ValueError, match="read_consumer.*requires.*read.*allowlist"):
        init_tools(allowlist=[str(tool_file)], include_mcp=False)
