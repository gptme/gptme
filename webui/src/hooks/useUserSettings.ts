import { useState, useEffect, useCallback } from 'react';
import { use$ } from '@legendapp/state/react';
import { useApi } from '@/contexts/ApiContext';
import { isDemoMode } from '@/utils/connectionConfig';
import { formatUnknownError } from '@/utils/errors';

export type UserSettingSource =
  | 'env'
  | 'config.local.toml'
  | 'config.toml'
  | 'config.runtime.toml'
  | 'oauth';

export interface UserSettingsProviderSource {
  auth_source: string;
  effective_source: UserSettingSource | null;
}

export interface UserSettingsConfigFiles {
  config_path: string;
  local_config_path: string;
  local_config_exists: boolean;
  runtime_config_path?: string;
  runtime_config_exists?: boolean;
  runtime_is_defaults?: boolean;
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

/**
 * Stable identity of the API endpoint + credentials a settings snapshot
 * belongs to.
 *
 * The settings endpoint is per-server *and* per-account, so a snapshot fetched
 * for one identity must never be rendered for another. Server URL alone is not
 * enough: a credential change can mean a different account on the same server,
 * and disconnecting means there is no server to have answered at all.
 */
function settingsIdentity(
  baseUrl: string,
  authHeader: string | null,
  isConnected: boolean
): string {
  return `${baseUrl}\u0000${authHeader ?? ''}\u0000${isConnected ? 'connected' : 'disconnected'}`;
}

interface UserSettingsSnapshot {
  identity: string;
  settings: UserSettings | null;
  error: string | null;
  isLoading: boolean;
}

export function useUserSettings() {
  const { api, isConnected$ } = useApi();
  const isConnected = use$(isConnected$);
  const [snapshot, setSnapshot] = useState<UserSettingsSnapshot | null>(null);
  const [refetchKey, setRefetchKey] = useState(0);

  const identity = settingsIdentity(api.baseUrl, api.authHeader, isConnected);
  // Only expose the snapshot that belongs to the identity being rendered. A
  // stale snapshot from a previous server/account/disconnect is hidden
  // synchronously during render, before the new fetch has a chance to settle.
  const ownsSnapshot = snapshot !== null && snapshot.identity === identity;
  const settings = ownsSnapshot ? snapshot.settings : null;
  const error = ownsSnapshot ? snapshot.error : null;
  // A same-identity snapshot carries its own loading flag; a foreign snapshot
  // (or none) is replaced by a load whenever we are connected outside demo mode.
  const isLoading = ownsSnapshot ? snapshot.isLoading : isConnected && !isDemoMode();

  useEffect(() => {
    if (isDemoMode()) {
      setSnapshot({ identity, settings: DEMO_USER_SETTINGS, error: null, isLoading: false });
      return;
    }
    // Don't fetch until connected — avoids LNA/CORS errors on hosted pages
    // (e.g. chat.gptme.org) that would otherwise hit http://127.0.0.1:5700
    // before any server is confirmed reachable.
    if (!isConnected) {
      setSnapshot({ identity, settings: null, error: null, isLoading: false });
      return;
    }
    const controller = new AbortController();
    // Guard against a transport that ignores abort: a completion that lands
    // after this effect was superseded (identity switch, refetch or unmount)
    // must not commit into the new identity's render.
    let cancelled = false;

    // A same-identity refetch keeps the current snapshot visible while it
    // reloads; an identity change starts from a blank snapshot.
    setSnapshot((prev) =>
      prev && prev.identity === identity
        ? { ...prev, error: null, isLoading: true }
        : { identity, settings: null, error: null, isLoading: true }
    );

    const fetchSettings = async () => {
      try {
        const headers: Record<string, string> = {};
        if (api.authHeader) {
          headers.Authorization = api.authHeader;
        }
        const response = await fetch(`${api.baseUrl}/api/v2/user/settings`, {
          headers,
          signal: controller.signal,
        });
        if (cancelled) return;
        if (response.status === 404) {
          // Older gptme-server versions don't expose /api/v2/user/settings.
          // Return null settings without surfacing an error so the UI degrades
          // gracefully instead of producing a background 404.
          setSnapshot({ identity, settings: null, error: null, isLoading: false });
          return;
        }
        if (!response.ok) {
          throw new Error(`Failed to fetch user settings: ${response.statusText}`);
        }
        const data = (await response.json()) as UserSettings;
        if (cancelled) return;
        setSnapshot({ identity, settings: data, error: null, isLoading: false });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof Error && err.name === 'AbortError') return;
        setSnapshot({
          identity,
          settings: null,
          error: formatUnknownError(err, 'Failed to fetch user settings'),
          isLoading: false,
        });
      }
    };

    void fetchSettings();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [api.baseUrl, api.authHeader, identity, isConnected, refetchKey]);

  const refetch = useCallback(() => setRefetchKey((k) => k + 1), []);

  return { settings, isLoading, error, refetch };
}
