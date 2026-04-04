"""Unit tests for gptme/info.py — system and environment information utilities.

Tests cover: dataclasses, package detection, extras parsing, installation info,
health checks, and version formatting (both human-readable and JSON).
"""

import importlib.metadata
import json
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gptme.info import (
    _EXTRA_DESCRIPTIONS,
    _INTERNAL_EXTRAS,
    ExtraInfo,
    InstallInfo,
    _is_package_installed,
    _parse_extras_from_metadata,
    format_version_info,
    get_available_providers,
    get_config_info,
    get_default_model,
    get_install_info,
    get_installed_extras,
    get_quick_health,
    get_system_info,
    get_tool_count,
)

# ─── Dataclass tests ────────────────────────────────────────────────────────


class TestExtraInfo:
    """Tests for ExtraInfo dataclass."""

    def test_basic_construction(self):
        e = ExtraInfo(name="browser", installed=True, description="Web browsing")
        assert e.name == "browser"
        assert e.installed is True
        assert e.description == "Web browsing"
        assert e.packages == []

    def test_with_packages(self):
        e = ExtraInfo(
            name="server",
            installed=False,
            description="REST API",
            packages=["flask", "gunicorn"],
        )
        assert e.packages == ["flask", "gunicorn"]

    def test_default_packages_is_empty_list(self):
        """Each instance gets its own default list (not shared)."""
        e1 = ExtraInfo(name="a", installed=False, description="")
        e2 = ExtraInfo(name="b", installed=False, description="")
        e1.packages.append("foo")
        assert e2.packages == []

    def test_fields(self):
        names = {f.name for f in fields(ExtraInfo)}
        assert names == {"name", "installed", "description", "packages"}


class TestInstallInfo:
    """Tests for InstallInfo dataclass."""

    def test_basic_construction(self):
        i = InstallInfo(method="uv", editable=True, path="/some/path")
        assert i.method == "uv"
        assert i.editable is True
        assert i.path == "/some/path"

    def test_default_path_is_none(self):
        i = InstallInfo(method="pip", editable=False)
        assert i.path is None


# ─── _is_package_installed tests ─────────────────────────────────────────────


class TestIsPackageInstalled:
    """Tests for _is_package_installed — name resolution and namespace handling."""

    def test_installed_package(self):
        """pytest is definitely installed in our test environment."""
        assert _is_package_installed("pytest") is True

    def test_not_installed_package(self):
        assert _is_package_installed("nonexistent_package_xyz_12345") is False

    def test_hyphen_to_underscore(self):
        """Packages with hyphens should try underscore variant."""
        with patch("importlib.util.find_spec") as mock_find:
            # First call (original name) fails, second (underscore) succeeds
            mock_find.side_effect = [None, MagicMock()]
            result = _is_package_installed("my-package")
            assert result is True
            assert mock_find.call_count == 2
            mock_find.assert_any_call("my-package")
            mock_find.assert_any_call("my_package")

    def test_namespace_package_base_name(self):
        """For 'opentelemetry-api', should try 'opentelemetry' as well."""
        with patch("importlib.util.find_spec") as mock_find:
            # original fails, underscore fails, base name succeeds
            mock_find.side_effect = [None, None, MagicMock()]
            result = _is_package_installed("opentelemetry-api")
            assert result is True
            mock_find.assert_any_call("opentelemetry")

    def test_underscore_name_no_redundant_variant(self):
        """Pure-underscore name has no distinct hyphen/underscore variant — only tried once."""
        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            result = _is_package_installed("my_package")
            assert result is False
            mock_find.assert_called_once_with("my_package")

    def test_simple_name_no_variants(self):
        """Package with no hyphens/underscores tries only original."""
        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            result = _is_package_installed("simple")
            assert result is False
            mock_find.assert_called_once_with("simple")

    def test_find_spec_raises_module_not_found(self):
        """ModuleNotFoundError should be caught and handled."""
        with patch("importlib.util.find_spec") as mock_find:
            mock_find.side_effect = ModuleNotFoundError
            result = _is_package_installed("broken")
            assert result is False

    def test_find_spec_raises_value_error(self):
        """ValueError should be caught (some edge cases in importlib)."""
        with patch("importlib.util.find_spec") as mock_find:
            mock_find.side_effect = ValueError
            result = _is_package_installed("broken")
            assert result is False

    def test_first_match_wins(self):
        """If original name matches, don't try variants."""
        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = MagicMock()  # first call succeeds
            result = _is_package_installed("my-package")
            assert result is True
            mock_find.assert_called_once_with("my-package")

    def test_hyphen_only_no_duplicate_base(self):
        """For 'a-b', base 'a' is different from original so it should be added."""
        with patch("importlib.util.find_spec") as mock_find:
            mock_find.return_value = None
            _is_package_installed("a-b")
            # Should try: 'a-b', 'a_b', 'a'
            assert mock_find.call_count == 3


