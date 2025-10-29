# Custom OpenAI-Compatible Providers

**Status**: Phase 1 Implementation (Configuration & Loading)

As of Phase 1, gptme supports configuring multiple custom OpenAI-compatible providers in addition to the built-in providers (openai, azure, openrouter, etc.).

## Configuration

Add custom providers to your `~/.config/gptme/gptme.toml`:

```toml
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
```

### Configuration Fields

- `name` (required): Provider identifier used in model selection
- `base_url` (required): Base URL for the OpenAI-compatible API
- `api_key` (optional): API key directly in config (not recommended)
- `api_key_env` (optional): Environment variable name containing the API key
- `default_model` (optional): Default model when only provider name is specified

### API Key Resolution

The API key is resolved in this priority order:

1. **Direct value**: `api_key = "key-here"` (not recommended for security)
2. **Environment variable**: `api_key_env = "MY_API_KEY"`
3. **Default convention**: `${PROVIDER_NAME}_API_KEY` (e.g., `GROQ_API_KEY` for provider named "groq")

## Usage

### With CLI

```bash
# Use specific custom provider with model
gptme --model vllm-local/my-model "query"

# Use custom provider with default model
gptme --model azure-gpt4 "query"

# List configured providers
gptme-util providers list
```

### Provider Listing

```bash
$ gptme-util providers list

🔌 Found 3 custom provider(s):

📡 vllm-local
   Base URL: http://localhost:8000/v1
   API Key: $VLLM_LOCAL_API_KEY (default)
   Default Model: meta-llama/Llama-3.1-8B

📡 azure-gpt4
   Base URL: https://my-azure-endpoint.openai.azure.com/openai/deployments
   API Key: $AZURE_API_KEY
   Default Model: gpt-4

📡 groq
   Base URL: https://api.groq.com/openai/v1
   API Key: $GROQ_API_KEY
   Default Model: llama-3.1-70b-versatile
```

## Backward Compatibility

The existing `local` provider continues to work using the `OPENAI_BASE_URL` and `OPENAI_API_KEY` environment variables. No changes are required for existing configurations.

## Implementation Details

### Phase 1 (Completed)

- ✅ Configuration schema (`ProviderConfig` dataclass)
- ✅ TOML parsing in `load_user_config()`
- ✅ Provider initialization in `llm_openai.py`
- ✅ `gptme-util providers list` command
- ✅ Backward compatibility with `local` provider
- ✅ API key resolution with priority order

### Phase 2 (Planned)

- [ ] Model selection updates for `provider/model` syntax
- [ ] Provider registry for managing custom providers
- [ ] Integration tests with mock providers

### Phase 3 (Planned)

- [ ] Complete documentation with examples
- [ ] User guide for common provider configurations
- [ ] Migration guide from `local` provider

## Examples

### Local vLLM Server

```toml
[[providers]]
name = "vllm-local"
base_url = "http://localhost:8000/v1"
default_model = "meta-llama/Llama-3.1-8B"
```

```bash
export VLLM_LOCAL_API_KEY="none"  # vLLM doesn't require auth
gptme --model vllm-local "What is the capital of France?"
```

### Groq Cloud

```toml
[[providers]]
name = "groq"
base_url = "https://api.groq.com/openai/v1"
api_key_env = "GROQ_API_KEY"
default_model = "llama-3.1-70b-versatile"
```

```bash
export GROQ_API_KEY="gsk_..."
gptme --model groq "Explain quantum computing"
```

### Azure OpenAI

```toml
[[providers]]
name = "azure-gpt4"
base_url = "https://my-endpoint.openai.azure.com/openai/deployments"
api_key_env = "AZURE_API_KEY"
default_model = "gpt-4"
```

```bash
export AZURE_API_KEY="..."
gptme --model azure-gpt4 "Write a Python function to sort a list"
```

## Related

- [Issue #673](https://github.com/gptme/gptme/issues/673) - Original feature request
- [Issue #514](https://github.com/gptme/gptme/issues/514) - Requesty provider support
- [Issue #548](https://github.com/gptme/gptme/issues/548) - AI/ML provider support
- [Issue #555](https://github.com/gptme/gptme/issues/555) - Chutes provider support
