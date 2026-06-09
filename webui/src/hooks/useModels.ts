import { useState, useEffect, useCallback, useRef } from 'react';
import { use$ } from '@legendapp/state/react';
import { useApi } from '@/contexts/ApiContext';
import { buildModelsFetchError } from '@/utils/modelsError';
import { isDemoMode } from '@/utils/connectionConfig';

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
  favorites?: string[];
}

export function useModels() {
  const { api, isConnected$ } = useApi();
  const isConnected = use$(isConnected$);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [defaultModel, setDefaultModel] = useState<string | null>(null);
  const [recommendedModels, setRecommendedModels] = useState<string[]>([]);
  const [favorites, setFavoritesState] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Mirror favorites in a ref so rapid toggles read the latest list instead of
  // a stale closure value (which could drop a favorite under quick clicks).
  const favoritesRef = useRef<string[]>([]);
  useEffect(() => {
    favoritesRef.current = favorites;
  }, [favorites]);
  const applyFavorites = useCallback((next: string[]) => {
    favoritesRef.current = next;
    setFavoritesState(next);
  }, []);

  useEffect(() => {
    if (!isConnected || isDemoMode()) {
      setIsLoading(false);
      return;
    }

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
          throw await buildModelsFetchError(response);
        }

        const data: ModelsResponse = await response.json();

        setModels(data.models);
        setDefaultModel(data.default || null);
        setRecommendedModels(data.recommended || []);
        setFavoritesState(data.favorites || []);
      } catch (err) {
        console.error('Failed to fetch models:', err);
        setError(err instanceof Error ? err.message : 'Failed to fetch models');
        setModels([]);
        setDefaultModel(null);
        setRecommendedModels([]);
        setFavoritesState([]);
      } finally {
        setIsLoading(false);
      }
    };

    fetchModels();
  }, [api.baseUrl, api.authHeader, isConnected]);

  const authedHeaders = useCallback(() => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (api.authHeader) headers.Authorization = api.authHeader;
    return headers;
  }, [api.authHeader]);

  // Persist the favorites list. Optimistically updates local state and reverts on failure.
  const saveFavorites = useCallback(
    async (next: string[]): Promise<boolean> => {
      const prev = favoritesRef.current;
      applyFavorites(next);
      try {
        const response = await fetch(`${api.baseUrl}/api/v2/user/favorites`, {
          method: 'POST',
          headers: authedHeaders(),
          body: JSON.stringify({ favorites: next }),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = (await response.json()) as { favorites?: string[] };
        if (data.favorites) applyFavorites(data.favorites);
        return true;
      } catch (err) {
        console.error('Failed to save favorites:', err);
        applyFavorites(prev);
        return false;
      }
    },
    [api.baseUrl, authedHeaders, applyFavorites]
  );

  const toggleFavorite = useCallback(
    (modelId: string): Promise<boolean> => {
      const current = favoritesRef.current;
      return saveFavorites(
        current.includes(modelId) ? current.filter((id) => id !== modelId) : [...current, modelId]
      );
    },
    [saveFavorites]
  );

  // Persist the default model for new chats. Returns whether a server restart is required.
  const saveDefaultModel = useCallback(
    async (modelId: string): Promise<{ ok: boolean; restartRequired: boolean }> => {
      try {
        const response = await fetch(`${api.baseUrl}/api/v2/user/default-model`, {
          method: 'POST',
          headers: authedHeaders(),
          body: JSON.stringify({ model: modelId }),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = (await response.json()) as { restart_required?: boolean };
        setDefaultModel(modelId);
        return { ok: true, restartRequired: !!data.restart_required };
      } catch (err) {
        console.error('Failed to save default model:', err);
        return { ok: false, restartRequired: false };
      }
    },
    [api.baseUrl, authedHeaders]
  );

  const availableModels = models.map((model) => model.id);

  return {
    models,
    availableModels,
    defaultModel,
    isLoading,
    error,
    recommendedModels,
    favorites,
    saveFavorites,
    toggleFavorite,
    saveDefaultModel,
  };
}
