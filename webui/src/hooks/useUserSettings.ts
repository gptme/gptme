import { useState, useEffect, useCallback } from 'react';
import { useApi } from '@/contexts/ApiContext';

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

export function useUserSettings() {
  const { api } = useApi();
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refetchKey, setRefetchKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    const fetchSettings = async () => {
      setIsLoading(true);
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
  }, [api.baseUrl, api.authHeader, refetchKey]);

  const refetch = useCallback(() => setRefetchKey((k) => k + 1), []);

  return { settings, isLoading, error, refetch };
}
