"""Read-only profile boundaries for built-in writers and dynamic loads."""

from unittest.mock import Mock

import pytest

from gptme.tools import (
    clear_tools,
    get_tools,
    init_tools,
    load_tool,
    restrict_tools_for_read_only,
)
from gptme.tools.base import ToolFunction, ToolSpec

WRITERS = (
    "save",
    "patch",
    "append",
    "shell",
    "ipython",
    "computer",
    "tmux",
    "hashline_edit",
    "morph",
    "patch_many",
    "convert",
    "memory",
    "watch",
    "subagent",
    "gh",
    "mcp",
)


@pytest.mark.parametrize(
    ("tool_name", "denied"),
    [
        ("browser", ("save_browser_state", "screenshot_url", "pdf_to_images")),
        ("rag", ("rag_index", "rag_index_conversations")),
    ],
)
def test_read_only_removes_file_writing_functions(
    tool_name: str, denied: tuple
) -> None:
    def safe_read() -> None:
        pass

    def write_file() -> None:
        pass

    functions = [ToolFunction.from_callable(safe_read)]
    for name in denied:
        # Filtering follows the callable name, as does direct-call dispatch.
        def writer() -> None:
            write_file()

        writer.__name__ = name
        functions.append(ToolFunction.from_callable(writer))
    tool = ToolSpec(name=tool_name, desc="Mixed read/write", functions=functions)
    restricted = restrict_tools_for_read_only([tool])
    assert [f.fn.__name__ for f in restricted[0].functions or []] == ["safe_read"]
    assert len(tool.functions or []) == len(denied) + 1


@pytest.fixture(autouse=True)
def clean_tools():
    clear_tools()
    yield
    clear_tools()


@pytest.mark.parametrize("name", WRITERS)
def test_read_only_drops_builtin_writer(name: str) -> None:
    tools = [ToolSpec(name="read", desc="Read"), ToolSpec(name=name, desc="Write")]
    assert [tool.name for tool in restrict_tools_for_read_only(tools)] == ["read"]


@pytest.mark.parametrize("name", WRITERS)
@pytest.mark.parametrize("allow_required", [False, True])
def test_read_only_dynamic_load_rejects_writer_before_init(
    name: str, allow_required: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_tools(["read"], read_only=True, include_mcp=False)
    initialize = Mock(side_effect=AssertionError("denied tool must not initialize"))
    monkeypatch.setattr("gptme.tools._init_single_tool", initialize)
    with pytest.raises(ValueError, match="not allowed in a read-only session"):
        load_tool(name, allow_required=allow_required)
    initialize.assert_not_called()
    assert [tool.name for tool in get_tools()] == ["read"]


def test_read_only_dynamic_load_rejects_denied_companion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_tools(["read"], read_only=True, include_mcp=False)
    tools = [
        ToolSpec(name="custom", desc="Needs shell", requires_tools=["shell"]),
        ToolSpec(name="shell", desc="Shell"),
    ]
    monkeypatch.setattr("gptme.tools.get_available_tools", lambda: tools)
    initialize = Mock(side_effect=AssertionError("closure must be checked before init"))
    monkeypatch.setattr("gptme.tools._init_single_tool", initialize)
    with pytest.raises(ValueError, match="not allowed in a read-only session"):
        load_tool("custom", allow_required=True)
    initialize.assert_not_called()
    assert [tool.name for tool in get_tools()] == ["read"]


def test_read_only_startup_drops_additional_writers() -> None:
    tools = init_tools(
        ["read", "tmux", "hashline_edit", "patch_many", "watch", "memory"],
        read_only=True,
        include_mcp=False,
    )
    assert [tool.name for tool in tools] == ["read"]


def test_read_only_browser_direct_calls_exclude_file_writers() -> None:
    from gptme.tools import with_direct_function_calls
    from gptme.tools.browser import tool as browser

    tools = with_direct_function_calls(restrict_tools_for_read_only([browser]))
    exposed = {f.fn.__name__ for f in tools[0].functions or []}
    assert {"read_url", "search", "snapshot_url"} <= exposed
    assert not exposed & {"save_browser_state", "screenshot_url", "pdf_to_images"}
    assert not tools[0].is_function_only


def test_read_only_startup_does_not_initialize_denied_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gptme.tools

    initialize = Mock(wraps=gptme.tools._init_single_tool)
    monkeypatch.setattr(gptme.tools, "_init_single_tool", initialize)
    init_tools(["read", "shell", "watch"], read_only=True, include_mcp=False)
    assert [call.args[0].name for call in initialize.call_args_list] == ["read"]