# ─── _parse_extras_from_metadata tests ───────────────────────────────────────


class TestParseExtrasFromMetadata:
    """Tests for _parse_extras_from_metadata."""

    def _make_mock_dist(self, extras, requires):
        """Create a mock distribution with given extras and requirements."""
        dist = MagicMock()
        dist.metadata = MagicMock()
        dist.metadata.get_all.return_value = extras
        dist.requires = requires
        return dist

    @patch("gptme.info.importlib.metadata.distribution")
    def test_package_not_found(self, mock_dist):
        mock_dist.side_effect = importlib.metadata.PackageNotFoundError
        result = _parse_extras_from_metadata()
        assert result == []

    @patch("gptme.info.importlib.metadata.distribution")
    def test_basic_extras_parsing(self, mock_dist):
        mock_dist.return_value = self._make_mock_dist(
            extras=["browser", "server"],
            requires=[
                'playwright ; extra == "browser"',
                'flask (>=3.0) ; extra == "server"',
            ],
        )
        result = _parse_extras_from_metadata()
        assert len(result) == 2
        names = [e.name for e in result]
        assert "browser" in names
        assert "server" in names
        # Check packages were parsed
        browser_info = next(e for e in result if e.name == "browser")
        assert "playwright" in browser_info.packages
        server_info = next(e for e in result if e.name == "server")
        assert "flask" in server_info.packages

    @patch("gptme.info.importlib.metadata.distribution")
    def test_internal_extras_filtered(self, mock_dist):
        mock_dist.return_value = self._make_mock_dist(
            extras=["browser", "all", "eval", "pyinstaller"],
            requires=['playwright ; extra == "browser"'],
        )
        result = _parse_extras_from_metadata()
        names = [e.name for e in result]
        assert "browser" in names
        for internal in _INTERNAL_EXTRAS:
            assert internal not in names

    @patch("gptme.info.importlib.metadata.distribution")
    def test_description_from_lookup(self, mock_dist):
        mock_dist.return_value = self._make_mock_dist(extras=["browser"], requires=[])
        result = _parse_extras_from_metadata()
        assert result[0].description == _EXTRA_DESCRIPTIONS["browser"]

    @patch("gptme.info.importlib.metadata.distribution")
    def test_description_fallback(self, mock_dist):
        """Unknown extras get auto-generated description from name."""
        mock_dist.return_value = self._make_mock_dist(
            extras=["my_feature"], requires=[]
        )
        result = _parse_extras_from_metadata()
        assert result[0].description == "My Feature"

    @patch("gptme.info.importlib.metadata.distribution")
    def test_no_extras(self, mock_dist):
        mock_dist.return_value = self._make_mock_dist(extras=[], requires=[])
        result = _parse_extras_from_metadata()
        assert result == []

    @patch("gptme.info.importlib.metadata.distribution")
    def test_none_extras(self, mock_dist):
        dist = MagicMock()
        dist.metadata = MagicMock()
        dist.metadata.get_all.return_value = None
        dist.requires = []
        mock_dist.return_value = dist
        result = _parse_extras_from_metadata()
        assert result == []

    @patch("gptme.info.importlib.metadata.distribution")
    def test_multiple_deps_per_extra(self, mock_dist):
        mock_dist.return_value = self._make_mock_dist(
            extras=["datascience"],
            requires=[
                'numpy ; extra == "datascience"',
                'pandas ; extra == "datascience"',
                'matplotlib ; extra == "datascience"',
            ],
        )
        result = _parse_extras_from_metadata()
        ds = result[0]
        assert len(ds.packages) == 3
        assert set(ds.packages) == {"numpy", "pandas", "matplotlib"}

    @patch("gptme.info.importlib.metadata.distribution")
    def test_requirement_with_version_constraints(self, mock_dist):
        mock_dist.return_value = self._make_mock_dist(
            extras=["server"],
            requires=[
                'flask (>=3.0,<4.0) ; extra == "server"',
                'gunicorn[gevent] ; extra == "server"',
            ],
        )
        result = _parse_extras_from_metadata()
        server = result[0]
        assert "flask" in server.packages
        assert "gunicorn" in server.packages

    @patch("gptme.info.importlib.metadata.distribution")
    def test_single_quote_extra_marker(self, mock_dist):
        mock_dist.return_value = self._make_mock_dist(
            extras=["browser"],
            requires=["playwright ; extra == 'browser'"],
        )
        result = _parse_extras_from_metadata()
        assert result[0].packages == ["playwright"]

    @patch("gptme.info.importlib.metadata.distribution")
    def test_no_space_extra_marker(self, mock_dist):
        mock_dist.return_value = self._make_mock_dist(
            extras=["browser"],
            requires=['playwright ; extra=="browser"'],
        )
        result = _parse_extras_from_metadata()
        assert result[0].packages == ["playwright"]

    @patch("gptme.info.importlib.metadata.distribution")
    def test_requirement_without_extra_ignored(self, mock_dist):
        mock_dist.return_value = self._make_mock_dist(
            extras=["browser"],
            requires=[
                "click",  # no extra marker
                'playwright ; extra == "browser"',
            ],
        )
        result = _parse_extras_from_metadata()
        assert result[0].packages == ["playwright"]

    @patch("gptme.info.importlib.metadata.distribution")
    def test_no_duplicate_packages(self, mock_dist):
        """Same package listed twice for same extra should not duplicate."""
        mock_dist.return_value = self._make_mock_dist(
            extras=["browser"],
            requires=[
                'playwright ; extra == "browser"',
                'playwright ; extra == "browser"',
            ],
        )
        result = _parse_extras_from_metadata()
        assert result[0].packages == ["playwright"]

    @patch("gptme.info.importlib.metadata.distribution")
    def test_results_sorted_by_name(self, mock_dist):
        mock_dist.return_value = self._make_mock_dist(
            extras=["telemetry", "browser", "server"],
            requires=[],
        )
        result = _parse_extras_from_metadata()
        names = [e.name for e in result]
        assert names == sorted(names)

    @patch("gptme.info.importlib.metadata.distribution")
    def test_installed_field_defaults_false(self, mock_dist):
        mock_dist.return_value = self._make_mock_dist(extras=["browser"], requires=[])
        result = _parse_extras_from_metadata()
        assert result[0].installed is False


