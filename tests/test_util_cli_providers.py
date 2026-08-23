"""Tests for the providers-related gptme-util CLI commands."""

import json
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from gptme.cli.util import main
from gptme.config import ProviderConfig
from gptme.llm.local_discovery import (
    DiscoveryResult,
    LocalProviderCandidate,
)


@pytest.fixture(autouse=True)
def _disable_real_local_discovery(monkeypatch):
    """Keep CLI tests hermetic: never probe the developer's real :11434/:1234."""
    monkeypatch.setenv("GPTME_NO_LOCAL_DISCOVERY", "1")


@pytest.fixture
def mock_config(mocker):
    """Mock configuration with provider settings."""
    config = Mock()
    config.user.providers = []
    mocker.patch("gptme.cli.util.get_config", return_value=config)
    return config


@pytest.fixture
def make_provider():
    """Factory for creating ProviderConfig instances."""

    def _make(
        name="test-provider",
        base_url="http://localhost:8000/v1",
        api_key=None,
        api_key_env=None,
        default_model=None,
    ):
        return ProviderConfig(
            name=name,
            base_url=base_url,
            api_key=api_key,
            api_key_env=api_key_env,
            default_model=default_model,
        )

    return _make


class TestProvidersList:
    """Tests for 'providers list' command."""

    def test_no_providers(self, mock_config):
        """Test when no providers are configured."""
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "No custom providers configured" in result.output
        assert "gptme.toml" in result.output

    def test_list_single_provider(self, mock_config, make_provider):
        """Test listing a single provider."""
        mock_config.user.providers = [
            make_provider(
                name="vllm-local",
                base_url="http://localhost:8000/v1",
                default_model="llama-3",
            )
        ]
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "vllm-local" in result.output
        assert "http://localhost:8000/v1" in result.output
        assert "llama-3" in result.output

    def test_list_provider_with_api_key_env(self, mock_config, make_provider):
        """Test listing shows API key env var source."""
        mock_config.user.providers = [make_provider(api_key_env="MY_SECRET_KEY")]
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "$MY_SECRET_KEY" in result.output

    def test_list_provider_with_direct_key(self, mock_config, make_provider):
        """Test listing shows direct key indicator (not the key itself)."""
        mock_config.user.providers = [make_provider(api_key="sk-secret")]
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "configured directly" in result.output
        assert "sk-secret" not in result.output

    def test_list_multiple_providers(self, mock_config, make_provider):
        """Test listing multiple providers."""
        mock_config.user.providers = [
            make_provider(name="provider-a", base_url="http://a:8000/v1"),
            make_provider(name="provider-b", base_url="http://b:9000/v1"),
        ]
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "provider-a" in result.output
        assert "provider-b" in result.output
        assert "2 custom provider(s)" in result.output


