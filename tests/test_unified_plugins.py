"""Tests for the unified plugin system (gptme.plugins.plugin, registry, entrypoints)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gptme.plugins.plugin import GptmePlugin
from gptme.plugins.registry import (
    _folder_plugin_to_gptme_plugin,
    _make_command_registrar,
    _make_hook_registrar,
    clear_registry,
    discover_all_plugins,
    get_all_plugins,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure each test starts with a clean registry."""
    clear_registry()
    yield
    clear_registry()


# --- GptmePlugin dataclass ---


class TestGptmePlugin:
    def test_minimal_plugin(self):
        """A plugin with only a name is valid."""
        p = GptmePlugin(name="minimal")
        assert p.name == "minimal"
        assert p.provider is None
        assert p.tool_modules == []
        assert p.tools == []
        assert p.register_hooks is None
        assert p.register_commands is None
        assert p.init is None

    def test_plugin_with_all_capabilities(self):
        """A plugin can provide tools, hooks, commands, and a provider."""
        from gptme.llm.models.types import ProviderPlugin

        provider = ProviderPlugin(
            name="test_provider",
            api_key_env="TEST_API_KEY",
            base_url="https://test.example.com/v1",
        )
        hook_fn = MagicMock()
        cmd_fn = MagicMock()
        init_fn = MagicMock()

        p = GptmePlugin(
            name="full",
            provider=provider,
            tool_modules=["my_pkg.tools"],
            register_hooks=hook_fn,
            register_commands=cmd_fn,
            init=init_fn,
        )
        assert p.provider is provider
        assert p.tool_modules == ["my_pkg.tools"]
        assert p.register_hooks is hook_fn
        assert p.register_commands is cmd_fn
        assert p.init is init_fn


# --- Folder plugin adapter ---


class TestFolderPluginAdapter:
    def test_converts_tool_modules(self):
        """Folder plugin tool_modules are preserved."""
        from pathlib import Path

        from gptme.plugins import Plugin

        fp = Plugin(
            name="myplugin", path=Path("/fake"), tool_modules=["myplugin.tools"]
        )
        gp = _folder_plugin_to_gptme_plugin(fp)
        assert gp.name == "myplugin"
        assert gp.tool_modules == ["myplugin.tools"]
        assert gp.register_hooks is None
        assert gp.register_commands is None

    def test_creates_hook_registrar(self):
        """Folder plugin hook_modules become a callable registrar."""
        from pathlib import Path

        from gptme.plugins import Plugin

        fp = Plugin(
            name="myplugin",
            path=Path("/fake"),
            hook_modules=["myplugin.hooks"],
        )
        gp = _folder_plugin_to_gptme_plugin(fp)
        assert gp.register_hooks is not None
        assert callable(gp.register_hooks)

    def test_creates_command_registrar(self):
        """Folder plugin command_modules become a callable registrar."""
        from pathlib import Path

        from gptme.plugins import Plugin

        fp = Plugin(
            name="myplugin",
            path=Path("/fake"),
            command_modules=["myplugin.commands"],
        )
        gp = _folder_plugin_to_gptme_plugin(fp)
        assert gp.register_commands is not None
        assert callable(gp.register_commands)


# --- Hook and command registrars ---


class TestRegistrars:
    def test_hook_registrar_calls_register(self):
        """Hook registrar imports module and calls register()."""
        mock_module = MagicMock()
        mock_module.register = MagicMock()

        with patch(
            "gptme.plugins.registry.importlib.import_module", return_value=mock_module
        ):
            registrar = _make_hook_registrar(["fake.hooks"])
            registrar()

        mock_module.register.assert_called_once()

    def test_hook_registrar_handles_missing_register(self):
        """Hook registrar warns when module has no register()."""
        mock_module = MagicMock(spec=[])  # No register attribute

        with patch(
            "gptme.plugins.registry.importlib.import_module", return_value=mock_module
        ):
            registrar = _make_hook_registrar(["fake.hooks"])
            registrar()  # Should not raise

    def test_hook_registrar_handles_import_error(self):
        """Hook registrar handles import errors gracefully."""
        with patch(
            "gptme.plugins.registry.importlib.import_module",
            side_effect=ImportError("nope"),
        ):
            registrar = _make_hook_registrar(["nonexistent.hooks"])
            registrar()  # Should not raise

    def test_command_registrar_calls_register(self):
        """Command registrar imports module and calls register()."""
        mock_module = MagicMock()
        mock_module.register = MagicMock()

        with patch(
            "gptme.plugins.registry.importlib.import_module", return_value=mock_module
        ):
            registrar = _make_command_registrar(["fake.commands"])
            registrar()

        mock_module.register.assert_called_once()


