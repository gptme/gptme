import { useState, useEffect } from 'react';
import { useApi } from '@/contexts/ApiContext';

export interface ModelInfo {
  id: string;
  provider: string;
  model: string;
  context: number;
  max_output?: number;
  supports_streaming: boolean;
  supports_vision: boolean;
  supports_reasoning: boolean;
  price_input: number;
  price_output: number;
}

export interface ModelsResponse {
  models: ModelInfo[];
  default: string | null;
}

// Shows if the fetch fails
const fallbackModels = [
  'anthropic/claude-sonnet-4-20250514',
  'anthropic/claude-3-5-haiku-20241022',
  'openai/gpt-5',
  'openai/gpt-4o',
  'openrouter/x-ai/grok-4',
];

// Shows as recommended options in the UI, if they are available
const recommendedModels = [
  'anthropic/claude-sonnet-4-20250514',
  'anthropic/claude-3-5-haiku-20241022',
  'openai/gpt-5',
  'openai/gpt-4o',
  'openrouter/x-ai/grok-4',
];

export function useModels() {
  const { api } = useApi();
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [defaultModel, setDefaultModel] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchModels = async () => {
      try {
        setIsLoading(true);
        setError(null);

        const headers: Record<string, string> = {
          'Content-Type': 'application/json',
        };

        if (api.authHeader) {
          headers.Authorization = api.authHeader;
        }

        const response = await fetch(`${api.baseUrl}/api/v2/models`, {
          headers,
        });

        if (!response.ok) {
          throw new Error(`Failed to fetch models: ${response.statusText}`);
        }

        const data: ModelsResponse = await response.json();

        setModels(data.models);
        setDefaultModel(data.default || null);
      } catch (err) {
        console.error('Failed to fetch models:', err);
        setError(err instanceof Error ? err.message : 'Failed to fetch models');

        // Use fallback models
        const fallbackModelInfos: ModelInfo[] = fallbackModels.map((model) => {
          const [provider, modelName] = model.split('/');
          return {
            id: model,
            provider,
            model: modelName,
            context: 128000,
            supports_streaming: true,
            supports_vision: false,
            supports_reasoning: false,
            price_input: 0,
            price_output: 0,
          };
        });
        setModels(fallbackModelInfos);
        setDefaultModel(fallbackModels[0]);
      } finally {
        setIsLoading(false);
      }
    };

    fetchModels();
  }, [api.baseUrl, api.authHeader]);

  // Convert models to simple string array for backward compatibility
  const availableModels = models.map((model) => model.id);
  const availableRecommendedModels = recommendedModels.filter((model) =>
    availableModels.includes(model)
  );

  return {
    models,
    availableModels,
    defaultModel,
    isLoading,
    error,
    fallbackModels,
    recommendedModels: availableRecommendedModels,
  };
}
