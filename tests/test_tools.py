import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.tools import (
    _discover_tools,
    clear_tools,
    get_available_tools,
    get_tool,
    get_tool_for_langtag,
    get_tools,
    has_tool,
    init_tools,
    is_supported_langtag,
    load_tool,
)
from gptme.tools.base import load_from_file


def test_init_tools():
    init_tools()

    assert len(get_tools()) > 1


def test_init_tools_allowlist():
    clear_tools()  # ensure clean state regardless of test ordering
    init_tools(allowlist=["save"])
    assert len(get_tools()) == 1
    assert get_tools()[0].name == "save"

    clear_tools()  # clear before testing second allowlist
    init_tools(allowlist=["save", "patch"])
    assert len(get_tools()) == 2


def test_init_tools_allowlist_from_env():
    clear_tools()  # ensure clean state regardless of test ordering

    # Define the behavior for get_env based on the input key
    def mock_get_env(key, default=None):
        if key == "TOOL_ALLOWLIST":
            return "save,patch"
        return default  # Return the default value for other keys

    with patch("gptme.config.get_config") as mock_get_config:
        # Mock the get_config function to return a mock object
        mock_config = mock_get_config.return_value
        # Mock the get_env method to return the custom_env_value
        mock_config.get_env.side_effect = mock_get_env

        init_tools()

    assert len(get_tools()) == 2


def test_init_tools_fails():
    with pytest.raises(ValueError, match="not found"):
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
    assert not found


def test_get_available_tools():
    # Clear cache to ensure test uses mocked config
    clear_tools()
    custom_env_value = "gptme.tools.save,gptme.tools.patch"

    with patch("gptme.config.get_config") as mock_get_config:
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


def test_load_tool():
    """Test loading a tool mid-conversation."""
    clear_tools()
    init_tools(allowlist=["save"])
    assert has_tool("save")
    assert not has_tool("patch")

    # Load 'patch' mid-conversation
    tool = load_tool("patch")
    assert tool.name == "patch"
    assert has_tool("patch")
    assert len(get_tools()) == 2


def test_load_tool_already_loaded():
    """Test that loading an already-loaded tool raises ValueError."""
    clear_tools()
    init_tools(allowlist=["save"])

    with pytest.raises(ValueError, match="already loaded"):
        load_tool("save")


def test_load_tool_not_found():
    """Test that loading a non-existent tool raises ValueError."""
    clear_tools()
    init_tools(allowlist=["save"])

    with pytest.raises(ValueError, match="not found"):
        load_tool("nonexistent_tool_xyz")


def test_load_tool_unavailable():
    """Test that loading an unavailable tool raises ValueError."""
    from gptme.tools.base import ToolSpec

    clear_tools()
    init_tools(allowlist=["save"])

    # Inject a fake unavailable tool into the available tools cache
    available = get_available_tools()
    unavailable_tool = ToolSpec(
        name="fake_unavailable",
        desc="A fake unavailable tool",
        available=False,
    )
    available.append(unavailable_tool)

    with pytest.raises(ValueError, match="unavailable"):
        load_tool("fake_unavailable")


def test_load_tool_context_isolation():
    """Test that tool loading is context-local (ContextVar per-thread).

    Each thread has its own tool state via ContextVar, so loading the same
    tool in two threads should succeed independently in both.
    """
    import threading

    results: list[str] = []

    def load_in_thread():
        # Each thread gets its own ContextVar state
        clear_tools()
        init_tools(allowlist=["save"])
        tool = load_tool("patch")
        results.append(tool.name)

    t1 = threading.Thread(target=load_in_thread)
    t2 = threading.Thread(target=load_in_thread)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Both threads should independently succeed
    assert len(results) == 2
    assert all(name == "patch" for name in results)


def test_load_multiple_tools_sequentially():
    """Test loading multiple tools one after another."""
    clear_tools()
    init_tools(allowlist=["save"])
    assert len(get_tools()) == 1

    load_tool("patch")
    assert len(get_tools()) == 2
    assert has_tool("patch")

    load_tool("append")
    assert len(get_tools()) == 3
    assert has_tool("append")


# --- File-based tool loading tests ---

_SAMPLE_TOOL_PY = """\
from gptme.tools.base import ToolSpec

sample_tool = ToolSpec(
    name="sample_file_tool",
    desc="A sample tool loaded from a file",
    available=True,
)
"""


def test_load_from_file():
    """Test loading a ToolSpec from a .py file."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(_SAMPLE_TOOL_PY)
        f.flush()
        path = Path(f.name)

    try:
        tools = load_from_file(path)
        assert len(tools) == 1
        assert tools[0].name == "sample_file_tool"
    finally:
        path.unlink()


def test_load_from_file_not_found():
    """Test that loading from a non-existent file raises ValueError."""
    with pytest.raises(ValueError, match="does not exist"):
        load_from_file(Path("/tmp/nonexistent_tool_xyz.py"))


def test_load_from_file_not_py():
    """Test that loading a non-.py file raises ValueError."""
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
        f.write("not a tool")
        path = Path(f.name)

    try:
        with pytest.raises(ValueError, match=".py file"):
            load_from_file(path)
    finally:
        path.unlink()


def test_init_tools_with_file_path():
    """Test that init_tools supports .py file paths in the allowlist."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(_SAMPLE_TOOL_PY)
        f.flush()
        path = Path(f.name)

    try:
        clear_tools()
        init_tools(allowlist=[str(path)])
        tools = get_tools()
        tool_names = [t.name for t in tools]
        assert "sample_file_tool" in tool_names
    finally:
        path.unlink()


def test_init_tools_mixed_names_and_files():
    """Test init_tools with both tool names and file paths."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(_SAMPLE_TOOL_PY)
        f.flush()
        path = Path(f.name)

    try:
        clear_tools()
        init_tools(allowlist=["save", str(path)])
        tools = get_tools()
        tool_names = [t.name for t in tools]
        assert "save" in tool_names
        assert "sample_file_tool" in tool_names
        assert len(tools) == 2
    finally:
        path.unlink()


def test_load_from_file_collision():
    """Test that two .py files with the same filename from different dirs both load correctly."""
    import tempfile

    tool1_py = """\
from gptme.tools.base import ToolSpec

tool1 = ToolSpec(
    name="collision_tool_1",
    desc="Tool 1",
    available=True,
)
"""
    tool2_py = """\
from gptme.tools.base import ToolSpec

tool2 = ToolSpec(
    name="collision_tool_2",
    desc="Tool 2",
    available=True,
)
"""
    with tempfile.TemporaryDirectory() as dir1, tempfile.TemporaryDirectory() as dir2:
        path1 = Path(dir1) / "mytool.py"
        path2 = Path(dir2) / "mytool.py"  # same filename, different directory
        path1.write_text(tool1_py)
        path2.write_text(tool2_py)

        tools1 = load_from_file(path1)
        tools2 = load_from_file(path2)

        assert len(tools1) == 1
        assert tools1[0].name == "collision_tool_1"
        assert len(tools2) == 1
        assert tools2[0].name == "collision_tool_2"