# ─── _get_extras caching tests ──────────────────────────────────────────────


class TestGetExtrasCache:
    """Tests for _get_extras caching behavior."""

    def test_cache_is_used(self):
        """After first call, subsequent calls should return cached result."""
        import gptme.info

        # Reset cache
        gptme.info._EXTRAS_CACHE = None
        with patch.object(gptme.info, "_parse_extras_from_metadata") as mock_parse:
            mock_parse.return_value = [
                ExtraInfo(name="test", installed=False, description="test")
            ]
            result1 = gptme.info._get_extras()
            result2 = gptme.info._get_extras()
            # Should only parse once
            mock_parse.assert_called_once()
            assert result1 is result2

        # Clean up
        gptme.info._EXTRAS_CACHE = None


# ─── get_install_info tests ──────────────────────────────────────────────────


class TestGetInstallInfo:
    """Tests for get_install_info — installation method detection."""

    def _mock_dist(
        self,
        installer="pip",
        direct_url_json=None,
        dist_type_name="Distribution",
    ):
        dist = MagicMock()

        def read_text(name):
            if name == "INSTALLER":
                return installer
            if name == "direct_url.json":
                return direct_url_json
            return None

        dist.read_text = read_text
        type(dist).__name__ = dist_type_name
        return dist

    @patch("gptme.info.importlib.metadata.distribution")
    def test_uv_installer(self, mock_dist_fn):
        mock_dist_fn.return_value = self._mock_dist(installer="uv")
        info = get_install_info()
        assert info.method == "uv"
        assert info.editable is False

    @patch("gptme.info.importlib.metadata.distribution")
    def test_pip_installer(self, mock_dist_fn):
        mock_dist_fn.return_value = self._mock_dist(installer="pip")
        info = get_install_info()
        assert info.method == "pip"

    @patch("gptme.info.importlib.metadata.distribution")
    def test_poetry_installer(self, mock_dist_fn):
        mock_dist_fn.return_value = self._mock_dist(installer="poetry")
        info = get_install_info()
        assert info.method == "poetry"

    @patch("gptme.info.importlib.metadata.distribution")
    def test_pipx_detection(self, mock_dist_fn):
        """pip installer with pipx in path should be detected as pipx."""
        url_json = json.dumps(
            {
                "url": "file:///home/user/.local/pipx/venvs/gptme",
                "dir_info": {"editable": False},
            }
        )
        mock_dist_fn.return_value = self._mock_dist(
            installer="pip", direct_url_json=url_json
        )
        info = get_install_info()
        assert info.method == "pipx"
        assert info.path == "/home/user/.local/pipx/venvs/gptme"

    @patch("gptme.info.importlib.metadata.distribution")
    def test_editable_install(self, mock_dist_fn):
        url_json = json.dumps(
            {
                "url": "file:///home/user/dev/gptme",
                "dir_info": {"editable": True},
            }
        )
        mock_dist_fn.return_value = self._mock_dist(
            installer="uv", direct_url_json=url_json
        )
        info = get_install_info()
        assert info.editable is True
        assert info.path == "/home/user/dev/gptme"

    @patch("gptme.info.importlib.metadata.distribution")
    def test_path_distribution_editable(self, mock_dist_fn):
        """PathDistribution type name indicates editable install."""
        mock_dist_fn.return_value = self._mock_dist(
            installer="pip", dist_type_name="PathDistribution"
        )
        info = get_install_info()
        assert info.editable is True

    @patch("gptme.info.importlib.metadata.distribution")
    def test_package_not_found(self, mock_dist_fn):
        mock_dist_fn.side_effect = importlib.metadata.PackageNotFoundError
        info = get_install_info()
        assert info.method == "unknown"
        assert info.editable is False
        assert info.path is None

    @patch("gptme.info.importlib.metadata.distribution")
    def test_installer_read_fails(self, mock_dist_fn):
        """If reading INSTALLER fails, should fallback gracefully."""
        dist = MagicMock()

        def read_text(name):
            if name == "INSTALLER":
                raise OSError("read failed")
            return

        dist.read_text = read_text
        type(dist).__name__ = "Distribution"
        mock_dist_fn.return_value = dist
        info = get_install_info()
        assert info.method == "unknown"

    @patch("gptme.info.importlib.metadata.distribution")
    def test_empty_installer(self, mock_dist_fn):
        mock_dist_fn.return_value = self._mock_dist(installer="")
        info = get_install_info()
        assert info.method == "unknown"

    @patch("gptme.info.importlib.metadata.distribution")
    def test_url_without_file_prefix(self, mock_dist_fn):
        """URL without file:// prefix should not set path."""
        url_json = json.dumps(
            {
                "url": "https://example.com/gptme",
                "dir_info": {"editable": False},
            }
        )
        mock_dist_fn.return_value = self._mock_dist(
            installer="pip", direct_url_json=url_json
        )
        info = get_install_info()
        assert info.path is None


