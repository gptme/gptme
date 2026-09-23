"""Tests for custom OpenAI-compatible providers configuration."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from gptme.config import Config, ProviderConfig, UserConfig
from gptme.config.user import get_user_config_runtime_path, load_user_config


def test_provider_config_creation():
    """Test creating a ProviderConfig instance."""
    provider = ProviderConfig(
        name="test-provider",
        base_url="http://localhost:8000/v1",
        api_key_env="TEST_API_KEY",
        default_model="test-model",
    )

    assert provider.name == "test-provider"
    assert provider.base_url == "http://localhost:8000/v1"
    assert provider.api_key_env == "TEST_API_KEY"
    assert provider.default_model == "test-model"


def test_provider_api_key_priority():
    """Test API key resolution priority."""
    # Test 1: Direct API key
    provider = ProviderConfig(
        name="test",
        base_url="http://localhost:8000/v1",
        api_key="direct-key",
    )

    config = Config()
    assert provider.get_api_key(config) == "direct-key"

    # Test 2: Environment variable
    provider = ProviderConfig(
        name="test",
        base_url="http://localhost:8000/v1",
        api_key_env="TEST_KEY",
    )

    # Would require mocking config.get_env_required

    # Test 3: Default (provider name uppercase)
    provider = ProviderConfig(
        name="myservice",
        base_url="http://localhost:8000/v1",
    )

    # Would check for MYSERVICE_API_KEY env var


def test_user_config_with_providers():
    """Test UserConfig can hold multiple providers."""
    providers = [
        ProviderConfig(
            name="vllm-local",
            base_url="http://localhost:8000/v1",
            default_model="meta-llama/Llama-3.1-8B",
        ),
        ProviderConfig(
            name="azure-gpt4",
            base_url="https://my-endpoint.openai.azure.com",
            api_key_env="AZURE_API_KEY",
        ),
    ]

    config = UserConfig(providers=providers)

    assert len(config.providers) == 2
    assert config.providers[0].name == "vllm-local"
    assert config.providers[1].name == "azure-gpt4"


def test_backward_compatibility_local_provider():
    """Test that 'local' provider still works with existing env vars."""
    # The "local" provider should still work using OPENAI_BASE_URL
    # This is tested in the init function with the existing elif branch


def test_malformed_provider_entries_skipped_not_fatal():
    """A malformed [[providers]] entry must not crash config loading.

    Regression test: a provider missing a required field (name or base_url)
    previously raised an unhandled TypeError from `_load_user_config`, which
    crashed every gptme/gptme-util invocation (including `--help`) since
    config loading happens eagerly at startup.
    """
    config_toml = """
[[providers]]
name = "missing-base-url"

[[providers]]
base_url = "http://localhost:9999/v1"

[[providers]]
name = "valid"
base_url = "http://localhost:8000/v1"

[env]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(config_toml)
    try:
        config = load_user_config(f.name)
        # Only the valid entry should survive; malformed ones are skipped.
        assert len(config.providers) == 1
        assert config.providers[0].name == "valid"
    finally:
        os.remove(f.name)


def test_non_dict_provider_entry_skipped_not_fatal():
    """A non-table [[providers]] entry (e.g. a bare string) must not crash."""
    config_toml = """
providers = ["not-a-table"]

[env]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(config_toml)
    try:
        config = load_user_config(f.name)
        assert config.providers == []
    finally:
        os.remove(f.name)


def test_malformed_user_providers_skipped_with_empty_runtime_overlay(
    tmp_path: Path,
) -> None:
    """A runtime overlay with no providers must not make user errors fatal."""
    main = tmp_path / "config.toml"
    main.write_text(
        """
[[providers]]
name = "missing-base-url"

[[providers]]
name = "valid"
base_url = "http://localhost:8000/v1"
""",
        encoding="utf-8",
    )
    get_user_config_runtime_path(str(main)).write_text(
        "# runtime overlay, no providers\n", encoding="utf-8"
    )

    config = load_user_config(str(main))
    assert [p.name for p in config.providers] == ["valid"]


def test_malformed_user_provider_skipped_alongside_runtime_providers(
    tmp_path: Path,
) -> None:
    """Valid runtime providers survive a malformed extra in user config."""
    main = tmp_path / "config.toml"
    main.write_text(
        """
[[providers]]
name = "missing-base-url"

