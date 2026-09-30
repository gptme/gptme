import { use$ } from '@legendapp/state/react';
import { useEffect, useState } from 'react';
import { useApi } from '@/contexts/ApiContext';
import type { ConnectionProbeResult, IApiClient } from '@/utils/api';

/**
 * Tri-state server connection status for rendering.
 *
 * `isConnected === false` is not the same as "disconnected": on a normal load
 * (and whenever the client is swapped, e.g. gptme.ai refreshing the session
 * token every hour) there is a window where nothing has been probed yet. UI
 * that keyed off the boolean flashed "Not connected to API…" placeholders and
 * the "Server not connected" banner for a second or two on every load and
 * token refresh. Only show disconnected UI once a connection attempt has
 * actually failed, or nothing has connected within a grace period.
 */
export type ConnectionStatus = 'connected' | 'connecting' | 'disconnected';

/** How long an un-probed client counts as "connecting" before we call it disconnected. */
export const CONNECTION_GRACE_MS = 5000;

export function deriveConnectionStatus({
  isConnected,
  isAttempting,
  lastResult,
  graceExpired,
}: {
  isConnected: boolean;
  /** A connect / auto-connect attempt (including retry back-off) is in progress. */
  isAttempting: boolean;
  lastResult: ConnectionProbeResult | null | undefined;
  graceExpired: boolean;
}): ConnectionStatus {
  if (isConnected) return 'connected';
  if (isAttempting) return 'connecting';
  if (lastResult && !lastResult.ok) return 'disconnected';
  return graceExpired ? 'disconnected' : 'connecting';
}

/**
 * Connection status for `client` (defaults to the primary client).
 *
 * The grace period restarts whenever the client instance changes or the client
 * drops from connected, so a token refresh / reconnect keeps showing the
 * neutral "connecting" state (and last-known-good data) instead of flashing
 * disconnected UI before the new probe has had a chance to run.
 */
export function useConnectionStatus(client?: IApiClient): ConnectionStatus {
  const { api, isConnecting$, isAutoConnecting$, isExchangingAuthCode, autoConnectSkipped } =
    useApi();
  const target = client ?? api;
  const isConnected = use$(target.isConnected$);
  const lastResult = use$(target.lastConnectionResult$);
  const isConnecting = use$(isConnecting$);
  const isAutoConnecting = use$(isAutoConnecting$);
  const [graceExpired, setGraceExpired] = useState(false);

  useEffect(() => {
    if (isConnected) return;
    setGraceExpired(false);
    const timer = setTimeout(() => setGraceExpired(true), CONNECTION_GRACE_MS);
    return () => clearTimeout(timer);
    // Keyed on the client's own observable (one per client instance) rather than
    // the client object, so callers that re-wrap a client don't reset the timer.
  }, [target.isConnected$, isConnected]);

  return deriveConnectionStatus({
    isConnected,
    isAttempting: isConnecting || isAutoConnecting || isExchangingAuthCode,
    lastResult,
    // No probe is coming for a skipped auto-connect, so don't hold the
    // disconnected guidance back for the grace period.
    graceExpired:
      graceExpired || (target.isConnected$ === api.isConnected$ && !!autoConnectSkipped),
  });
}