# ─── get_installed_extras tests ──────────────────────────────────────────────


class TestGetInstalledExtras:
    """Tests for get_installed_extras."""

    @patch("gptme.info._get_extras")
    @patch("gptme.info._is_package_installed")
    def test_installed_extra(self, mock_installed, mock_extras):
        mock_extras.return_value = [
            ExtraInfo(
                name="browser",
                installed=False,
                description="Web browsing",
                packages=["playwright"],
            )
        ]
        mock_installed.return_value = True
        result = get_installed_extras()
        assert len(result) == 1
        assert result[0].installed is True

    @patch("gptme.info._get_extras")
    @patch("gptme.info._is_package_installed")
    def test_not_installed_extra(self, mock_installed, mock_extras):
        mock_extras.return_value = [
            ExtraInfo(
                name="browser",
                installed=False,
                description="Web browsing",
                packages=["playwright"],
            )
        ]
        mock_installed.return_value = False
        result = get_installed_extras()
        assert result[0].installed is False

    @patch("gptme.info._get_extras")
    @patch("gptme.info._is_package_installed")
    def test_extra_with_no_packages(self, mock_installed, mock_extras):
        """Extras with empty packages list should always be not installed."""
        mock_extras.return_value = [
            ExtraInfo(
                name="computer", installed=False, description="Computer", packages=[]
            )
        ]
        result = get_installed_extras()
        assert result[0].installed is False
        mock_installed.assert_not_called()

    @patch("gptme.info._get_extras")
    @patch("gptme.info._is_package_installed")
    def test_any_package_makes_installed(self, mock_installed, mock_extras):
        """If any package in the list is installed, extra is installed."""
        mock_extras.return_value = [
            ExtraInfo(
                name="datascience",
                installed=False,
                description="Data science",
                packages=["numpy", "pandas", "matplotlib"],
            )
        ]
        # numpy installed, others not
        mock_installed.side_effect = [True]
        result = get_installed_extras()
        assert result[0].installed is True