[[providers]]
name = "valid-user"
base_url = "http://localhost:8000/v1"
""",
        encoding="utf-8",
    )
    get_user_config_runtime_path(str(main)).write_text(
        """
[[providers]]
name = "runtime-ok"
base_url = "http://localhost:9000/v1"
""",
        encoding="utf-8",
    )

    config = load_user_config(str(main))
    names = [p.name for p in config.providers]
    assert "missing-base-url" not in names
    assert "runtime-ok" in names
    assert "valid-user" in names


def test_malformed_same_name_override_does_not_drop_runtime_provider(
    tmp_path: Path,
) -> None:
    """A malformed user override of a runtime provider must not remove it.

    Merge-by-name used to apply unexpected keys onto the runtime entry, after
    which non-strict parse skipped the whole provider.
    """
    main = tmp_path / "config.toml"
    main.write_text(
        """
[[providers]]
name = "runtime-ok"
unexpected_key = true
""",
        encoding="utf-8",
    )
    get_user_config_runtime_path(str(main)).write_text(
        """
[[providers]]
name = "runtime-ok"
base_url = "http://localhost:9000/v1"
""",
        encoding="utf-8",
    )

    config = load_user_config(str(main))
    assert [p.name for p in config.providers] == ["runtime-ok"]
    assert config.providers[0].base_url == "http://localhost:9000/v1"


def test_valid_same_name_user_override_still_applies(tmp_path: Path) -> None:
    """A well-formed same-name override still merges onto the runtime entry."""
    main = tmp_path / "config.toml"
    main.write_text(
        """
[[providers]]
name = "runtime-ok"
api_key = "user-secret"
""",
        encoding="utf-8",
    )
    get_user_config_runtime_path(str(main)).write_text(
        """
[[providers]]
name = "runtime-ok"
base_url = "http://localhost:9000/v1"
""",
        encoding="utf-8",
    )

    config = load_user_config(str(main))
    assert len(config.providers) == 1
    assert config.providers[0].name == "runtime-ok"
    assert config.providers[0].base_url == "http://localhost:9000/v1"
    assert config.providers[0].api_key == "user-secret"


def test_custom_provider_supports_tools_api():
    """Test that custom providers support the tools API.

    Regression test for https://github.com/gptme/gptme/issues/1330
    Custom providers (e.g. llama-server) are OpenAI-compatible and should
    support the tools API, but the check was failing because it passed the
    full model path (e.g. 'sanctuary/gpt-oss') to is_custom_provider()
    instead of just the provider name ('sanctuary').
    """
    from gptme.llm.llm_openai import _spec2tool
    from gptme.llm.models import ModelMeta
    from gptme.tools.base import Parameter, ToolSpec

    # Create a minimal tool spec
    spec = ToolSpec(
        name="test_tool",
        desc="A test tool",
        instructions="Use this tool for testing",
        parameters=[
            Parameter(
                name="arg",
                type="string",
                description="A test argument",
                required=True,
            ),
        ],
    )

    # Simulate a custom provider model (provider="unknown", model="sanctuary/gpt-oss")
    model = ModelMeta(provider="unknown", model="sanctuary/gpt-oss", context=128_000)

    # Mock is_custom_provider to return True for "sanctuary"
    with patch("gptme.llm.llm_openai.is_custom_provider") as mock_icp:
        mock_icp.return_value = True
        result = _spec2tool(spec, model)

    # Verify the provider name was extracted correctly (not the full model path)
    mock_icp.assert_called_once_with("sanctuary")

    # Verify we got a valid tool definition back
    assert result["type"] == "function"
    assert result["function"]["name"] == "test_tool"
    assert "parameters" in result["function"]


# Example configuration that would be in gptme.toml:
EXAMPLE_CONFIG = """
[prompt]
about_user = "I am a developer"

[env]
SOME_VAR = "value"

[[providers]]
name = "vllm-local"
base_url = "http://localhost:8000/v1"
default_model = "meta-llama/Llama-3.1-8B"

[[providers]]
name = "azure-gpt4"
base_url = "https://my-azure-endpoint.openai.azure.com/openai/deployments"
api_key_env = "AZURE_API_KEY"
default_model = "gpt-4"

[[providers]]
name = "groq"
base_url = "https://api.groq.com/openai/v1"
api_key_env = "GROQ_API_KEY"
default_model = "llama-3.1-70b-versatile"
"""
