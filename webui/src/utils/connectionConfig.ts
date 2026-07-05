import {
  findOrCreateServerByUrl,
  connectServer,
  getActiveServer,
  setActiveServer,
  updateServer,
} from '@/stores/servers';

const DEFAULT_API_URL = 'http://127.0.0.1:5700';
const DEFAULT_CLOUD_APP_BASE_URL = 'https://gptme.ai';
const DEFAULT_CLOUD_EXCHANGE_BASE_URL = 'https://fleet.gptme.ai';

declare global {
  interface Window {
    __GPTME_WEBUI_ENV__?: Record<string, string | undefined>;
  }
}

function trimTrailingSlash(url: string): string {
  return url.replace(/\/+$/, '');
}

function isUnsetViteHtmlPlaceholder(value: string): boolean {
  return /^%VITE_[A-Z0-9_]+%$/.test(value);
}

/**
 * Whether the app should run in offline demo mode (fixture-backed, no live
 * backend, no auth). Activated by a `?demo=1` URL flag so a demo can be shared
 * as a plain link. Safe to call outside a browser (returns false).
 */
export function isDemoMode(): boolean {
  if (typeof window === 'undefined' || !window.location) {
    return false;
  }
  return new URLSearchParams(window.location.search).get('demo') === '1';
}

// Browser builds can inject runtime env from index.html before this module
// loads. Keep process.env fallback for Jest/Node tests; the Function() path is
// retained only for compatibility with any bundler that supports it.
function getEnvVar(name: string): string | undefined {
  if (typeof window !== 'undefined') {
    const value = window.__GPTME_WEBUI_ENV__?.[name];
    if (value && !isUnsetViteHtmlPlaceholder(value)) {
      return value;
    }
  }

  try {
    const value = Function(`return import.meta.env.${name}`)() as string | undefined;
    if (value && !isUnsetViteHtmlPlaceholder(value)) {
      return value;
    }
  } catch {
    // Jest / Node runtime (import.meta not available)
    if (typeof process !== 'undefined' && process.env) {
      const value = process.env[name];
      if (value && !isUnsetViteHtmlPlaceholder(value)) {
        return value;
      }
    }
  }
  return undefined;
}

// The browser auth UI lives on gptme.ai, but the auth-code exchange POST is
// handled by the fleet operator. For custom single-origin deployments, keep the
// previous "same origin" behavior unless an explicit fleet base URL is set.
function getCloudAppBaseUrl(): string {
  return trimTrailingSlash(getEnvVar('VITE_GPTME_CLOUD_BASE_URL') || DEFAULT_CLOUD_APP_BASE_URL);
}

export function resolveCloudExchangeBaseUrl(
  cloudAppBaseUrl: string,
  fleetBaseUrl?: string
): string {
  if (fleetBaseUrl) {
    return trimTrailingSlash(fleetBaseUrl);
  }
  if (trimTrailingSlash(cloudAppBaseUrl) === DEFAULT_CLOUD_APP_BASE_URL) {
    return DEFAULT_CLOUD_EXCHANGE_BASE_URL;
  }
  return trimTrailingSlash(cloudAppBaseUrl);
}

function getCloudExchangeBaseUrl(): string {
  return resolveCloudExchangeBaseUrl(getCloudAppBaseUrl(), getEnvVar('VITE_GPTME_FLEET_BASE_URL'));
}

export interface ConnectionConfig {
  baseUrl: string;
  authToken: string | null;
  useAuthToken: boolean;
}

export interface AuthCodeExchangeResult {
  userToken: string;
  instanceUrl: string;
  instanceId: string;
}

/**
 * Exchange an auth code for a user token via the fleet-operator.
 * This implements the secure auth code flow where tokens are never exposed in URLs.
 */
export async function exchangeAuthCode(
  code: string,
  exchangeUrl: string
): Promise<AuthCodeExchangeResult> {
  const response = await fetch(exchangeUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ code }),
  });

  if (!response.ok) {
    // Default to HTTP status — shown when JSON parsing fails (e.g. nginx HTML error page)
    let errorMessage = `Auth code exchange failed: HTTP ${response.status}`;
    try {
      const body = await response.text();
      try {
        const parsed = JSON.parse(body);
        if (parsed.hint) {
          errorMessage = parsed.hint;
        } else if (parsed.error) {
          errorMessage = parsed.error;
        }
      } catch {
        // Non-JSON body (nginx HTML error page, etc.) — log for diagnostics
        console.error('[ConnectionConfig] Non-JSON error response body:', body.slice(0, 300));
      }
    } catch {
      // Body unreadable — keep status-based message
    }
    throw new Error(errorMessage);
  }

  return response.json();
}

