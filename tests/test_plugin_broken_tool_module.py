"""A folder plugin whose tool module raises at import (anything but
ModuleNotFoundError) must not crash tool discovery and gptme startup; every
other plugin failure mode is logged and tolerated."""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

import gptme.plugins as plugins_mod
from gptme.tools import get_available_tools

_PLUGIN = "brokenplug_tool_import"


@pytest.fixture(params=[False, True], ids=["tools-namespace", "tools-package"])
def broken_plugin_dir(request, tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    plug = root / _PLUGIN
    (plug / "tools").mkdir(parents=True)
    (plug / "__init__.py").write_text("")
    if request.param:
        # With a package, the failure is raised inside _collect_tool_modules.
        (plug / "tools" / "__init__.py").write_text("")
    # plugin built against another gptme version: renamed symbol
    # "a_broken" sorts before "good", so a scan that stops at the first
    # failure would skip the working sibling.
    (plug / "tools" / "a_broken.py").write_text(
        "from gptme.tools.base import ToolSpecRenamedInNewerGptme\n"
    )
    (plug / "tools" / "good.py").write_text(
        "from gptme.tools.base import ToolSpec\n"
        'tool = ToolSpec(name="brokenplug_good", desc="ok", instructions="")\n'
    )
    # Fresh caches and import state for this test only; restored on teardown.
    monkeypatch.setattr(plugins_mod, "_plugin_cache", {})
    monkeypatch.setattr(
        plugins_mod, "_loaded_plugins", type(plugins_mod._loaded_plugins)()
    )
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield root
    for name in [m for m in sys.modules if m == _PLUGIN or m.startswith(f"{_PLUGIN}.")]:
        del sys.modules[name]


def test_broken_folder_plugin_tool_does_not_crash_tool_discovery(
    broken_plugin_dir, caplog
):
    cfg = MagicMock()
    cfg.get_env.side_effect = lambda key, default=None: (
        "gptme.tools.save" if key == "TOOL_MODULES" else default
    )
    cfg.get_plugin_config.return_value = ([broken_plugin_dir], None)

    with (
        caplog.at_level(logging.ERROR, logger="gptme.tools"),
        patch("gptme.config.get_config", return_value=cfg),
        patch("gptme.plugins.registry.get_all_plugins", return_value=[]),
    ):
        tools = get_available_tools(include_mcp=False)  # must not raise

    names = {t.name for t in tools}
    assert "save" in names
    assert "brokenplug_good" in names, "the working sibling tool was skipped"
    assert any(
        "Failed to import tool module" in r.getMessage()
        and "a_broken" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]
