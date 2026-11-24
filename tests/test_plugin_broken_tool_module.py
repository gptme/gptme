"""A folder plugin whose tool module raises at import (anything but
ModuleNotFoundError) must not crash tool discovery and gptme startup; every
other plugin failure mode is logged and tolerated."""

from unittest.mock import MagicMock, patch

import pytest

import gptme.plugins as plugins_mod
from gptme.tools import get_available_tools


@pytest.fixture
def broken_plugin_dir(tmp_path):
    root = tmp_path / "plugins"
    plug = root / "brokenplug_tool_import"
    (plug / "tools").mkdir(parents=True)
    (plug / "__init__.py").write_text("")
    # e.g. a plugin importing a symbol removed from gptme, or a bad env var
    (plug / "tools" / "thing.py").write_text(
        # plugin built against another gptme version: renamed symbol
        "from gptme.tools.base import ToolSpecRenamedInNewerGptme\n"
    )
    plugins_mod._plugin_cache.clear()
    plugins_mod._loaded_plugins.clear()
    yield root
    plugins_mod._plugin_cache.clear()
    plugins_mod._loaded_plugins.clear()


def test_broken_folder_plugin_tool_does_not_crash_tool_discovery(broken_plugin_dir):
    cfg = MagicMock()
    cfg.get_env.side_effect = lambda key, default=None: (
        "gptme.tools.save" if key == "TOOL_MODULES" else default
    )
    cfg.get_plugin_config.return_value = ([broken_plugin_dir], None)

    with (
        patch("gptme.config.get_config", return_value=cfg),
        patch("gptme.plugins.registry.get_all_plugins", return_value=[]),
    ):
        tools = get_available_tools(include_mcp=False)  # must not raise

    assert any(t.name == "save" for t in tools)
