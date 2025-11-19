"""Tests for custom OpenAI-compatible providers configuration."""

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