# ─── get_available_providers tests ───────────────────────────────────────────


class TestGetAvailableProviders:
    """Tests for get_available_providers."""

    def test_returns_provider_names(self):
        with patch("gptme.llm.list_available_providers") as mock_providers:
            mock_providers.return_value = [
                ("openai", MagicMock()),
                ("anthropic", MagicMock()),
            ]
            result = get_available_providers()
            assert result == ["openai", "anthropic"]

    def test_error_returns_empty(self):
        with patch(
            "gptme.llm.list_available_providers", side_effect=RuntimeError("error")
        ):
            result = get_available_providers()
            assert result == []


# ─── get_default_model tests ────────────────────────────────────────────────


class TestGetDefaultModel:
    """Tests for get_default_model."""

    def test_returns_model_string(self):
        mock_config = MagicMock()
        mock_config.get_env.return_value = "claude-sonnet-4-6"
        with patch("gptme.config.get_config", return_value=mock_config):
            result = get_default_model()
            assert result == "claude-sonnet-4-6"

    def test_returns_none_when_not_set(self):
        mock_config = MagicMock()
        mock_config.get_env.return_value = ""
        with patch("gptme.config.get_config", return_value=mock_config):
            result = get_default_model()
            assert result is None

    def test_returns_none_on_error(self):
        with patch("gptme.config.get_config", side_effect=RuntimeError):
            result = get_default_model()
            assert result is None


# ─── get_tool_count tests ───────────────────────────────────────────────────


class TestGetToolCount:
    """Tests for get_tool_count."""

    def test_counts_available_tools(self):
        mock_tools = [
            SimpleNamespace(is_available=True),
            SimpleNamespace(is_available=True),
            SimpleNamespace(is_available=False),
        ]
        with patch("gptme.tools.get_available_tools", return_value=mock_tools):
            result = get_tool_count()
            assert result == 2

    def test_error_returns_zero(self):
        with patch("gptme.tools.get_available_tools", side_effect=RuntimeError):
            result = get_tool_count()
            assert result == 0


# ─── get_quick_health tests ─────────────────────────────────────────────────


