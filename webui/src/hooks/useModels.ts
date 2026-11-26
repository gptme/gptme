import { useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { use$ } from '@legendapp/state/react';
import { useApi } from '@/contexts/ApiContext';
import { setupWizard$ } from '@/stores/setupWizard';
import { buildModelsFetchError } from '@/utils/modelsError';
import { isDemoMode } from '@/utils/connectionConfig';
import { formatUnknownError } from '@/utils/errors';

export interface ModelInfo {
  id: string;
  provider: string;
  model: string;
  context: number;
  max_output?: number | null;
  deprecated?: boolean;
  supports_streaming: boolean;
  supports_vision: boolean;
  supports_reasoning: boolean;
  reasoning_effort_levels?: string[];
  price_input?: number | null;
  price_output?: number | null;
}

export interface ModelsResponse {
  models: ModelInfo[];
  default: string | null;
  recommended: string[];
  favorites?: string[];
}

const EMPTY_MODELS: ModelInfo[] = [];
const EMPTY_IDS: string[] = [];

export function useModels() {
  const { api, isConnected$ } = useApi();
  const isConnected = use$(isConnected$);
  // Setup can restart the same server without changing its URL, token or
  // connected flag. Refresh the catalog/default alongside provider status.
  const providerStatusVersion = use$(setupWizard$.providerStatusVersion);
  const queryClient = useQueryClient();
  // All mounted pickers share one catalog, scoped to the server and credentials.
  // Read mutations from this cache too: a second consumer may save before React
  // has rendered the first consumer's optimistic update.
  const queryKey = ['models', api.baseUrl, api.authHeader, providerStatusVersion] as const;
  const enabled = isConnected && !isDemoMode();

  const authedHeaders = useCallback(() => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (api.authHeader) headers.Authorization = api.authHeader;
    return headers;
  }, [api.authHeader]);

  const { data, isLoading, error, refetch } = useQuery<ModelsResponse>({
    queryKey,
    enabled,
    queryFn: async ({ signal }) => {
      const response = await fetch(`${api.baseUrl}/api/v2/models`, {
        headers: authedHeaders(),
        signal,
      });
      if (!response.ok) throw await buildModelsFetchError(response);
      return response.json();
    },
    retry: false,
    // Re-activating a cached server must refresh externally changed preferences.
    staleTime: 0,
  });

  // Persist the favorites list. Optimistically updates shared state and reverts on failure.
  const favoritesMutation = useMutation({
    // Serialize writes from every consumer so rapid toggles cannot send competing
    // replacement lists or roll back a newer successful save.
    scope: { id: JSON.stringify(queryKey) },
    mutationFn: async (change: { next: string[] } | { modelId: string }): Promise<boolean> => {
      // Join any activation/Settings refresh before deriving a replacement list.
      // Cancelling it would discard newer favorites and default-model settings.
      try {
        await queryClient.fetchQuery<ModelsResponse>({
          queryKey,
          staleTime: queryClient.getQueryState(queryKey)?.fetchStatus === 'fetching' ? 0 : Infinity,
        });
      } catch (err) {
        console.error('Failed to refresh models before saving favorites:', err);
        return false;
      }
      const prev = queryClient.getQueryData<ModelsResponse>(queryKey)?.favorites || [];
      const next =
        'next' in change
          ? change.next
          : prev.includes(change.modelId)
            ? prev.filter((id) => id !== change.modelId)
            : [...prev, change.modelId];
      const applyFavorites = (favorites: string[]) => {
        queryClient.setQueryData<ModelsResponse>(queryKey, (current) =>
          current ? { ...current, favorites } : current
        );
      };
      applyFavorites(next);
      try {
        const response = await fetch(`${api.baseUrl}/api/v2/user/favorites`, {
          method: 'POST',
          headers: authedHeaders(),
          body: JSON.stringify({ favorites: next }),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const saved = (await response.json()) as { favorites?: string[] };
        if (saved.favorites) applyFavorites(saved.favorites);
        return true;
      } catch (err) {
        console.error('Failed to save favorites:', err);
        applyFavorites(prev);
        return false;
      }
    },
  });
  const saveFavorites = (next: string[]) => favoritesMutation.mutateAsync({ next });
  const toggleFavorite = (modelId: string) => favoritesMutation.mutateAsync({ modelId });

  // Persist the default model for new chats. Returns whether a server restart is required.
  const saveDefaultModel = async (
    modelId: string
  ): Promise<{ ok: boolean; restartRequired: boolean }> => {
    try {
      await queryClient.cancelQueries({ queryKey, exact: true });
      const response = await fetch(`${api.baseUrl}/api/v2/user/default-model`, {
        method: 'POST',
        headers: authedHeaders(),
        body: JSON.stringify({ model: modelId }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const saved = (await response.json()) as { restart_required?: boolean };
      // A refresh can start while the POST is pending. Abort it before publishing
      // the confirmed default so its older response cannot overwrite the save.
      await queryClient.cancelQueries({ queryKey, exact: true });
      queryClient.setQueryData<ModelsResponse>(queryKey, (current) =>
        current ? { ...current, default: modelId } : current
      );
      return { ok: true, restartRequired: !!saved.restart_required };
    } catch (err) {
      console.error('Failed to save default model:', err);
      return { ok: false, restartRequired: false };
    }
  };

  const models = data?.models || EMPTY_MODELS;
  return {
    models,
    availableModels: models.map((model) => model.id),
    defaultModel: data?.default || null,
    isLoading: enabled && isLoading,
    error: error ? formatUnknownError(error, 'Failed to fetch models') : null,
    recommendedModels: data?.recommended || EMPTY_IDS,
    favorites: data?.favorites || EMPTY_IDS,
    saveFavorites,
    toggleFavorite,
    saveDefaultModel,
    refetch,
  };
}