# --- Entry-point discovery ---


class TestEntrypointDiscovery:
    def test_discovers_gptme_plugin_instances(self):
        """Entry points that export GptmePlugin are discovered."""
        from gptme.plugins.entrypoints import (
            clear_entrypoint_cache,
            discover_entrypoint_plugins,
        )

        clear_entrypoint_cache()
        plugin = GptmePlugin(name="test_ep")
        mock_ep = MagicMock()
        mock_ep.name = "test_ep"
        mock_ep.load.return_value = plugin

        with patch("gptme.plugins.entrypoints.entry_points", return_value=[mock_ep]):
            result = discover_entrypoint_plugins()

        assert len(result) == 1
        assert result[0].name == "test_ep"
        clear_entrypoint_cache()

    def test_discovers_factory_functions(self):
        """Entry points that export a callable returning GptmePlugin are supported."""
        from gptme.plugins.entrypoints import (
            clear_entrypoint_cache,
            discover_entrypoint_plugins,
        )

        clear_entrypoint_cache()
        plugin = GptmePlugin(name="factory_plugin")
        factory = MagicMock(return_value=plugin)
        # Factory is not a GptmePlugin instance itself
        factory.__class__ = type("factory", (), {})

        mock_ep = MagicMock()
        mock_ep.name = "factory_plugin"
        mock_ep.load.return_value = factory

        with patch("gptme.plugins.entrypoints.entry_points", return_value=[mock_ep]):
            result = discover_entrypoint_plugins()

        assert len(result) == 1
        assert result[0].name == "factory_plugin"
        clear_entrypoint_cache()

    def test_skips_invalid_exports(self):
        """Entry points exporting non-GptmePlugin objects are skipped."""
        from gptme.plugins.entrypoints import (
            clear_entrypoint_cache,
            discover_entrypoint_plugins,
        )

        clear_entrypoint_cache()
        mock_ep = MagicMock()
        mock_ep.name = "bad_plugin"
        mock_ep.load.return_value = "not a plugin"

        with patch("gptme.plugins.entrypoints.entry_points", return_value=[mock_ep]):
            result = discover_entrypoint_plugins()

        assert len(result) == 0
        clear_entrypoint_cache()

    def test_handles_load_error(self):
        """Entry points that fail to load are skipped gracefully."""
        from gptme.plugins.entrypoints import (
            clear_entrypoint_cache,
            discover_entrypoint_plugins,
        )

        clear_entrypoint_cache()
        mock_ep = MagicMock()
        mock_ep.name = "broken"
        mock_ep.load.side_effect = ImportError("missing dep")

        with patch("gptme.plugins.entrypoints.entry_points", return_value=[mock_ep]):
            result = discover_entrypoint_plugins()

        assert len(result) == 0
        clear_entrypoint_cache()


# --- Unified registry ---


