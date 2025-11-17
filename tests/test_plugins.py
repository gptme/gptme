"""Tests for the plugin system."""

import tempfile
from pathlib import Path

from gptme.config import PluginsConfig
from gptme.plugins import Plugin, discover_plugins, get_plugin_tool_modules


def test_plugin_dataclass():
    """Test Plugin dataclass creation."""
    plugin = Plugin(
        name="test_plugin",
        path=Path("/tmp/test_plugin"),
        tool_modules=["test_plugin.tools"],
    )
    assert plugin.name == "test_plugin"
    assert plugin.path == Path("/tmp/test_plugin")
    assert plugin.tool_modules == ["test_plugin.tools"]


def test_plugins_config_dataclass():
    """Test PluginsConfig dataclass creation."""
    config = PluginsConfig(
        paths=["~/.config/gptme/plugins"],
        enabled=["plugin1", "plugin2"],
    )
    assert config.paths == ["~/.config/gptme/plugins"]
    assert config.enabled == ["plugin1", "plugin2"]


def test_discover_plugins_empty_path():
    """Test plugin discovery with non-existent path."""
    plugins = discover_plugins([Path("/nonexistent/path")])
    assert plugins == []


def test_discover_plugins_with_valid_plugin():
    """Test plugin discovery with a valid plugin structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / "my_plugin"
        plugin_dir.mkdir()

        # Create __init__.py to make it a valid package
        (plugin_dir / "__init__.py").write_text("# Plugin init")

        # Create tools directory with __init__.py
        tools_dir = plugin_dir / "tools"
        tools_dir.mkdir()
        (tools_dir / "__init__.py").write_text("# Tools init")

        # Discover plugins
        plugins = discover_plugins([Path(tmpdir)])

        assert len(plugins) == 1
        assert plugins[0].name == "my_plugin"
        assert plugins[0].path == plugin_dir
        assert "my_plugin.tools" in plugins[0].tool_modules


def test_discover_plugins_without_tools():
    """Test plugin discovery for plugin without tools directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / "minimal_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "__init__.py").write_text("# Plugin init")

        plugins = discover_plugins([Path(tmpdir)])

        assert len(plugins) == 1
        assert plugins[0].name == "minimal_plugin"
        assert plugins[0].tool_modules == []


def test_discover_plugins_invalid_package():
    """Test that directories without __init__.py are skipped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create directory without __init__.py
        invalid_dir = Path(tmpdir) / "not_a_plugin"
        invalid_dir.mkdir()

        plugins = discover_plugins([Path(tmpdir)])
        assert plugins == []


def test_get_plugin_tool_modules():
    """Test getting tool modules from plugins."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create first plugin
        plugin1_dir = Path(tmpdir) / "plugin1"
        plugin1_dir.mkdir()
        (plugin1_dir / "__init__.py").write_text("")
        tools1_dir = plugin1_dir / "tools"
        tools1_dir.mkdir()
        (tools1_dir / "__init__.py").write_text("")

        # Create second plugin
        plugin2_dir = Path(tmpdir) / "plugin2"
        plugin2_dir.mkdir()
        (plugin2_dir / "__init__.py").write_text("")
        tools2_dir = plugin2_dir / "tools"
        tools2_dir.mkdir()
        (tools2_dir / "__init__.py").write_text("")

        # Get tool modules
        tool_modules = get_plugin_tool_modules([Path(tmpdir)])

        assert len(tool_modules) == 2
        assert "plugin1.tools" in tool_modules
        assert "plugin2.tools" in tool_modules


