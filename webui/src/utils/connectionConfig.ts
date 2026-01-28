const DEFAULT_API_URL = 'http://127.0.0.1:5700';

// Fleet operator URL for auth code exchange
// Configure via VITE_FLEET_OPERATOR_URL environment variable
const FLEET_OPERATOR_URL = import.meta.env.VITE_FLEET_OPERATOR_URL || 'https://fleet.gptme.ai';

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
 * Derives from VITE_FLEET_OPERATOR_URL environment variable.
 */
function getExchangeUrl(): string {
  return `${FLEET_OPERATOR_URL}/api/v1/operator/auth/exchange`;
}

export function getConnectionConfigFromSources(hash?: string): ConnectionConfig {
  const params = new URLSearchParams(hash || '');

  // Get values from fragment (legacy direct token flow)
  const fragmentBaseUrl = params.get('baseUrl');
  const fragmentUserToken = params.get('userToken');

  // Save fragment values to localStorage if present
  // Wrap in try/catch for private browsing mode or disabled storage
  try {
    if (fragmentBaseUrl) {
      localStorage.setItem('gptme_baseUrl', fragmentBaseUrl);
    }
    if (fragmentUserToken) {
      localStorage.setItem('gptme_userToken', fragmentUserToken);
    }
  } catch {
    // localStorage unavailable (private browsing, storage disabled, etc.)
    console.warn('[ConnectionConfig] localStorage unavailable, config will not persist');
  }

  // Clean fragment from URL if parameters were found
  if ((fragmentBaseUrl || fragmentUserToken) && typeof window !== 'undefined') {
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  }

  // Get stored values with fallback for unavailable localStorage
  let storedBaseUrl: string | null = null;
  let storedUserToken: string | null = null;
  try {
    storedBaseUrl = localStorage.getItem('gptme_baseUrl');
    storedUserToken = localStorage.getItem('gptme_userToken');
  } catch {
    // localStorage unavailable
  }

  return {
    baseUrl: fragmentBaseUrl || storedBaseUrl || import.meta.env.VITE_API_URL || DEFAULT_API_URL,
    authToken: fragmentUserToken || storedUserToken || null,
    useAuthToken: Boolean(fragmentUserToken || storedUserToken),
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

      // Save exchanged values to localStorage
      // Wrap in try/catch for private browsing mode or disabled storage
      try {
        localStorage.setItem('gptme_baseUrl', result.instanceUrl);
        localStorage.setItem('gptme_userToken', result.userToken);
      } catch {
        // localStorage unavailable (private browsing, storage disabled, etc.)
        console.warn('[ConnectionConfig] localStorage unavailable, config will not persist');
      }

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
      // Fall back to stored/default config on exchange failure
      // The user will see an error and can try again
      throw error;
    }
  }

  // Legacy flow: direct token in hash or from storage
  return getConnectionConfigFromSources(hash);
}

/**
 * Get the current API base URL from the same sources as the main API client
 */
export function getApiBaseUrl(): string {
  return getConnectionConfigFromSources().baseUrl;
}

/**
 * Get the current auth header from the same sources as the main API client
 */
export function getAuthHeader(): string | null {
  const config = getConnectionConfigFromSources();
  return config.useAuthToken && config.authToken ? `Bearer ${config.authToken}` : null;
}
