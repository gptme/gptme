"""Tests for the gptme doctor command."""

import json
from unittest.mock import patch

from click.testing import CliRunner

from gptme.cli.doctor import (
    CheckResult,
    CheckStatus,
    _check_api_keys,
    _check_browser,
    _check_config,
    _check_mcp,
    _check_permissions,
    _check_python_deps,
    _check_python_version,
    _check_tools,
    _check_version,
    main,
    run_diagnostics,
)
from gptme.config import MCPConfig, MCPServerConfig


class TestCheckStatus:
    """Test CheckStatus enum."""

    def test_all_statuses_exist(self):
        """Verify all expected statuses exist."""
        assert CheckStatus.OK
        assert CheckStatus.WARNING
        assert CheckStatus.ERROR
        assert CheckStatus.SKIPPED


class TestCheckResult:
    """Test CheckResult dataclass."""

    def test_basic_result(self):
        """Test creating a basic check result."""
        result = CheckResult(
            name="Test Check",
            status=CheckStatus.OK,
            message="All good",
        )
        assert result.name == "Test Check"
        assert result.status == CheckStatus.OK
        assert result.message == "All good"
        assert result.details is None
        assert result.fix_hint is None

    def test_result_with_all_fields(self):
        """Test creating a check result with all fields."""
        result = CheckResult(
            name="Test Check",
            status=CheckStatus.ERROR,
            message="Something wrong",
            details="Detailed info",
            fix_hint="Try this fix",
        )
        assert result.details == "Detailed info"
        assert result.fix_hint == "Try this fix"


class TestCheckPythonVersion:
    """Test _check_python_version function."""

    def test_current_python_passes(self):
        """Test that the running Python version (must be >=3.10 to run gptme) is OK."""
        results = _check_python_version()
        assert len(results) == 1
        assert results[0].status == CheckStatus.OK
        assert "Python" in results[0].name

    def test_old_python_fails(self):
        """Test that Python < 3.10 produces an ERROR."""
        from unittest.mock import MagicMock

        old_version = MagicMock()
        old_version.major = 3
        old_version.minor = 9
        old_version.micro = 0
        old_version.__ge__ = lambda self, other: other <= (3, 9, 0)
        with patch("gptme.cli.doctor.sys.version_info", old_version):
            results = _check_python_version()
        assert len(results) == 1
        assert results[0].status == CheckStatus.ERROR
        assert "3.9.0" in results[0].message
        assert results[0].fix_hint is not None

    def test_minimum_python_passes(self):
        """Test that exactly Python 3.10 is accepted."""
        from unittest.mock import MagicMock

        min_version = MagicMock()
        min_version.major = 3
        min_version.minor = 10
        min_version.micro = 0
        min_version.__ge__ = lambda self, other: other <= (3, 10, 0)
        with patch("gptme.cli.doctor.sys.version_info", min_version):
            results = _check_python_version()
        assert len(results) == 1
        assert results[0].status == CheckStatus.OK

    def test_verbose_shows_executable(self):
        """Test that verbose mode shows the Python executable path."""
        results = _check_python_version(verbose=True)
        assert len(results) == 1
        assert results[0].details is not None


