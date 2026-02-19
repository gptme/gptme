import {
  findOrCreateServerByUrl,
  getActiveServer,
  setActiveServer,
  updateServer,
} from '@/stores/servers';

const DEFAULT_API_URL = 'http://127.0.0.1:5700';

// Fleet operator URL for auth code exchange
// Configure via VITE_GPTME_CLOUD_BASE_URL environment variable
const FLEET_OPERATOR_URL = import.meta.env.VITE_GPTME_CLOUD_BASE_URL || 'https://fleet.gptme.ai';

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
    const error = await response.json().catch(() => ({ error: 'Unknown error' }));
    throw new Error(error.hint || error.error || `Auth code exchange failed: ${response.status}`);
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
 * Derives from VITE_GPTME_CLOUD_BASE_URL environment variable.
 */
function getExchangeUrl(): string {
  return `${FLEET_OPERATOR_URL}/api/v1/operator/auth/exchange`;
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
    baseUrl: import.meta.env.VITE_GPTME_API_URL || DEFAULT_API_URL,
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
  return server?.baseUrl || import.meta.env.VITE_GPTME_API_URL || DEFAULT_API_URL;
}

/**
 * Get the current auth header from the active server
 */
export function getAuthHeader(): string | null {
  const server = getActiveServer();
  if (!server) return null;
  return server.useAuthToken && server.authToken ? `Bearer ${server.authToken}` : null;
}
