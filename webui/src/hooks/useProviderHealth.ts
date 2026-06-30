import { useCallback, useEffect, useRef } from 'react';
import { use$ } from '@legendapp/state/react';
import { useApi } from '@/contexts/ApiContext';
import { providerHealth$ } from '@/stores/providerHealth';
import type { ProviderHealthResponse } from '@/stores/providerHealth';
import { isDemoMode } from '@/utils/connectionConfig';

export const POLL_INTERVAL_MS = 30_000;

/**
 * Fetch provider health from the server and share state via the global
 * providerHealth$ observable. When `poll` is true, re-fetches every 30s
 * while the calling component is mounted.
 */
export function useProviderHealth(poll = false) {
  const { api, isConnected$ } = useApi();
  const isConnected = use$(isConnected$);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const demoMode = isDemoMode();

  const fetchHealth = useCallback(
    async (force = false) => {
      if (demoMode) {
        providerHealth$.data.set({ providers: {} });
        providerHealth$.isLoading.set(false);
        providerHealth$.error.set(null);
        return;
      }
      providerHealth$.isLoading.set(true);
      providerHealth$.error.set(null);
      try {
        const headers: Record<string, string> = {};
        if (api.authHeader) headers.Authorization = api.authHeader;
        const suffix = force ? '?force=1' : '';
        const response = await fetch(`${api.baseUrl}/api/v2/providers/health${suffix}`, {
          headers,
        });
        if (!response.ok) {
          throw new Error(`Failed to fetch provider health: ${response.statusText}`);
        }
        const data = (await response.json()) as ProviderHealthResponse;
        providerHealth$.data.set(data);
      } catch (err) {
        providerHealth$.error.set(
          err instanceof Error ? err.message : 'Failed to fetch provider health'
        );
      } finally {
        providerHealth$.isLoading.set(false);
      }
    },
    [api.authHeader, api.baseUrl, demoMode]
  );

  useEffect(() => {
    // Don't make requests until connected — avoids LNA/CORS errors on hosted
    // pages (e.g. chat.gptme.org) that would try http://127.0.0.1:5700 before
    // any server is confirmed reachable.
    if (!demoMode && !isConnected) {
      providerHealth$.isLoading.set(false);
      return;
    }
    void fetchHealth();
    if (!demoMode && poll) {
      intervalRef.current = setInterval(() => void fetchHealth(), POLL_INTERVAL_MS);
    }
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [demoMode, fetchHealth, isConnected, poll]);

  const data = use$(providerHealth$.data);
  const isLoading = use$(providerHealth$.isLoading);
  const error = use$(providerHealth$.error);

  return { data, isLoading, error, refresh: fetchHealth };
}
