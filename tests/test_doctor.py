"""Tests for the gptme doctor command."""

import json
from unittest.mock import patch

from click.testing import CliRunner

from gptme.cli.doctor import (
    CheckResult,
    CheckStatus,
    _check_api_keys,
    _check_config,
    _check_permissions,
    _check_python_deps,
    _check_tools,
    main,
    run_diagnostics,
)


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

        # Should check common optional deps
        assert any("playwright" in name for name in dep_names)
        assert any("sentence_transformers" in name for name in dep_names)


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

    @patch("gptme.doctor.list_available_providers")
    @patch("gptme.doctor.get_config")
    @patch("gptme.doctor.validate_api_key")
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

    @patch("gptme.doctor.list_available_providers")
    @patch("gptme.doctor.get_config")
    @patch("gptme.doctor.validate_api_key")
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

    @patch("gptme.doctor.list_available_providers")
    @patch("gptme.doctor.get_config")
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

    @patch("gptme.doctor.list_available_providers")
    @patch("gptme.doctor.get_config")
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

    @patch("gptme.doctor.list_available_providers")
    @patch("gptme.doctor.get_config")
    @patch.dict("os.environ", {"AZURE_OPENAI_API_KEY": "test-key"}, clear=True)
    def test_azure_uses_special_env_var(self, mock_config, mock_providers):
        """Test that Azure uses AZURE_OPENAI_API_KEY (special case)."""

        # Setup: azure provider available
        mock_providers.return_value = [("azure", None)]
        mock_config_obj = mock_config.return_value
        mock_config_obj.get_env.return_value = None

        with patch("gptme.doctor.validate_api_key") as mock_validate:
            mock_validate.return_value = (True, None)
            results = _check_api_keys()

            # Find azure result
            azure_results = [r for r in results if "azure" in r.name.lower()]
            assert len(azure_results) >= 1
            azure_result = azure_results[0]
            # Should find the key and mark as OK
            assert azure_result.status == CheckStatus.OK