class TestCheckVersion:
    """Test _check_version function."""

    def test_returns_results(self):
        """Test that version check returns results."""
        results = _check_version()
        assert len(results) == 1
        assert "Version" in results[0].name

    @patch("gptme.cli.doctor.__version__", "0.31.0")
    def test_dev_install_skips_pypi(self):
        """Test that dev installs skip PyPI check."""
        with patch("importlib.metadata.version", return_value="0.31.0.dev123"):
            results = _check_version()
            assert len(results) == 1
            assert results[0].status == CheckStatus.OK
            assert "Development" in results[0].message

    @patch("gptme.cli.doctor.__version__", "0.31.0")
    def test_up_to_date(self):
        """Test that matching version shows OK."""
        import io
        import json as json_mod

        mock_resp = io.BytesIO(json_mod.dumps({"info": {"version": "0.31.0"}}).encode())
        mock_cm = patch("urllib.request.urlopen")
        with (
            patch("importlib.metadata.version", return_value="0.31.0"),
            mock_cm as mock_urlopen,
        ):
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = lambda s, *a: None

            results = _check_version()
            assert len(results) == 1
            assert results[0].status == CheckStatus.OK
            assert "Up to date" in results[0].message

    @patch("gptme.cli.doctor.__version__", "0.30.0")
    def test_update_available(self):
        """Test that newer version triggers warning."""
        import io
        import json as json_mod

        mock_resp = io.BytesIO(json_mod.dumps({"info": {"version": "0.31.0"}}).encode())
        mock_cm = patch("urllib.request.urlopen")
        with (
            patch("importlib.metadata.version", return_value="0.30.0"),
            mock_cm as mock_urlopen,
        ):
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = lambda s, *a: None

            results = _check_version()
            assert len(results) == 1
            assert results[0].status == CheckStatus.WARNING
            assert "0.31.0" in results[0].message
            assert results[0].fix_hint is not None

    @patch("gptme.cli.doctor.__version__", "0.32.0")
    def test_current_ahead_of_pypi(self):
        """Test that installed version newer than PyPI shows OK (no spurious warning)."""
        import io
        import json as json_mod

        mock_resp = io.BytesIO(json_mod.dumps({"info": {"version": "0.31.0"}}).encode())
        mock_cm = patch("urllib.request.urlopen")
        with (
            patch("importlib.metadata.version", return_value="0.32.0"),
            mock_cm as mock_urlopen,
        ):
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = lambda s, *a: None

            results = _check_version()
            assert len(results) == 1
            assert results[0].status == CheckStatus.OK
            assert results[0].fix_hint is None
            assert "0.32.0" in results[0].message

    @patch("gptme.cli.doctor.__version__", "0.31.0")
    def test_network_error_graceful(self):
        """Test that network errors are handled gracefully."""
        with (
            patch("importlib.metadata.version", return_value="0.31.0"),
            patch("urllib.request.urlopen", side_effect=Exception("Network error")),
        ):
            results = _check_version()
            assert len(results) == 1
            # Should still report OK (installed version) not ERROR
            assert results[0].status == CheckStatus.OK
            assert "0.31.0" in results[0].message


