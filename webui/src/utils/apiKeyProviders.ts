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

// Subscription providers that authenticate via OAuth instead of an API key.
export type SubscriptionProvider = 'openai-subscription' | 'grok-subscription' | 'openrouter-pkce';

export type SubscriptionProviderOption = {
  value: SubscriptionProvider;
  label: string;
  description: string;
};

export const SUBSCRIPTION_PROVIDER_OPTIONS: SubscriptionProviderOption[] = [
  {
    value: 'openai-subscription',
    label: 'ChatGPT (Plus / Pro)',
    description: 'Sign in with your existing ChatGPT subscription — no API key needed.',
  },
  {
    value: 'grok-subscription',
    label: 'Grok (SuperGrok)',
    description: 'Sign in with your SuperGrok subscription — no API key needed.',
  },
  {
    value: 'openrouter-pkce',
    label: 'OpenRouter (free sign-in)',
    description: 'Sign in to OpenRouter — returns a permanent API key, no credit card required.',
  },
];