/**
 * Check if URL hash contains auth code flow parameters.
 * Returns the code if present. Exchange URL is derived from configuration.
 */
function getAuthCodeParams(hash?: string): { code: string } | null {
  const params = new URLSearchParams(hash || '');
  const code = params.get('code');

  if (code) {
    return { code };
  }
  return null;
}

/**
 * Get the exchange URL for auth code flow.
 * Derives from VITE_GPTME_FLEET_BASE_URL when present, otherwise falls back to
 * the managed-service default or the custom cloud app origin.
 */
function getExchangeUrl(): string {
  return `${getCloudExchangeBaseUrl()}/api/v1/operator/auth/exchange`;
}

export function getConnectionConfigFromSources(hash?: string): ConnectionConfig {
  const params = new URLSearchParams(hash || '');

  // Get values from fragment (legacy direct token flow)
  const fragmentBaseUrl = params.get('baseUrl');
  const fragmentUserToken = params.get('userToken');

  // Register fragment values as a server in the registry
  if (fragmentBaseUrl) {
    const server = findOrCreateServerByUrl(fragmentBaseUrl, {
      authToken: fragmentUserToken || null,
      useAuthToken: Boolean(fragmentUserToken),
    });
    connectServer(server.id);
    setActiveServer(server.id);

    // Clean fragment from URL
    if (typeof window !== 'undefined') {
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
    }

    return {
      baseUrl: server.baseUrl,
      authToken: server.authToken,
      useAuthToken: server.useAuthToken,
    };
  }

  // Clean fragment if only userToken was provided (update active server)
  if (fragmentUserToken) {
    const active = getActiveServer();
    if (active) {
      updateServer(active.id, { authToken: fragmentUserToken, useAuthToken: true });
    }

    if (typeof window !== 'undefined') {
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
    }
  }

  // Read from active server in registry
  const activeServer = getActiveServer();
  if (activeServer) {
    return {
      baseUrl: activeServer.baseUrl,
      authToken: activeServer.authToken,
      useAuthToken: activeServer.useAuthToken,
    };
  }

  // Fallback (should not happen since registry always has at least one server)
  return {
    baseUrl: getEnvVar('VITE_GPTME_API_URL') || DEFAULT_API_URL,
    authToken: null,
    useAuthToken: false,
  };
}

/**
 * Process URL hash for connection configuration.
 * Handles both legacy direct token flow and new auth code exchange flow.
 *
 * @returns ConnectionConfig after processing (may involve async exchange)
 */
export async function processConnectionFromHash(hash?: string): Promise<ConnectionConfig> {
  const authCodeParams = getAuthCodeParams(hash);

  if (authCodeParams) {
    // Auth code flow: exchange code for token
    console.log('[ConnectionConfig] Auth code flow detected, exchanging code...');

    try {
      const exchangeUrl = getExchangeUrl();
      const result = await exchangeAuthCode(authCodeParams.code, exchangeUrl);

      // Register the exchanged server in the registry
      const server = findOrCreateServerByUrl(result.instanceUrl, {
        authToken: result.userToken,
        useAuthToken: true,
      });
      connectServer(server.id);
      setActiveServer(server.id);

      // Clean fragment from URL
      if (typeof window !== 'undefined') {
        window.history.replaceState(null, '', window.location.pathname + window.location.search);
      }

      console.log('[ConnectionConfig] Auth code exchanged successfully');

      return {
        baseUrl: result.instanceUrl,
        authToken: result.userToken,
        useAuthToken: true,
      };
    } catch (error) {
      console.error('[ConnectionConfig] Auth code exchange failed:', error);
      throw error;
    }
  }

  // Legacy flow: direct token in hash or from storage
  return getConnectionConfigFromSources(hash);
}

/**
 * Get the current API base URL from the active server
 */
export function getApiBaseUrl(): string {
  const server = getActiveServer();
  return server?.baseUrl || getEnvVar('VITE_GPTME_API_URL') || DEFAULT_API_URL;
}

/**
 * Get the current auth header from the active server
 */
export function getAuthHeader(): string | null {
  const server = getActiveServer();
  if (!server) return null;
  return server.useAuthToken && server.authToken ? `Bearer ${server.authToken}` : null;
}