class TestCheckBrowser:
    """Test _check_browser function."""

    @patch("importlib.util.find_spec", return_value=None)
    def test_no_playwright_returns_empty(self, mock_find):
        """Test that missing playwright returns no results."""
        results = _check_browser()
        assert len(results) == 0

    @patch("importlib.util.find_spec", return_value=True)
    def test_playwright_no_browsers(self, mock_find, tmp_path):
        """Test warning when playwright installed but no browsers."""
        with patch.dict("os.environ", {"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path)}):
            results = _check_browser()
            assert len(results) == 1
            assert results[0].status == CheckStatus.WARNING
            assert "no browsers" in results[0].message.lower()
            assert results[0].fix_hint is not None

    @patch("importlib.util.find_spec", return_value=True)
    def test_playwright_with_browsers(self, mock_find, tmp_path):
        """Test OK when playwright has browsers installed."""
        # Create fake browser directories
        (tmp_path / "chromium-1148").mkdir()
        (tmp_path / "firefox-1460").mkdir()

        with patch.dict("os.environ", {"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path)}):
            results = _check_browser()
            assert len(results) == 1
            assert results[0].status == CheckStatus.OK
            assert "2 browser(s)" in results[0].message


class TestCheckTools:
    """Test _check_tools function."""

    def test_finds_required_tools(self):
        """Test that required tools are checked."""
        results = _check_tools()

        # Should always check python3 and git
        tool_names = [r.name for r in results]
        assert any("python3" in name for name in tool_names)
        assert any("git" in name for name in tool_names)

    def test_python3_found(self):
        """Test that python3 is found (we're running in Python!)."""
        results = _check_tools()
        python_results = [r for r in results if "python3" in r.name]

        assert len(python_results) == 1
        assert python_results[0].status == CheckStatus.OK

    @patch("shutil.which")
    def test_missing_tool_warning(self, mock_which):
        """Test that missing optional tools produce warnings."""

        # Make all tools except python3 and git missing
        def which_side_effect(tool):
            if tool in ("python3", "git"):
                return f"/usr/bin/{tool}"
            return None

        mock_which.side_effect = which_side_effect

        results = _check_tools()

        # Optional tools should have warnings
        optional_results = [
            r for r in results if r.name not in ("Tool: python3", "Tool: git")
        ]
        for r in optional_results:
            assert r.status in (CheckStatus.WARNING, CheckStatus.OK)

    @patch("shutil.which")
    def test_missing_required_tool_error(self, mock_which):
        """Test that missing required tools produce errors."""

        def which_side_effect(tool):
            if tool == "git":
                return None  # git not found
            if tool == "python3":
                return "/usr/bin/python3"
            return None

        mock_which.side_effect = which_side_effect

        results = _check_tools()

        git_result = next(r for r in results if "git" in r.name)
        assert git_result.status == CheckStatus.ERROR
        assert git_result.fix_hint is not None


class TestCheckPythonDeps:
    """Test _check_python_deps function."""

    def test_returns_results(self):
        """Test that function returns results."""
        results = _check_python_deps()
        assert len(results) > 0

    def test_checks_known_deps(self):
        """Test that known optional deps are checked."""
        results = _check_python_deps()
        dep_names = [r.name for r in results]

        # Should check common optional deps (extras)
        # Names from info.py EXTRAS list (synced with pyproject.toml)
        assert any("browser" in name for name in dep_names)
        assert any("dspy" in name for name in dep_names)


class TestCheckConfig:
    """Test _check_config function."""

    def test_returns_results(self):
        """Test that function returns results."""
        results = _check_config()
        assert len(results) > 0

    def test_checks_user_config(self):
        """Test that user config is checked."""
        results = _check_config()
        config_results = [r for r in results if "User" in r.name]
        assert len(config_results) == 1


class TestCheckPermissions:
    """Test _check_permissions function."""

    def test_returns_results(self):
        """Test that function returns results."""
        results = _check_permissions()
        assert len(results) > 0

    def test_checks_logs_permissions(self):
        """Test that logs permissions are checked."""
        results = _check_permissions()
        logs_results = [r for r in results if "Logs" in r.name]
        assert len(logs_results) == 1


class TestRunDiagnostics:
    """Test run_diagnostics function."""

    def test_returns_results_and_summary(self):
        """Test that function returns results and summary."""
        results, summary = run_diagnostics()

        assert isinstance(results, list)
        assert isinstance(summary, dict)
        assert len(results) > 0

    def test_summary_has_expected_keys(self):
        """Test that summary has all expected keys."""
        _, summary = run_diagnostics()

        assert "total" in summary
        assert "ok" in summary
        assert "warning" in summary
        assert "error" in summary
        assert "skipped" in summary

    def test_summary_counts_match(self):
        """Test that summary counts add up to total."""
        results, summary = run_diagnostics()

        counted_total = (
            summary["ok"] + summary["warning"] + summary["error"] + summary["skipped"]
        )
        assert summary["total"] == counted_total
        assert summary["total"] == len(results)


class TestCLI:
    """Test CLI interface."""

    def test_cli_runs(self):
        """Test that CLI runs without error."""
        runner = CliRunner()
        result = runner.invoke(main, [])

        # Should complete (exit code 0 or 1 depending on system state)
        assert result.exit_code in (0, 1)

    def test_cli_verbose(self):
        """Test verbose flag works."""
        runner = CliRunner()
        result = runner.invoke(main, ["--verbose"])

        assert result.exit_code in (0, 1)
        # Verbose should show more details (paths, fix hints)

    def test_cli_json_output(self):
        """Test JSON output flag works."""
        runner = CliRunner()
        result = runner.invoke(main, ["--json"])

        assert result.exit_code in (0, 1)

        # Output should be valid JSON
        output = json.loads(result.output)
        assert "summary" in output
        assert "results" in output
        assert isinstance(output["results"], list)

    def test_cli_json_structure(self):
        """Test JSON output has correct structure."""
        runner = CliRunner()
        result = runner.invoke(main, ["--json"])

        output = json.loads(result.output)

        # Check summary structure
        summary = output["summary"]
        assert "total" in summary
        assert "ok" in summary

        # Check results structure
        if output["results"]:
            first_result = output["results"][0]
            assert "name" in first_result
            assert "status" in first_result
            assert "message" in first_result


class TestCheckApiKeys:
    """Test _check_api_keys function."""

    @patch("gptme.cli.doctor.list_available_providers")
    @patch("gptme.cli.doctor.get_config")
    @patch("gptme.cli.doctor.validate_api_key")
    @patch.dict("os.environ", {}, clear=True)
    def test_valid_api_key(self, mock_validate, mock_config, mock_providers):
        """Test that valid API keys are reported as OK."""

        # Setup mocks
        mock_providers.return_value = [("openai", None)]
        mock_config_obj = mock_config.return_value
        mock_config_obj.get_env.return_value = "sk-test1234567890"
        mock_validate.return_value = (True, None)

        results = _check_api_keys()

        # Find openai result
        openai_results = [r for r in results if "openai" in r.name.lower()]
        assert len(openai_results) >= 1
        openai_result = openai_results[0]
        assert openai_result.status == CheckStatus.OK
        assert "valid" in openai_result.message.lower()

    @patch("gptme.cli.doctor.list_available_providers")
    @patch("gptme.cli.doctor.get_config")
    @patch("gptme.cli.doctor.validate_api_key")
    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-invalid"}, clear=True)
    def test_invalid_api_key(self, mock_validate, mock_config, mock_providers):
        """Test that invalid API keys are reported as ERROR."""

        # Setup mocks
        mock_providers.return_value = [("openai", None)]
        mock_config_obj = mock_config.return_value
        mock_config_obj.get_env.return_value = None
        mock_validate.return_value = (False, "Invalid key format")

        results = _check_api_keys()

        # Find openai result
        openai_results = [r for r in results if "openai" in r.name.lower()]
        assert len(openai_results) >= 1
        openai_result = openai_results[0]
        assert openai_result.status == CheckStatus.ERROR
        assert "invalid" in openai_result.message.lower()
        assert openai_result.fix_hint is not None

    @patch("gptme.cli.doctor.list_available_providers")
    @patch("gptme.cli.doctor.get_config")
    @patch("gptme.cli.doctor.validate_api_key")
    @patch.dict("os.environ", {}, clear=True)
    def test_quota_exhausted_api_key(self, mock_validate, mock_config, mock_providers):
        """Test that quota-exhausted API keys are reported as WARNING, not OK."""

        mock_providers.return_value = [("anthropic", None)]
        mock_config_obj = mock_config.return_value
        mock_config_obj.get_env.return_value = "sk-ant-test1234567890"
        quota_warning = (
            "API quota exhausted — You have reached your specified API usage limits."
        )
        mock_validate.return_value = (True, quota_warning)

        results = _check_api_keys()

        anthropic_results = [r for r in results if "anthropic" in r.name.lower()]
        assert len(anthropic_results) >= 1
        result = anthropic_results[0]
        assert result.status == CheckStatus.WARNING
        assert (
            "quota" in result.message.lower()
            or "usage limits" in result.message.lower()
        )

    @patch("gptme.cli.doctor.list_available_providers")
    @patch("gptme.cli.doctor.get_config")
    @patch.dict("os.environ", {}, clear=True)
    def test_provider_available_but_key_not_retrievable(
        self, mock_config, mock_providers
    ):
        """Test that providers available but with non-retrievable keys show WARNING."""

        # Setup: provider is available but we can't get the key via env or config
        mock_providers.return_value = [("openai", None)]
        mock_config_obj = mock_config.return_value
        mock_config_obj.get_env.return_value = None

        results = _check_api_keys()

        # Find openai result
        openai_results = [r for r in results if "openai" in r.name.lower()]
        assert len(openai_results) >= 1
        openai_result = openai_results[0]
        assert openai_result.status == CheckStatus.WARNING
        assert "not retrievable" in openai_result.message.lower()

    @patch("gptme.cli.doctor.list_available_providers")
    @patch("gptme.cli.doctor.get_config")
    @patch.dict("os.environ", {}, clear=True)
    def test_unconfigured_provider_skipped(self, mock_config, mock_providers):
        """Test that unconfigured providers are reported as SKIPPED."""

        # Setup: no providers available
        mock_providers.return_value = []
        mock_config_obj = mock_config.return_value
        mock_config_obj.get_env.return_value = None

        results = _check_api_keys()

        # All provider results should be SKIPPED
        for result in results:
            if "API Key:" in result.name:
                assert result.status == CheckStatus.SKIPPED
                assert "not configured" in result.message.lower()

    @patch("gptme.cli.doctor.list_available_providers")
    @patch("gptme.cli.doctor.get_config")
    @patch.dict("os.environ", {"AZURE_OPENAI_API_KEY": "test-key"}, clear=True)
    def test_azure_uses_special_env_var(self, mock_config, mock_providers):
        """Test that Azure uses AZURE_OPENAI_API_KEY (special case)."""

        # Setup: azure provider available
        mock_providers.return_value = [("azure", None)]
        mock_config_obj = mock_config.return_value
        mock_config_obj.get_env.return_value = None

        with patch("gptme.cli.doctor.validate_api_key") as mock_validate:
            mock_validate.return_value = (True, None)
            results = _check_api_keys()

            # Find azure result
            azure_results = [r for r in results if "azure" in r.name.lower()]
            assert len(azure_results) >= 1
            azure_result = azure_results[0]
            # Should find the key and mark as OK
            assert azure_result.status == CheckStatus.OK

    @patch("gptme.cli.doctor.list_available_providers")
    @patch("gptme.cli.doctor.get_config")
    @patch.dict("os.environ", {}, clear=True)
    def test_oauth_provider_authenticated(self, mock_config, mock_providers):
        """OAuth providers should show as authenticated when token exists."""
        mock_providers.return_value = [("openai-subscription", "oauth")]
        mock_config.return_value.get_env.return_value = None

        results = _check_api_keys()

        # Find the openai-subscription result
        oauth_results = [r for r in results if "openai-subscription" in r.name]
        assert len(oauth_results) == 1
        assert oauth_results[0].status == CheckStatus.OK
        assert "Auth:" in oauth_results[0].name
        assert "OAuth" in oauth_results[0].message

    @patch("gptme.cli.doctor.list_available_providers")
    @patch("gptme.cli.doctor.get_config")
    @patch.dict("os.environ", {}, clear=True)
    def test_oauth_provider_not_authenticated(self, mock_config, mock_providers):
        """OAuth providers should show setup hint when not authenticated."""
        mock_providers.return_value = []
        mock_config.return_value.get_env.return_value = None

        results = _check_api_keys()

        # Find the openai-subscription result
        oauth_results = [r for r in results if "openai-subscription" in r.name]
        assert len(oauth_results) == 1
        assert oauth_results[0].status == CheckStatus.SKIPPED
        assert oauth_results[0].fix_hint is not None
        assert "gptme auth" in oauth_results[0].fix_hint

    @patch("gptme.cli.doctor.list_available_providers")
    @patch("gptme.cli.doctor.get_config")
    @patch("gptme.cli.doctor.validate_api_key")
    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test123"}, clear=True)
    def test_mixed_api_and_oauth_providers(
        self, mock_validate, mock_config, mock_providers
    ):
        """Both API key and OAuth providers should be checked correctly."""
        mock_providers.return_value = [
            ("openai", "OPENAI_API_KEY"),
            ("openai-subscription", "oauth"),
        ]
        mock_config.return_value.get_env.return_value = None
        mock_validate.return_value = (True, "")

        results = _check_api_keys()

        # API key provider should use "API Key:" prefix
        api_results = [r for r in results if r.name.startswith("API Key:")]
        openai_api = [r for r in api_results if "openai" in r.name]
        assert any(r.status == CheckStatus.OK for r in openai_api)

        # OAuth provider should use "Auth:" prefix
        auth_results = [r for r in results if r.name.startswith("Auth:")]
        assert len(auth_results) == 1
        assert auth_results[0].status == CheckStatus.OK


class TestCheckMCP:
    """Test _check_mcp function."""

    @patch("gptme.cli.doctor.get_config")
    def test_mcp_disabled(self, mock_config):
        """Test that disabled MCP is reported as SKIPPED."""
        mock_config.return_value.mcp = MCPConfig(enabled=False)

        results = _check_mcp()
        assert len(results) == 1
        assert results[0].status == CheckStatus.SKIPPED
        assert "not enabled" in results[0].message.lower()

    @patch("gptme.cli.doctor.get_config")
    def test_mcp_enabled_no_servers(self, mock_config):
        """Test MCP enabled but no servers configured."""
        mock_config.return_value.mcp = MCPConfig(enabled=True, servers=[])

        results = _check_mcp()
        assert len(results) == 1
        assert results[0].status == CheckStatus.OK
        assert "0 server(s)" in results[0].message

    @patch("gptme.cli.doctor.get_config")
    def test_mcp_disabled_server(self, mock_config):
        """Test that disabled servers are reported as SKIPPED."""
        server = MCPServerConfig(name="test-server", enabled=False, command="echo")
        mock_config.return_value.mcp = MCPConfig(enabled=True, servers=[server])

        results = _check_mcp()
        assert len(results) == 2  # status + server
        server_result = results[1]
        assert server_result.status == CheckStatus.SKIPPED
        assert "Disabled" in server_result.message

    @patch("shutil.which", return_value="/usr/bin/npx")
    @patch("gptme.cli.doctor.get_config")
    def test_mcp_stdio_server_found(self, mock_config, mock_which):
        """Test that stdio server with available command is OK."""
        server = MCPServerConfig(
            name="test-mcp", command="npx", args=["-y", "some-mcp-server"]
        )
        mock_config.return_value.mcp = MCPConfig(enabled=True, servers=[server])

        results = _check_mcp()
        server_result = [r for r in results if "test-mcp" in r.name][0]
        assert server_result.status == CheckStatus.OK
        assert "'npx' found" in server_result.message

    @patch("shutil.which", return_value=None)
    @patch("gptme.cli.doctor.get_config")
    def test_mcp_stdio_server_not_found(self, mock_config, mock_which):
        """Test that stdio server with missing command is ERROR."""
        server = MCPServerConfig(name="test-mcp", command="nonexistent-binary")
        mock_config.return_value.mcp = MCPConfig(enabled=True, servers=[server])

        results = _check_mcp()
        server_result = [r for r in results if "test-mcp" in r.name][0]
        assert server_result.status == CheckStatus.ERROR
        assert "not found" in server_result.message
        assert server_result.fix_hint is not None

    @patch("gptme.cli.doctor.get_config")
    def test_mcp_stdio_server_no_command(self, mock_config):
        """Test that stdio server with no command is ERROR."""
        server = MCPServerConfig(name="test-mcp", command="")
        mock_config.return_value.mcp = MCPConfig(enabled=True, servers=[server])

        results = _check_mcp()
        server_result = [r for r in results if "test-mcp" in r.name][0]
        assert server_result.status == CheckStatus.ERROR
        assert "No command" in server_result.message

    @patch("urllib.request.urlopen")
    @patch("gptme.cli.doctor.get_config")
    def test_mcp_http_server_reachable(self, mock_config, mock_urlopen):
        """Test that reachable HTTP server is OK."""
        server = MCPServerConfig(name="remote-mcp", url="http://localhost:8080/mcp")
        mock_config.return_value.mcp = MCPConfig(enabled=True, servers=[server])

        # Mock successful HTTP response
        mock_urlopen.return_value.__enter__ = lambda s: None
        mock_urlopen.return_value.__exit__ = lambda s, *a: None

        results = _check_mcp()
        server_result = [r for r in results if "remote-mcp" in r.name][0]
        assert server_result.status == CheckStatus.OK
        assert "reachable" in server_result.message.lower()

    @patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("refused"))
    @patch("gptme.cli.doctor.get_config")
    def test_mcp_http_server_unreachable(self, mock_config, mock_urlopen):
        """Test that unreachable HTTP server is ERROR."""
        server = MCPServerConfig(name="remote-mcp", url="http://localhost:9999/mcp")
        mock_config.return_value.mcp = MCPConfig(enabled=True, servers=[server])

        results = _check_mcp()
        server_result = [r for r in results if "remote-mcp" in r.name][0]
        assert server_result.status == CheckStatus.ERROR
        assert "Cannot reach" in server_result.message

    @patch("urllib.request.urlopen")
    @patch("gptme.cli.doctor.get_config")
    def test_mcp_http_server_error_status_still_reachable(
        self, mock_config, mock_urlopen
    ):
        """Test that HTTP errors (4xx/5xx) still count as reachable."""
        from email.message import Message
        from urllib.error import HTTPError

        server = MCPServerConfig(name="remote-mcp", url="http://localhost:8080/mcp")
        mock_config.return_value.mcp = MCPConfig(enabled=True, servers=[server])

        mock_urlopen.side_effect = HTTPError(
            "http://localhost:8080/mcp", 404, "Not Found", Message(), None
        )

        results = _check_mcp()
        server_result = [r for r in results if "remote-mcp" in r.name][0]
        assert server_result.status == CheckStatus.OK
        assert "reachable" in server_result.message.lower()

    @patch("shutil.which")
    @patch("gptme.cli.doctor.get_config")
    def test_mcp_multiple_servers(self, mock_config, mock_which):
        """Test checking multiple MCP servers."""
        servers = [
            MCPServerConfig(name="server-a", command="npx", args=["-y", "mcp-a"]),
            MCPServerConfig(name="server-b", command="missing-cmd"),
        ]
        mock_config.return_value.mcp = MCPConfig(enabled=True, servers=servers)

        def which_side_effect(cmd):
            return "/usr/bin/npx" if cmd == "npx" else None

        mock_which.side_effect = which_side_effect

        results = _check_mcp()
        # 1 status + 2 servers
        assert len(results) == 3

        a_result = [r for r in results if "server-a" in r.name][0]
        b_result = [r for r in results if "server-b" in r.name][0]
        assert a_result.status == CheckStatus.OK
        assert b_result.status == CheckStatus.ERROR

    @patch("shutil.which", return_value="/usr/bin/npx")
    @patch("gptme.cli.doctor.get_config")
    def test_mcp_verbose_shows_details(self, mock_config, mock_which):
        """Test that verbose mode shows command args."""
        server = MCPServerConfig(
            name="test-mcp", command="npx", args=["-y", "some-server"]
        )
        mock_config.return_value.mcp = MCPConfig(enabled=True, servers=[server])

        results = _check_mcp(verbose=True)
        server_result = [r for r in results if "test-mcp" in r.name][0]
        assert server_result.details is not None
        assert "npx" in server_result.details
        assert "-y" in server_result.details
