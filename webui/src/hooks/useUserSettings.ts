import { useState, useEffect, useCallback } from 'react';
import { use$ } from '@legendapp/state/react';
import { useApi } from '@/contexts/ApiContext';
import { isDemoMode } from '@/utils/connectionConfig';

export type UserSettingSource = 'env' | 'config.local.toml' | 'config.toml' | 'oauth';

export interface UserSettingsProviderSource {
  auth_source: string;
  effective_source: UserSettingSource | null;
}

export interface UserSettingsConfigFiles {
  config_path: string;
  local_config_path: string;
  local_config_exists: boolean;
  write_target: string;
  local_overrides_main: boolean;
}

export interface UserSettings {
  providers_configured: string[];
  provider_sources?: Record<string, UserSettingsProviderSource>;
  default_model: string | null;
  default_model_source?: UserSettingSource | null;
  config_files?: UserSettingsConfigFiles;
}

const DEMO_USER_SETTINGS: UserSettings = {
  providers_configured: [],
  default_model: null,
};

export function useUserSettings() {
  const { api, isConnected$ } = useApi();
  const isConnected = use$(isConnected$);
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refetchKey, setRefetchKey] = useState(0);

  useEffect(() => {
    if (isDemoMode()) {
      setSettings(DEMO_USER_SETTINGS);
      setError(null);
      setIsLoading(false);
      return;
    }
    // Don't fetch until connected — avoids LNA/CORS errors on hosted pages
    // (e.g. chat.gptme.org) that would otherwise hit http://127.0.0.1:5700
    // before any server is confirmed reachable.
    if (!isConnected) {
      setIsLoading(false);
      return;
    }
    const controller = new AbortController();

    setIsLoading(true);
    const fetchSettings = async () => {
      setError(null);
      try {
        const headers: Record<string, string> = {};
        if (api.authHeader) {
          headers.Authorization = api.authHeader;
        }
        const response = await fetch(`${api.baseUrl}/api/v2/user/settings`, {
          headers,
          signal: controller.signal,
        });
        if (response.status === 404) {
          // Older gptme-server versions don't expose /api/v2/user/settings.
          // Return null settings without surfacing an error so the UI degrades
          // gracefully instead of producing a background 404.
          setSettings(null);
          setIsLoading(false);
          return;
        }
        if (!response.ok) {
          throw new Error(`Failed to fetch user settings: ${response.statusText}`);
        }
        const data = (await response.json()) as UserSettings;
        setSettings(data);
        setIsLoading(false);
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : 'Failed to fetch user settings');
        setSettings(null);
        setIsLoading(false);
      }
    };

    void fetchSettings();
    return () => controller.abort();
  }, [api.baseUrl, api.authHeader, isConnected, refetchKey]);

  const refetch = useCallback(() => setRefetchKey((k) => k + 1), []);

  return { settings, isLoading, error, refetch };
}