class TestUnifiedRegistry:
    def test_empty_registry(self):
        """Registry starts empty."""
        assert get_all_plugins() == []

    def test_discover_merges_sources(self):
        """discover_all_plugins merges folder and entry-point plugins."""
        ep_plugin = GptmePlugin(name="ep_plugin")

        with (
            patch(
                "gptme.plugins.registry.discover_entrypoint_plugins",
                return_value=(ep_plugin,),
            ),
            patch(
                "gptme.plugins.registry._discover_legacy_provider_plugins",
                return_value=[],
            ),
            patch("gptme.config.get_config") as mock_config,
        ):
            mock_config.return_value = MagicMock()
            result = discover_all_plugins()

        assert len(result) == 1
        assert result[0].name == "ep_plugin"
        # Also available via get_all_plugins
        assert len(get_all_plugins()) == 1

    def test_legacy_provider_bridged(self):
        """Legacy gptme.providers entry points are bridged to GptmePlugin."""
        from gptme.llm.models.types import ProviderPlugin

        legacy = ProviderPlugin(
            name="legacy_prov",
            api_key_env="LEGACY_KEY",
            base_url="https://legacy.example.com",
        )

        with (
            patch(
                "gptme.plugins.registry.discover_entrypoint_plugins",
                return_value=(),
            ),
            patch(
                "gptme.plugins.registry._discover_legacy_provider_plugins",
                return_value=[GptmePlugin(name="legacy_prov", provider=legacy)],
            ),
            patch("gptme.config.get_config") as mock_config,
        ):
            mock_config.return_value = MagicMock()
            result = discover_all_plugins()

        assert len(result) == 1
        assert result[0].name == "legacy_prov"
        assert result[0].provider is legacy

    def test_deduplication_prefers_unified(self):
        """If a plugin appears in both gptme.plugins and gptme.providers, unified wins."""
        from gptme.llm.models.types import ProviderPlugin

        unified = GptmePlugin(name="my_provider", tool_modules=["my_provider.tools"])
        legacy_prov = ProviderPlugin(
            name="my_provider",
            api_key_env="KEY",
            base_url="https://example.com",
        )
        legacy = GptmePlugin(name="my_provider", provider=legacy_prov)

        with (
            patch(
                "gptme.plugins.registry.discover_entrypoint_plugins",
                return_value=(unified,),
            ),
            patch(
                "gptme.plugins.registry._discover_legacy_provider_plugins",
                return_value=[legacy],
            ),
            patch("gptme.config.get_config") as mock_config,
        ):
            mock_config.return_value = MagicMock()
            result = discover_all_plugins()

        # Only the unified plugin, not the legacy duplicate
        assert len(result) == 1
        assert result[0] is unified

    def test_allowlist_filters(self):
        """enabled_plugins allowlist filters discovered plugins."""
        p1 = GptmePlugin(name="allowed")
        p2 = GptmePlugin(name="not_allowed")

        with (
            patch(
                "gptme.plugins.registry.discover_entrypoint_plugins",
                return_value=(p1, p2),
            ),
            patch(
                "gptme.plugins.registry._discover_legacy_provider_plugins",
                return_value=[],
            ),
            patch("gptme.config.get_config") as mock_config,
        ):
            mock_config.return_value = MagicMock()
            result = discover_all_plugins(enabled_plugins=["allowed"])

        assert len(result) == 1
        assert result[0].name == "allowed"

    def test_plugin_init_called(self):
        """Plugin-level init() is called with config during discovery."""
        init_fn = MagicMock()
        p = GptmePlugin(name="with_init", init=init_fn)

        with (
            patch(
                "gptme.plugins.registry.discover_entrypoint_plugins",
                return_value=(p,),
            ),
            patch(
                "gptme.plugins.registry._discover_legacy_provider_plugins",
                return_value=[],
            ),
            patch("gptme.config.get_config") as mock_config,
        ):
            config = MagicMock()
            mock_config.return_value = config
            discover_all_plugins()

        init_fn.assert_called_once_with(config)

    def test_plugin_init_failure_logged(self):
        """Plugin init() failure is logged, not raised."""

        def bad_init(config):
            raise RuntimeError("init failed")

        p = GptmePlugin(name="bad_init", init=bad_init)

        with (
            patch(
                "gptme.plugins.registry.discover_entrypoint_plugins",
                return_value=(p,),
            ),
            patch(
                "gptme.plugins.registry._discover_legacy_provider_plugins",
                return_value=[],
            ),
            patch("gptme.config.get_config") as mock_config,
        ):
            mock_config.return_value = MagicMock()
            # Should not raise
            result = discover_all_plugins()

        assert len(result) == 1


# --- Provider plugin backward compatibility ---


class TestProviderBackwardCompat:
    def test_provider_helpers_work_via_registry(self):
        """is_plugin_provider, get_provider_plugin, get_plugin_api_keys
        all work when the registry has been initialized."""
        from gptme.llm.models.types import ProviderPlugin
        from gptme.llm.provider_plugins import (
            get_plugin_api_keys,
            get_provider_plugin,
            is_plugin_provider,
        )

        prov = ProviderPlugin(
            name="test_prov",
            api_key_env="TEST_KEY",
            base_url="https://test.example.com",
        )
        gp = GptmePlugin(name="test_prov", provider=prov)

        with (
            patch(
                "gptme.plugins.registry.discover_entrypoint_plugins",
                return_value=(gp,),
            ),
            patch(
                "gptme.plugins.registry._discover_legacy_provider_plugins",
                return_value=[],
            ),
            patch("gptme.config.get_config") as mock_config,
        ):
            mock_config.return_value = MagicMock()
            discover_all_plugins()

        assert is_plugin_provider("test_prov")
        assert not is_plugin_provider("nonexistent")
        assert get_provider_plugin("test_prov") is prov
        assert get_provider_plugin("nonexistent") is None
        assert get_plugin_api_keys() == {"test_prov": "TEST_KEY"}

    def test_fallback_before_registry_init(self):
        """Before discover_all_plugins(), provider helpers fall back to raw scanning."""
        from gptme.llm.provider_plugins import (
            _discover_provider_plugins_raw,
            clear_plugin_cache,
            discover_provider_plugins,
        )

        clear_plugin_cache()
        clear_registry()

        # With no actual entry points installed, should return empty
        result = discover_provider_plugins()
        assert isinstance(result, tuple)
        # Just verify it doesn't crash — actual content depends on installed packages

        # Clean up
        _discover_provider_plugins_raw.cache_clear()
