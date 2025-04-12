from unittest.mock import patch

import pytest
from gptme.tools import (
    _discover_tools,
    get_available_tools,
    get_tool,
    get_tool_for_langtag,
    get_tools,
    has_tool,
    init_tools,
    is_supported_langtag,
)


def test_init_tools():
    init_tools()

    assert len(get_tools()) > 1


def test_init_tools_allowlist():
    init_tools(allowlist=["save"])
    assert len(get_tools()) == 1
    assert get_tools()[0].name == "save"

    init_tools(allowlist=["save", "patch"])
    assert len(get_tools()) == 2


def test_init_tools_allowlist_from_env():
    # Define the behavior for get_env based on the input key
    def mock_get_env(key, default=None):
        if key == "TOOL_ALLOWLIST":
            return "save,patch"
        return default  # Return the default value for other keys

    with patch("gptme.tools.get_config") as mock_get_config:
        # Mock the get_config function to return a mock object
        mock_config = mock_get_config.return_value
        # Mock the get_env method to return the custom_env_value
        mock_config.get_env.side_effect = mock_get_env

        init_tools()

    assert len(get_tools()) == 2


def test_init_tools_fails():
    with pytest.raises(ValueError):
        init_tools(allowlist=["save", "missing_tool"])


def test_tool_loading_with_package():
    found = _discover_tools(["gptme.tools"])

    found_names = [t.name for t in found]

    assert "save" in found_names
    assert "ipython" in found_names


def test_tool_loading_with_module():
    found = _discover_tools(["gptme.tools.save"])

    found_names = [t.name for t in found]

    assert "save" in found_names
    assert "ipython" not in found_names


def test_tool_loading_with_missing_package():
    found = _discover_tools(["gptme.fake_"])

    # We're only checking that no error was raised and that none
    # of the returned tools come from the non-existent module
    # The MCP tools might be included which is fine
    for tool in found:
        assert not tool.name.startswith(
            "fake_"
        ), f"Tool {tool.name} should not be from non-existent module"


def test_get_available_tools():
    custom_env_value = "gptme.tools.save,gptme.tools.patch"

    with patch("gptme.tools.get_config") as mock_get_config:
        # Mock the get_config function to return a mock object
        mock_config = mock_get_config.return_value
        # Mock the get_env method to return the custom_env_value
        mock_config.get_env.return_value = custom_env_value

        tools = get_available_tools()

    assert len(tools) == 3
    assert [t.name for t in tools] == ["append", "patch", "save"]


def test_has_tool():
    init_tools(allowlist=["save"])

    assert has_tool("save")
    assert not has_tool("anothertool")


def test_get_tool():
    init_tools(allowlist=["save"])

    tool_save = get_tool("save")

    assert tool_save
    assert tool_save.name == "save"

    assert not get_tool("anothertool")


def test_get_tool_for_lang_tag():
    init_tools(allowlist=["save", "ipython"])

    assert (tool_python := get_tool_for_langtag("ipython"))
    assert tool_python.name == "ipython"

    assert not get_tool_for_langtag("randomtag")


def test_is_supported_lang_tag():
    init_tools(allowlist=["save"])

    assert is_supported_langtag("save")
    assert not is_supported_langtag("randomtag")