class TestProvidersTest:
    """Tests for 'providers test' command."""

    def test_provider_not_found(self, mock_config):
        """Test when provider name doesn't match any config."""
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "test", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_provider_not_found_shows_available(self, mock_config, make_provider):
        """Test that missing provider shows available options."""
        mock_config.user.providers = [make_provider(name="my-llm")]
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "test", "wrong-name"])
        assert result.exit_code == 1
        assert "not found" in result.output
        assert "my-llm" in result.output

    def test_provider_not_found_no_providers(self, mock_config):
        """Test missing provider when no providers configured at all."""
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "test", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output
        assert "gptme providers add" in result.output

    def test_missing_api_key_env(self, mock_config, make_provider, monkeypatch):
        """Test when API key env var is not set."""
        mock_config.user.providers = [make_provider(api_key_env="MISSING_KEY_VAR")]
        monkeypatch.delenv("MISSING_KEY_VAR", raising=False)
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "test", "test-provider"])
        assert result.exit_code == 1
        assert "not set" in result.output

    def test_successful_connection(self, mock_config, make_provider):
        """Test successful provider connection listing models."""
        mock_config.user.providers = [
            make_provider(api_key="test-key", default_model="llama-3")
        ]

        # Mock OpenAI client
        mock_model = Mock()
        mock_model.id = "llama-3"
        mock_client = Mock()
        mock_client.models.list.return_value = [mock_model]

        with patch("openai.OpenAI", return_value=mock_client) as mock_cls:
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 0
        assert "Connected" in result.output
        assert "llama-3" in result.output
        assert "Default model" in result.output
        assert "is available" in result.output

        # Verify client was created with correct params
        mock_cls.assert_called_once_with(
            api_key="test-key",
            base_url="http://localhost:8000/v1",
            timeout=10,
        )

    def test_successful_connection_default_model_missing(
        self, mock_config, make_provider
    ):
        """Test when default model is not in the provider's model list."""
        mock_config.user.providers = [
            make_provider(api_key="test-key", default_model="missing-model")
        ]

        mock_model = Mock()
        mock_model.id = "other-model"
        mock_client = Mock()
        mock_client.models.list.return_value = [mock_model]

        with patch("openai.OpenAI", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 0
        assert "Connected" in result.output
        assert "not found in model list" in result.output

    def test_connection_failure(self, mock_config, make_provider):
        """Test when connection to provider fails."""
        mock_config.user.providers = [make_provider(api_key="test-key")]

        mock_client = Mock()
        mock_client.models.list.side_effect = ConnectionError("Connection refused")

        with patch("openai.OpenAI", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 1
        assert "Connection failed" in result.output
        assert "Connection refused" in result.output

    def test_many_models_truncated(self, mock_config, make_provider):
        """Test that model list is truncated after 10 entries."""
        mock_config.user.providers = [make_provider(api_key="test-key")]

        models = []
        for i in range(15):
            m = Mock()
            m.id = f"model-{i}"
            models.append(m)

        mock_client = Mock()
        mock_client.models.list.return_value = models

        with patch("openai.OpenAI", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 0
        assert "model-0" in result.output
        assert "model-9" in result.output
        assert "model-10" not in result.output
        assert "5 more" in result.output

    def test_hyphenated_provider_name_env_var(self, mock_config, make_provider):
        """Test that hyphenated provider names produce valid env var names."""
        mock_config.user.providers = [
            make_provider(name="my-local-llm")  # hyphens → underscores
        ]

        mock_client = Mock()
        mock_client.models.list.return_value = []

        with (
            patch.dict("os.environ", {"MY_LOCAL_LLM_API_KEY": "env-key"}),
            patch("openai.OpenAI", return_value=mock_client) as mock_cls,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "my-local-llm"])

        assert result.exit_code == 0
        assert "Connected" in result.output
        mock_cls.assert_called_once_with(
            api_key="env-key",
            base_url="http://localhost:8000/v1",
            timeout=10,
        )

    def test_api_key_from_env_default(self, mock_config, make_provider):
        """Test API key resolution from default env var (PROVIDER_NAME_API_KEY)."""
        mock_config.user.providers = [
            make_provider(name="myservice")  # no api_key or api_key_env
        ]

        mock_client = Mock()
        mock_client.models.list.return_value = []

        with (
            patch.dict("os.environ", {"MYSERVICE_API_KEY": "env-key"}),
            patch("openai.OpenAI", return_value=mock_client) as mock_cls,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "myservice"])

        assert result.exit_code == 0
        assert "Connected" in result.output
        mock_cls.assert_called_once_with(
            api_key="env-key",
            base_url="http://localhost:8000/v1",
            timeout=10,
        )

    def test_api_key_from_explicit_env(self, mock_config, make_provider):
        """Test API key resolution from explicit env var."""
        mock_config.user.providers = [make_provider(api_key_env="CUSTOM_KEY_VAR")]

        mock_client = Mock()
        mock_client.models.list.return_value = []

        with (
            patch.dict("os.environ", {"CUSTOM_KEY_VAR": "custom-env-key"}),
            patch("openai.OpenAI", return_value=mock_client) as mock_cls,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 0
        assert "Connected" in result.output
        mock_cls.assert_called_once_with(
            api_key="custom-env-key",
            base_url="http://localhost:8000/v1",
            timeout=10,
        )

    def test_star_marks_default_model(self, mock_config, make_provider):
        """Test that the default model gets a star marker in the list."""
        mock_config.user.providers = [
            make_provider(api_key="k", default_model="special-model")
        ]

        m1, m2 = Mock(), Mock()
        m1.id = "other-model"
        m2.id = "special-model"
        mock_client = Mock()
        mock_client.models.list.return_value = [m1, m2]

        with patch("openai.OpenAI", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 0
        # The default model should have a star
        lines = result.output.split("\n")
        special_line = [ln for ln in lines if "special-model" in ln][0]
        assert "⭐" in special_line
        other_line = [ln for ln in lines if "other-model" in ln][0]
        assert "⭐" not in other_line

    def test_no_default_model(self, mock_config, make_provider):
        """Test output when no default model is configured."""
        mock_config.user.providers = [
            make_provider(api_key="k")  # no default_model
        ]

        mock_model = Mock()
        mock_model.id = "some-model"
        mock_client = Mock()
        mock_client.models.list.return_value = [mock_model]

        with patch("openai.OpenAI", return_value=mock_client):
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "test", "test-provider"])

        assert result.exit_code == 0
        assert "Connected" in result.output
        # No default model message
        assert "Default model" not in result.output


class TestProvidersAdd:
    """Tests for 'providers add' command."""

    def test_add_invokes_setup_wizard(self):
        """Test that 'providers add' calls the interactive setup wizard."""
        with patch("gptme.cli.setup._setup_custom_provider") as mock_setup:
            mock_setup.return_value = ("my-provider", "sk-test")
            runner = CliRunner()
            result = runner.invoke(main, ["providers", "add"])

        assert result.exit_code == 0
        mock_setup.assert_called_once()

    def test_list_empty_mentions_add_command(self, mock_config):
        """Test that empty providers list mentions 'gptme providers add'."""
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "gptme providers add" in result.output


def _ollama_up(*models: str) -> DiscoveryResult:
    cand = LocalProviderCandidate(
        name="ollama",
        display_name="Ollama",
        base_url="http://127.0.0.1:11434/v1",
        hint="run ollama serve",
    )
    return DiscoveryResult(
        candidate=cand,
        status="up",
        reason="ok",
        models=models,
    )


def _lmstudio_down() -> DiscoveryResult:
    cand = LocalProviderCandidate(
        name="lmstudio",
        display_name="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        hint="Open LM Studio → Local Server → Start",
    )
    return DiscoveryResult(
        candidate=cand,
        status="down",
        reason="not running (connection refused)",
    )


class TestProvidersListDiscovery:
    """Tests for auto-discovered local providers in 'providers list'."""

    def test_shows_discovered_ollama(self, mock_config, monkeypatch, mocker):
        monkeypatch.delenv("GPTME_NO_LOCAL_DISCOVERY", raising=False)
        mocker.patch(
            "gptme.llm.local_discovery.discover_local_providers",
            return_value=[_ollama_up("llama3.2:3b"), _lmstudio_down()],
        )
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "Local auto-discovery" in result.output
        assert "Ollama" in result.output
        assert "http://127.0.0.1:11434/v1" in result.output
        assert "llama3.2:3b" in result.output
        assert "/v1/models" in result.output
        assert "LM Studio" in result.output
        assert "connection refused" in result.output
        assert "gptme providers add" in result.output

    def test_json_includes_discovered(self, mock_config, monkeypatch, mocker):
        monkeypatch.delenv("GPTME_NO_LOCAL_DISCOVERY", raising=False)
        mocker.patch(
            "gptme.llm.local_discovery.discover_local_providers",
            return_value=[_ollama_up("llama3.2:3b"), _lmstudio_down()],
        )
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["configured"] == []
        names = [d["name"] for d in payload["discovered"]]
        assert names == ["ollama", "lmstudio"]
        ollama = payload["discovered"][0]
        assert ollama["status"] == "up"
        assert ollama["models"] == ["llama3.2:3b"]
        assert ollama["models_url"] == "http://127.0.0.1:11434/v1/models"
        assert payload["discovered"][1]["status"] == "down"
        assert payload["discovered"][1]["reason"]

    def test_no_discover_flag_skips_probe(self, mock_config, mocker):
        spy = mocker.patch("gptme.llm.local_discovery.discover_local_providers")
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list", "--no-discover"])
        assert result.exit_code == 0
        spy.assert_not_called()
        assert "Local auto-discovery" not in result.output

    def test_env_disable_notes_disabled(self, mock_config):
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "GPTME_NO_LOCAL_DISCOVERY" in result.output

    def test_json_discovery_disabled_flag(self, mock_config, monkeypatch, mocker):
        """--no-discover sets discovery_disabled=true in JSON output."""
        monkeypatch.delenv("GPTME_NO_LOCAL_DISCOVERY", raising=False)
        spy = mocker.patch("gptme.llm.local_discovery.discover_local_providers")
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list", "--json", "--no-discover"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["discovery_disabled"] is True
        assert payload["discovered"] == []
        spy.assert_not_called()

    def test_json_discovery_disabled_env_var(self, mock_config, monkeypatch):
        """GPTME_NO_LOCAL_DISCOVERY sets discovery_disabled=true in JSON output."""
        # env var already set by the autouse fixture; ensure --discover flag is default (True)
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["discovery_disabled"] is True
        assert payload["discovered"] == []

    def test_json_discovery_enabled(self, mock_config, monkeypatch, mocker):
        """When discovery runs and finds nothing, discovery_disabled=false."""
        monkeypatch.delenv("GPTME_NO_LOCAL_DISCOVERY", raising=False)
        mocker.patch(
            "gptme.llm.local_discovery.discover_local_providers",
            return_value=[],
        )
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["discovery_disabled"] is False
        assert payload["discovered"] == []


class TestStripControls:
    """Unit tests for _strip_controls helper."""

    def test_removes_c0_control_chars(self):
        from gptme.cli.util import _strip_controls

        # ESC byte is stripped; the printable bracket/letter tail is kept (harmless without ESC)
        assert _strip_controls("\x1b[1mhello\x1b[0m") == "[1mhello[0m"
        assert _strip_controls("clean") == "clean"
        assert _strip_controls("\x00null\x01soh\x1f us") == "nullsoh us"

    def test_removes_c1_control_chars(self):
        from gptme.cli.util import _strip_controls

        assert _strip_controls("\x80\x9f") == ""
        assert _strip_controls("ok\x9bsequence") == "oksequence"

    def test_preserves_printable_and_unicode(self):
        from gptme.cli.util import _strip_controls

        assert _strip_controls("llama3.2:3b") == "llama3.2:3b"
        assert _strip_controls("model/name-v1") == "model/name-v1"
        assert _strip_controls("Ångström") == "Ångström"


class TestEscapeInjectionInDiscovery:
    """Verify terminal escape sequences from probe responses are stripped at display time."""

    def test_model_id_escape_stripped(self, mock_config, monkeypatch, mocker):
        """Model IDs containing escape sequences must not reach the terminal raw."""
        monkeypatch.delenv("GPTME_NO_LOCAL_DISCOVERY", raising=False)
        evil_model_id = "\x1b]51;A\x07evil-model"
        mocker.patch(
            "gptme.llm.local_discovery.discover_local_providers",
            return_value=[_ollama_up(evil_model_id)],
        )
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "\x1b" not in result.output
        assert "evil-model" in result.output

    def test_reason_escape_stripped(self, mock_config, monkeypatch, mocker):
        """Probe reason strings containing escape sequences must not reach the terminal raw."""
        monkeypatch.delenv("GPTME_NO_LOCAL_DISCOVERY", raising=False)
        cand = LocalProviderCandidate(
            name="ollama",
            display_name="Ollama",
            base_url="http://127.0.0.1:11434/v1",
            hint="run ollama serve",
        )
        evil_reason = "HTTP error: \x1b[31mfailed\x1b[0m"
        evil_result = DiscoveryResult(
            candidate=cand,
            status="error",
            reason=evil_reason,
        )
        mocker.patch(
            "gptme.llm.local_discovery.discover_local_providers",
            return_value=[evil_result],
        )
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "\x1b" not in result.output
        assert "failed" in result.output

    def test_all_control_model_ids_fall_back_to_placeholder(
        self, mock_config, monkeypatch, mocker
    ):
        """When every model ID is pure control chars, example falls back to <model>."""
        monkeypatch.delenv("GPTME_NO_LOCAL_DISCOVERY", raising=False)
        mocker.patch(
            "gptme.llm.local_discovery.discover_local_providers",
            return_value=[_ollama_up("\x1b\x00\x9f")],
        )
        runner = CliRunner()
        result = runner.invoke(main, ["providers", "list"])
        assert result.exit_code == 0
        assert "\x1b" not in result.output
        assert "local/<model>" in result.output