def test_get_plugin_tool_modules_with_allowlist():
    """Test getting tool modules with plugin allowlist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create two plugins
        for name in ["plugin1", "plugin2"]:
            plugin_dir = Path(tmpdir) / name
            plugin_dir.mkdir()
            (plugin_dir / "__init__.py").write_text("")
            tools_dir = plugin_dir / "tools"
            tools_dir.mkdir()
            (tools_dir / "__init__.py").write_text("")

        # Get tool modules with allowlist (only plugin1)
        tool_modules = get_plugin_tool_modules(
            [Path(tmpdir)],
            enabled_plugins=["plugin1"],
        )

        assert len(tool_modules) == 1
        assert "plugin1.tools" in tool_modules
        assert "plugin2.tools" not in tool_modules


def test_discover_plugins_with_individual_tool_files():
    """Test plugin discovery with individual tool files (not a tools package)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / "my_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "__init__.py").write_text("")

        # Create tools directory WITHOUT __init__.py (not a package)
        tools_dir = plugin_dir / "tools"
        tools_dir.mkdir()

        # Create individual tool files
        (tools_dir / "tool1.py").write_text("# Tool 1")
        (tools_dir / "tool2.py").write_text("# Tool 2")
        (tools_dir / "_private.py").write_text("# Should be skipped")

        plugins = discover_plugins([Path(tmpdir)])

        assert len(plugins) == 1
        plugin = plugins[0]

        # Should discover individual tool modules (not the private one)
        assert len(plugin.tool_modules) == 2
        assert "my_plugin.tools.tool1" in plugin.tool_modules
        assert "my_plugin.tools.tool2" in plugin.tool_modules
        assert "my_plugin.tools._private" not in plugin.tool_modules


def test_plugin_hooks_dataclass():
    """Test Plugin dataclass with hook modules."""
    plugin = Plugin(
        name="test_plugin",
        path=Path("/tmp/test_plugin"),
        tool_modules=["test_plugin.tools"],
        hook_modules=["test_plugin.hooks"],
    )
    assert plugin.hook_modules == ["test_plugin.hooks"]


def test_discover_plugins_with_hooks():
    """Test plugin discovery with hooks directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / "my_plugin"
        plugin_dir.mkdir()

        # Create __init__.py
        (plugin_dir / "__init__.py").write_text("# Plugin init")

        # Create hooks directory with __init__.py
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "__init__.py").write_text("# Hooks init")

        # Discover plugins
        plugins = discover_plugins([Path(tmpdir)])

        assert len(plugins) == 1
        assert plugins[0].name == "my_plugin"
        assert "my_plugin.hooks" in plugins[0].hook_modules


def test_discover_plugins_with_individual_hook_modules():
    """Test plugin discovery with individual hook module files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / "my_plugin"
        plugin_dir.mkdir()

        # Create __init__.py
        (plugin_dir / "__init__.py").write_text("# Plugin init")

        # Create hooks directory without __init__.py
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir()

        # Create individual hook modules
        (hooks_dir / "my_hook.py").write_text("def my_hook(): pass")
        (hooks_dir / "another_hook.py").write_text("def another_hook(): pass")

        # Discover plugins
        plugins = discover_plugins([Path(tmpdir)])

        assert len(plugins) == 1
        assert "my_plugin.hooks.my_hook" in plugins[0].hook_modules
        assert "my_plugin.hooks.another_hook" in plugins[0].hook_modules


def test_register_plugin_hooks():
    """Test plugin hook registration."""
    from gptme.hooks import HookType, clear_hooks, get_hooks
    from gptme.plugins import register_plugin_hooks

    # Clear any existing hooks
    clear_hooks()

    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / "test_plugin"
        plugin_dir.mkdir()

        # Create __init__.py
        (plugin_dir / "__init__.py").write_text("# Plugin init")

        # Create hooks directory with a hook module
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir()

        # Create a hook module with register() function
        hook_code = """
from gptme.hooks import HookType, register_hook
from gptme.message import Message

def test_hook(**kwargs):
    yield Message("system", "Test hook executed")

def register():
    register_hook(
        "test_plugin.test_hook",
        HookType.SESSION_START,
        test_hook,
        priority=0
    )
"""
        (hooks_dir / "test_hooks.py").write_text(hook_code)

        # Register plugin hooks
        register_plugin_hooks([Path(tmpdir)])

        # Verify hook was registered
        hooks = get_hooks(HookType.SESSION_START)
        hook_names = [h.name for h in hooks]
        assert "test_plugin.test_hook" in hook_names
