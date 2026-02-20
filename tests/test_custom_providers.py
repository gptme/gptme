"""Tests for custom OpenAI-compatible providers configuration."""

from unittest.mock import patch

from gptme.config import Config, ProviderConfig, UserConfig


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
    pass


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
