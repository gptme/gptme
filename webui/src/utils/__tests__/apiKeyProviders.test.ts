import { API_KEY_PROVIDER_METADATA, API_KEY_PROVIDER_OPTIONS } from '../apiKeyProviders';

describe('apiKeyProviders', () => {
  it('keeps metadata aligned with provider options', () => {
    expect(Object.keys(API_KEY_PROVIDER_METADATA).sort()).toEqual(
      API_KEY_PROVIDER_OPTIONS.map((provider) => provider.value).sort()
    );

    for (const provider of API_KEY_PROVIDER_OPTIONS) {
      expect(API_KEY_PROVIDER_METADATA[provider.value]).toEqual(provider);
    }
  });

  it('lists the supported API-key providers in display order', () => {
    expect(API_KEY_PROVIDER_OPTIONS.map((provider) => provider.label)).toEqual([
      'Anthropic',
      'OpenAI',
      'OpenRouter',
      'Gemini',
      'Groq',
      'xAI',
      'DeepSeek',
    ]);
  });
});