class TestGetQuickHealth:
    """Tests for get_quick_health — health check counting."""

    @patch("gptme.info.get_config_info")
    @patch("gptme.info.get_available_providers")
    @patch("shutil.which")
    def test_all_healthy(self, mock_which, mock_providers, mock_config):
        mock_which.return_value = "/usr/bin/tool"  # all tools found
        mock_providers.return_value = ["openai"]
        mock_config.return_value = {"config_exists": True}
        ok, warnings, errors = get_quick_health()
        assert ok >= 4  # 2 required + 2 optional at minimum
        assert errors == 0

    @patch("gptme.info.get_config_info")
    @patch("gptme.info.get_available_providers")
    @patch("shutil.which")
    def test_missing_required_tool(self, mock_which, mock_providers, mock_config):
        def which_side_effect(name):
            if name in ("python3", "git"):
                return None  # required tools missing
            return "/usr/bin/" + name

        mock_which.side_effect = which_side_effect
        mock_providers.return_value = ["openai"]
        mock_config.return_value = {"config_exists": True}
        ok, warnings, errors = get_quick_health()
        assert errors == 2  # both required tools missing

    @patch("gptme.info.get_config_info")
    @patch("gptme.info.get_available_providers")
    @patch("shutil.which")
    def test_missing_optional_tool(self, mock_which, mock_providers, mock_config):
        def which_side_effect(name):
            if name in ("gh", "tmux"):
                return None  # optional tools missing
            return "/usr/bin/" + name

        mock_which.side_effect = which_side_effect
        mock_providers.return_value = ["openai"]
        mock_config.return_value = {"config_exists": True}
        ok, warnings, errors = get_quick_health()
        assert warnings >= 2  # optional tools generate warnings, not errors
        assert errors == 0

    @patch("gptme.info.get_config_info")
    @patch("gptme.info.get_available_providers")
    @patch("shutil.which")
    def test_no_providers(self, mock_which, mock_providers, mock_config):
        mock_which.return_value = "/usr/bin/tool"
        mock_providers.return_value = []  # no providers
        mock_config.return_value = {"config_exists": True}
        ok, warnings, errors = get_quick_health()
        assert warnings >= 1  # missing providers is a warning

    @patch("gptme.info.get_config_info")
    @patch("gptme.info.get_available_providers")
    @patch("shutil.which")
    def test_no_config(self, mock_which, mock_providers, mock_config):
        mock_which.return_value = "/usr/bin/tool"
        mock_providers.return_value = ["openai"]
        mock_config.return_value = {"config_exists": False}
        ok, warnings, errors = get_quick_health()
        assert warnings >= 1  # missing config is a warning


# ─── get_system_info tests ──────────────────────────────────────────────────


class TestGetSystemInfo:
    """Tests for get_system_info."""

    def test_returns_expected_keys(self):
        info = get_system_info()
        assert "python_version" in info
        assert "platform" in info
        assert "platform_version" in info
        assert "machine" in info

    def test_python_version_format(self):
        info = get_system_info()
        # Python version should look like "3.x.y"
        parts = info["python_version"].split(".")
        assert len(parts) >= 2
        assert int(parts[0]) >= 3


# ─── get_config_info tests ──────────────────────────────────────────────────


class TestGetConfigInfo:
    """Tests for get_config_info."""

    def test_returns_expected_keys(self):
        info = get_config_info()
        assert "logs_dir" in info
        assert "config_path" in info
        assert "config_exists" in info

    def test_config_exists_is_bool(self):
        info = get_config_info()
        assert isinstance(info["config_exists"], bool)

    @patch("gptme.config.get_project_config")
    def test_project_config_detected(self, mock_project_cfg, tmp_path):
        """When gptme.toml exists, project_config should be set."""
        (tmp_path / "gptme.toml").write_text("[prompt]\n")
        mock_project_cfg.return_value = MagicMock()
        with patch("gptme.info.Path.cwd", return_value=tmp_path):
            info = get_config_info()
            assert "project_config" in info
            assert "gptme.toml" in info["project_config"]


# ─── format_version_info tests ──────────────────────────────────────────────


