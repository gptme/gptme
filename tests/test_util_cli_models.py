"""Tests for the models-related gptme-util CLI commands."""

import json
import types
from unittest.mock import Mock, patch

from click.testing import CliRunner

from gptme.cli.util import main


class TestModelsTest:
    """Tests for 'models test' command."""

    def test_help(self):
        """Test that help text is shown."""
        runner = CliRunner()
        result = runner.invoke(main, ["models", "test", "--help"])
        assert result.exit_code == 0
        assert "Test connectivity to a model" in result.output
        assert "MODEL_NAME" in result.output

    def test_unknown_model(self):
        """Test error on unrecognized model/provider."""
        with patch(
            "gptme.llm.get_provider_from_model",
            side_effect=ValueError("Unknown provider: fake"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["models", "test", "fake/model"])
        assert result.exit_code == 1
        assert "Unknown model or provider" in result.output
        assert "gptme-util models list" in result.output

    def test_missing_api_key(self):
        """Test error when API key is not configured."""
        mock_config = Mock()
        mock_config.get_env.return_value = None
        with (
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.cli.util.get_config", return_value=mock_config),
        ):
            runner = CliRunner()
            result = runner.invoke(
                main, ["models", "test", "anthropic/claude-haiku-4-5"]
            )
        assert result.exit_code == 1
        assert "ANTHROPIC_API_KEY" in result.output
        assert "not set" in result.output or "not configured" in result.output

    def test_missing_api_key_json(self):
        """Test --json output when API key is missing."""
        mock_config = Mock()
        mock_config.get_env.return_value = None
        with (
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.cli.util.get_config", return_value=mock_config),
        ):
            runner = CliRunner()
            result = runner.invoke(
                main, ["models", "test", "anthropic/claude-haiku-4-5", "--json"]
            )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False
        assert "ANTHROPIC_API_KEY" in data["error"]

    def test_successful_call(self):
        """Test successful model test call."""
        mock_config = Mock()
        mock_config.get_env.return_value = "sk-ant-test-key"
        with (
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.cli.util.get_config", return_value=mock_config),
            patch("gptme.llm.init_llm"),
            patch(
                "gptme.llm._chat_complete",
                return_value=("OK", {"model": "claude-haiku-4-5"}),
            ) as mock_complete,
        ):
            runner = CliRunner()
            result = runner.invoke(
                main, ["models", "test", "anthropic/claude-haiku-4-5"]
            )

        assert result.exit_code == 0, result.output
        assert "✅" in result.output
        assert "working correctly" in result.output
        call_args = mock_complete.call_args
        messages = call_args[0][0]
        assert messages[0].role == "system"
        assert messages[1].role == "user"
        assert call_args[1]["max_tokens"] == 5

    def test_successful_call_json(self):
        """Test --json output on successful call."""
        mock_config = Mock()
        mock_config.get_env.return_value = "sk-ant-test-key"
        with (
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.cli.util.get_config", return_value=mock_config),
            patch("gptme.llm.init_llm"),
            patch("gptme.llm._chat_complete", return_value=("OK", {})),
        ):
            runner = CliRunner()
            result = runner.invoke(
                main, ["models", "test", "anthropic/claude-haiku-4-5", "--json"]
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["model"] == "anthropic/claude-haiku-4-5"
        assert data["provider"] == "anthropic"
        assert "latency_ms" in data
        assert data["response"] == "OK"

    def test_api_failure(self):
        """Test error output when API call fails."""
        mock_config = Mock()
        mock_config.get_env.return_value = "sk-ant-expired"
        with (
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.cli.util.get_config", return_value=mock_config),
            patch("gptme.llm.init_llm"),
            patch(
                "gptme.llm._chat_complete",
                side_effect=Exception("Error code: 401 - Unauthorized"),
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(
                main, ["models", "test", "anthropic/claude-haiku-4-5"]
            )
        assert result.exit_code == 1
        assert "Request failed" in result.output
        assert "Common causes" in result.output

    def test_bare_provider_resolves_default(self):
        """Test that a bare provider name resolves to a default model."""
        mock_config = Mock()
        mock_config.get_env.side_effect = lambda k: (
            "sk-test" if k == "ANTHROPIC_API_KEY" else None
        )
        with (
            patch("gptme.llm.get_provider_from_model", return_value="anthropic"),
            patch("gptme.cli.util.get_config", return_value=mock_config),
            patch("gptme.llm.init_llm"),
            patch("gptme.llm._chat_complete", return_value=("OK", {})),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["models", "test", "anthropic"])
        assert result.exit_code == 0, result.output
        assert "Using default model for anthropic" in result.output
        assert "claude-haiku-4-5" in result.output

    def test_bare_provider_no_default(self):
        """Test that providers without a default model (e.g. azure) give a clear error."""
        runner = CliRunner()
        result = runner.invoke(main, ["models", "test", "azure"])
        assert result.exit_code == 1
        assert "No default model for 'azure'" in result.output
        assert (
            "azure/my-deployment" in result.output or "full model name" in result.output
        )


class TestModelsInfo:
    """Tests for 'models info' command."""

    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["models", "info", "--help"])
        assert result.exit_code == 0
        assert "detailed information about a specific model" in result.output

    @staticmethod
    def _run_models_info(*args: str):
        """Invoke 'gptme-util models info' via CliRunner for separate stdout/stderr capture.

        Uses r.stdout and r.stderr (available since click 8.2) so stdout and stderr are
        independently accessible without spawning a subprocess (which is slow on CI and
        can hit pytest timeouts).
        """
        runner = CliRunner()
        r = runner.invoke(main, ["models", "info", *args])
        result = types.SimpleNamespace(
            returncode=r.exit_code,
            stdout=r.stdout,
            stderr=r.stderr or "",
        )
        return result

    def test_known_model_no_warning(self):
        """A recognized provider/model shows info with no fallback warning."""
        result = self._run_models_info("anthropic/claude-opus-4-7")
        assert result.returncode == 0, result.stderr
        assert "Provider: anthropic" in result.stdout
        assert "Unrecognized provider" not in result.stderr

    def test_unknown_provider_exits_1(self):
        """An unrecognized provider prefix (unknown provider) exits 1.

        The ⚠️ warning goes to stderr and stdout is empty — callers must not
        script on fabricated fallback values from an unknown provider.
        """
        result = self._run_models_info("bogus/model")
        assert result.returncode == 1, result.stdout
        # Warning lands on stderr.
        assert "Unrecognized provider" in result.stderr
        # No model data written to stdout (we exit before printing).
        assert "Model:" not in result.stdout

    def test_unknown_bare_model_exits_1(self):
        """A bare model name not in the registry exits 1 with a clear error."""
        result = self._run_models_info("nonexistent-model-xyz")
        assert result.returncode == 1, result.stdout
        assert "Unknown model" in result.stderr
        assert "nonexistent-model-xyz" in result.stderr
        assert "Model:" not in result.stdout

    def test_unknown_provider_json_exits_1(self):
        """With --json and an unknown provider, exit 1 and no JSON on stdout."""
        result = self._run_models_info("bogus/model", "--json")
        assert result.returncode == 1, result.stdout
        # Warning still on stderr.
        assert "Unrecognized provider" in result.stderr
        # stdout is empty — no fabricated JSON.
        assert result.stdout.strip() == ""

    def test_unknown_provider_json_stays_clean_after_stdout_logging(self):
        """A prior interactive logging setup must not contaminate stderr or stdout."""
        from gptme.init import init_logging
        from gptme.llm.models.resolution import _logged_warnings

        init_logging(verbose=False, stderr=False)
        _logged_warnings.clear()

        result = self._run_models_info("bogus/model", "--json")

        assert result.returncode == 1, result.stdout
        assert "Unrecognized provider" in result.stderr
        assert result.stdout.strip() == ""

    def test_known_provider_unknown_model_id_exits_0(self):
        """A known provider with an unrecognized model ID uses closest-match
        metadata and exits 0 — supports newly-released models not yet in registry."""
        result = self._run_models_info("anthropic/brand-new-model-xyz")
        assert result.returncode == 0, result.stderr
        assert "Provider: anthropic" in result.stdout

    def test_custom_provider_model_exits_0(self):
        """A configured custom provider resolves to provider='unknown' internally
        but is valid — must exit 0 and print model data, not exit 1.

        Stubs both `get_model` (to return a provider='unknown' model object,
        mimicking the internal custom-provider routing value) and
        `get_provider_from_model` (to succeed with a CustomProvider, i.e. the
        provider IS recognized), so the exit-0 path is exercised
        deterministically regardless of environment-level custom config.
        """
        from gptme.llm import CustomProvider
        from gptme.llm.models import get_model as _get_model

        unknown_provider_model = _get_model("nonexistent-xyz")  # provider="unknown"
        with (
            patch(
                "gptme.llm.models.get_model",
                return_value=unknown_provider_model,
            ),
            patch(
                "gptme.llm.get_provider_from_model",
                return_value=CustomProvider("my-custom"),
            ),
        ):
            result = self._run_models_info("my-custom/my-model")
        assert result.returncode == 0, result.stderr
        assert "Unrecognized provider" not in result.stderr
        assert "Provider: unknown" in result.stdout

    def test_bare_custom_provider_name_exits_0(self):
        """A bare model name that IS a configured custom provider (with a
        default_model) resolves to provider='unknown' internally but is valid —
        must exit 0, not be rejected as an unknown bare name."""
        from gptme.llm.models import get_model as _get_model

        unknown_provider_model = _get_model("nonexistent-xyz")  # provider="unknown"
        with (
            patch(
                "gptme.llm.models.get_model",
                return_value=unknown_provider_model,
            ),
            patch("gptme.llm.is_custom_provider", return_value=True),
        ):
            result = self._run_models_info("my-custom")
        assert result.returncode == 0, result.stderr
        assert "Unknown model" not in result.stderr


class TestModelsRecommended:
    """Tests for 'models recommended' (rendered into docs/evals.rst at build time)."""

    def test_table_lists_every_recommended_provider(self):
        from gptme.llm.models import RECOMMENDED_MODELS

        runner = CliRunner()
        result = runner.invoke(main, ["models", "recommended"])
        assert result.exit_code == 0, result.output
        for provider, model in RECOMMENDED_MODELS.items():
            assert f"{provider}/{model}" in result.output

    def test_rst_is_a_grid_table_with_literals(self):
        runner = CliRunner()
        result = runner.invoke(main, ["models", "recommended", "--format", "rst"])
        assert result.exit_code == 0, result.output
        lines = result.output.strip().splitlines()
        assert lines[0].startswith("+-") and lines[2].startswith("+=")
        assert "``anthropic/claude-sonnet-5-5``" in result.output
        # every row has the same width, or Sphinx rejects the table
        assert len({len(line) for line in lines}) == 1

    def test_json(self):
        from gptme.llm.models import RECOMMENDED_MODELS

        runner = CliRunner()
        result = runner.invoke(main, ["models", "recommended", "--format", "json"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert {row["provider"] for row in rows} == set(RECOMMENDED_MODELS)
        assert all({"provider", "model", "summary_model"} <= set(r) for r in rows)

    def test_markdown(self):
        runner = CliRunner()
        result = runner.invoke(main, ["models", "recommended", "--format", "markdown"])
        assert result.exit_code == 0, result.output
        assert result.output.startswith("| Provider | Recommended model |")
