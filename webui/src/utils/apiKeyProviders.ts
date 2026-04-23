export type ApiKeyProvider =
  | 'anthropic'
  | 'openai'
  | 'openrouter'
  | 'gemini'
  | 'groq'
  | 'xai'
  | 'deepseek';

export type ApiKeyProviderOption = {
  value: ApiKeyProvider;
  label: string;
  placeholder: string;
};

export const API_KEY_PROVIDER_OPTIONS: ApiKeyProviderOption[] = [
  { value: 'anthropic', label: 'Anthropic', placeholder: 'sk-ant-...' },
  { value: 'openai', label: 'OpenAI', placeholder: 'sk-...' },
  { value: 'openrouter', label: 'OpenRouter', placeholder: 'sk-or-...' },
  { value: 'gemini', label: 'Gemini', placeholder: 'AIza...' },
  { value: 'groq', label: 'Groq', placeholder: 'gsk_...' },
  { value: 'xai', label: 'xAI', placeholder: 'xai-...' },
  { value: 'deepseek', label: 'DeepSeek', placeholder: 'sk-...' },
];

export const API_KEY_PROVIDER_METADATA = Object.fromEntries(
  API_KEY_PROVIDER_OPTIONS.map((provider) => [provider.value, provider])
) as Record<ApiKeyProvider, ApiKeyProviderOption>;
