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
  recommended: string[];
}

export function useModels() {
  const { api } = useApi();
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [defaultModel, setDefaultModel] = useState<string | null>(null);
  const [recommendedModels, setRecommendedModels] = useState<string[]>([]);
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
        setRecommendedModels(data.recommended || []);
      } catch (err) {
        console.error('Failed to fetch models:', err);
        setError(err instanceof Error ? err.message : 'Failed to fetch models');
        setModels([]);
        setDefaultModel(null);
        setRecommendedModels([]);
      } finally {
        setIsLoading(false);
      }
    };

    fetchModels();
  }, [api.baseUrl, api.authHeader]);

  const availableModels = models.map((model) => model.id);

  return {
    models,
    availableModels,
    defaultModel,
    isLoading,
    error,
    recommendedModels,
  };
}