class TestFormatVersionInfo:
    """Tests for format_version_info — both human-readable and JSON."""

    def _mock_all_info(self):
        """Set up mocks for all info-gathering functions."""
        patches = {
            "gptme.info.get_system_info": {
                "python_version": "3.12.0",
                "platform": "Linux",
                "platform_version": "6.1.0",
                "machine": "x86_64",
            },
            "gptme.info.get_config_info": {
                "logs_dir": "/tmp/logs",
                "config_path": "/tmp/config.toml",
                "config_exists": True,
            },
            "gptme.info.get_install_info": InstallInfo(
                method="uv", editable=True, path="/dev/gptme"
            ),
            "gptme.info.get_installed_extras": [
                ExtraInfo(name="browser", installed=True, description="Browsing"),
                ExtraInfo(name="server", installed=False, description="Server"),
            ],
            "gptme.info.get_available_providers": ["openai", "anthropic"],
            "gptme.info.get_default_model": "claude-sonnet-4-6",
            "gptme.info.get_tool_count": 15,
            "gptme.info.get_quick_health": (6, 0, 0),
        }
        return patches

    def _apply_patches(self, patches):
        """Apply multiple patches and return context managers."""
        managers = []
        for target, value in patches.items():
            p = patch(target, return_value=value)
            managers.append(p)
            p.start()
        return managers

    def _stop_patches(self, managers):
        for m in managers:
            m.stop()

    def test_human_readable_contains_version(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "gptme v" in result
        finally:
            self._stop_patches(managers)

    def test_human_readable_contains_python(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "Python 3.12.0" in result
        finally:
            self._stop_patches(managers)

    def test_human_readable_editable_indicator(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "(editable)" in result
        finally:
            self._stop_patches(managers)

    def test_human_readable_model(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "claude-sonnet-4-6" in result
        finally:
            self._stop_patches(managers)

    def test_human_readable_installed_extras(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "✓ browser" in result
        finally:
            self._stop_patches(managers)

    def test_human_readable_providers(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "openai" in result
        finally:
            self._stop_patches(managers)

    def test_human_readable_tools_count(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "15 available" in result
        finally:
            self._stop_patches(managers)

    def test_human_readable_health_ok(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "All good" in result
        finally:
            self._stop_patches(managers)

    def test_human_readable_health_warnings(self):
        patches = self._mock_all_info()
        patches["gptme.info.get_quick_health"] = (4, 2, 0)
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "2 warnings" in result
            assert "gptme-doctor" in result
        finally:
            self._stop_patches(managers)

    def test_human_readable_health_errors(self):
        patches = self._mock_all_info()
        patches["gptme.info.get_quick_health"] = (3, 1, 2)
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "2 errors" in result
        finally:
            self._stop_patches(managers)

    def test_verbose_shows_not_installed(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            result = format_version_info(verbose=True)
            assert "✗ server" in result
        finally:
            self._stop_patches(managers)

    def test_verbose_shows_config_path(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            result = format_version_info(verbose=True)
            assert "Config:" in result
        finally:
            self._stop_patches(managers)

    def test_verbose_shows_source_path(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            result = format_version_info(verbose=True)
            assert "/dev/gptme" in result
        finally:
            self._stop_patches(managers)

    def test_json_output_valid(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            result = format_version_info(output_json=True)
            data = json.loads(result)
            assert "version" in data
            assert "python" in data
            assert "platform" in data
        finally:
            self._stop_patches(managers)

    def test_json_output_install_section(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            data = json.loads(format_version_info(output_json=True))
            assert data["install"]["method"] == "uv"
            assert data["install"]["editable"] is True
            assert data["install"]["path"] == "/dev/gptme"
        finally:
            self._stop_patches(managers)

    def test_json_output_extras_section(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            data = json.loads(format_version_info(output_json=True))
            assert "browser" in data["extras"]["installed"]
            assert "server" in data["extras"]["not_installed"]
        finally:
            self._stop_patches(managers)

    def test_json_output_health_section(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            data = json.loads(format_version_info(output_json=True))
            assert data["health"]["ok"] == 6
            assert data["health"]["warnings"] == 0
            assert data["health"]["errors"] == 0
        finally:
            self._stop_patches(managers)

    def test_json_output_providers(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            data = json.loads(format_version_info(output_json=True))
            assert data["providers"] == ["openai", "anthropic"]
        finally:
            self._stop_patches(managers)

    def test_json_output_model(self):
        patches = self._mock_all_info()
        managers = self._apply_patches(patches)
        try:
            data = json.loads(format_version_info(output_json=True))
            assert data["default_model"] == "claude-sonnet-4-6"
        finally:
            self._stop_patches(managers)

    def test_no_model_hides_line(self):
        patches = self._mock_all_info()
        patches["gptme.info.get_default_model"] = None
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "Model:" not in result
        finally:
            self._stop_patches(managers)

    def test_no_tools_hides_line(self):
        patches = self._mock_all_info()
        patches["gptme.info.get_tool_count"] = 0
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "Tools:" not in result
        finally:
            self._stop_patches(managers)

    def test_many_providers_shows_count(self):
        """More than 3 providers in non-verbose mode shows count only."""
        patches = self._mock_all_info()
        patches["gptme.info.get_available_providers"] = [
            "openai",
            "anthropic",
            "google",
            "local",
        ]
        managers = self._apply_patches(patches)
        try:
            result = format_version_info(verbose=False)
            assert "4 configured" in result
        finally:
            self._stop_patches(managers)

    def test_many_providers_verbose_lists_all(self):
        """Verbose mode always lists provider names."""
        patches = self._mock_all_info()
        patches["gptme.info.get_available_providers"] = [
            "openai",
            "anthropic",
            "google",
            "local",
        ]
        managers = self._apply_patches(patches)
        try:
            result = format_version_info(verbose=True)
            assert "openai" in result
            assert "anthropic" in result
        finally:
            self._stop_patches(managers)

    def test_not_editable_no_indicator(self):
        patches = self._mock_all_info()
        patches["gptme.info.get_install_info"] = InstallInfo(
            method="pip", editable=False
        )
        managers = self._apply_patches(patches)
        try:
            result = format_version_info()
            assert "(editable)" not in result
        finally:
            self._stop_patches(managers)

    def test_no_extras_installed_hides_section(self):
        """When no extras installed and not verbose, hide Extras section."""
        patches = self._mock_all_info()
        patches["gptme.info.get_installed_extras"] = [
            ExtraInfo(name="browser", installed=False, description="Browsing"),
        ]
        managers = self._apply_patches(patches)
        try:
            result = format_version_info(verbose=False)
            assert "Extras:" not in result
        finally:
            self._stop_patches(managers)

    def test_no_extras_verbose_shows_section(self):
        """Verbose mode shows Extras even when none installed."""
        patches = self._mock_all_info()
        patches["gptme.info.get_installed_extras"] = [
            ExtraInfo(name="browser", installed=False, description="Browsing"),
        ]
        managers = self._apply_patches(patches)
        try:
            result = format_version_info(verbose=True)
            assert "Extras:" in result
            assert "✗ browser" in result
        finally:
            self._stop_patches(managers)

    def test_verbose_shows_project_config(self):
        patches = self._mock_all_info()
        patches["gptme.info.get_config_info"] = {
            "logs_dir": "/tmp/logs",
            "config_path": "/tmp/config.toml",
            "config_exists": True,
            "project_config": "/home/user/project/gptme.toml",
        }
        managers = self._apply_patches(patches)
        try:
            result = format_version_info(verbose=True)
            assert "Project:" in result
            assert "/home/user/project/gptme.toml" in result
        finally:
            self._stop_patches(managers)


# ─── Edge cases and integration ─────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and cross-function behavior."""

    @patch("gptme.info.importlib.metadata.distribution")
    def test_parse_extras_with_none_requires(self, mock_dist):
        """dist.requires can be None."""
        dist = MagicMock()
        dist.metadata = MagicMock()
        dist.metadata.get_all.return_value = ["browser"]
        dist.requires = None
        mock_dist.return_value = dist
        result = _parse_extras_from_metadata()
        assert len(result) == 1
        assert result[0].packages == []

    def test_internal_extras_set(self):
        """Verify internal extras haven't changed unexpectedly."""
        assert "all" in _INTERNAL_EXTRAS
        assert "eval" in _INTERNAL_EXTRAS
        assert "pyinstaller" in _INTERNAL_EXTRAS

    def test_extra_descriptions_keys(self):
        """Known extras should have descriptions."""
        assert "browser" in _EXTRA_DESCRIPTIONS
        assert "server" in _EXTRA_DESCRIPTIONS

    @patch("gptme.info.importlib.metadata.distribution")
    def test_install_info_null_installer(self, mock_dist_fn):
        """INSTALLER returning None should be handled."""
        dist = MagicMock()
        dist.read_text = lambda name: None
        type(dist).__name__ = "Distribution"
        mock_dist_fn.return_value = dist
        info = get_install_info()
        assert info.method == "unknown"
