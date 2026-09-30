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
    if (isConnected) {
      // Reset as soon as the client connects. Otherwise a first probe that took
      // longer than the grace period leaves graceExpired stuck at true, and the
      // next client swap / drop renders one frame as disconnected before this
      // effect resets it — the exact flash this hook exists to prevent.
      setGraceExpired(false);
      return;
    }
    setGraceExpired(false);
    const timer = setTimeout(() => setGraceExpired(true), CONNECTION_GRACE_MS);
    return () => clearTimeout(timer);
    // Keyed on the client's own observable (one per client instance) rather than
    // the client object, so callers that re-wrap a client don't reset the timer.
  }, [target.isConnected$, isConnected]);

  // The attempt flags (isConnecting$/isAutoConnecting$/isExchangingAuthCode) track
  // the *primary* client only. Applying them to a secondary client would report
  // it as "connecting" whenever the primary is retrying, hiding the secondary's
  // own failed probe (and its retry button). Secondary clients rely on their own
  // lastConnectionResult$ plus the grace period instead.
  const isPrimary = target.isConnected$ === api.isConnected$;

  return deriveConnectionStatus({
    isConnected,
    isAttempting: isPrimary && (isConnecting || isAutoConnecting || isExchangingAuthCode),
    lastResult,
    // No probe is coming for a skipped auto-connect, so don't hold the
    // disconnected guidance back for the grace period.
    graceExpired:
      graceExpired || (target.isConnected$ === api.isConnected$ && !!autoConnectSkipped),
  });
}
