import { useInfiniteQuery } from '@tanstack/react-query';
import { useApi } from '@/contexts/ApiContext';
import { use$ } from '@legendapp/state/react';
import type { ConversationSummary } from '@/types/conversation';
import type { ConnectionConfig } from '@/utils/connectionConfig';

/**
 * Canonical prefix for the conversations list query.
 *
 * Callers that invalidate the list MUST use this instead of an inline array:
 * `invalidateQueries` matches by key *prefix*, so an extra trailing element
 * (e.g. the old `isConnected` flag) silently matches nothing and the list is
 * never refreshed.
 */
export const conversationsQueryKey = (baseUrl: string) => ['conversations', baseUrl] as const;

/** Decode the `sub` claim of a JWT bearer token, or null if it isn't one. */
function jwtSubject(token: string): string | null {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
    const payload = JSON.parse(atob(padded)) as { sub?: unknown };
    return typeof payload.sub === 'string' && payload.sub ? payload.sub : null;
  } catch {
    return null;
  }
}

/**
 * Stable identity of the credentials a cached conversation list belongs to.
 *
 * A credential change can mean a different account on the same server URL, so
 * cached pages are scoped by *who* the credentials belong to rather than by the
 * server URL alone — the new account must not be shown, or served from cache,
 * the previous account's conversations.
 *
 * JWT bearer tokens (what gptme-cloud injects) carry the account in `sub`; that
 * is stable across the hourly token refresh, so a refresh keeps the list. Opaque
 * tokens (self-hosted servers) are scoped by a hash of the token itself: they
 * rarely rotate, and a different token may well be a different account.
 */
export function conversationsCredentialsScope(
  config: Pick<ConnectionConfig, 'useAuthToken' | 'authToken'>
): string {
  if (!config.useAuthToken || !config.authToken) return 'anon';
  const subject = jwtSubject(config.authToken);
  return subject ? `user-${subject}` : `token-${tokenScopeHash(config.authToken)}`;
}

/**
 * Non-reversible scope for an opaque token, so the raw token never appears in a
 * query key.
 *
 * Two independent 32-bit FNV passes (FNV-1a and FNV-1) are concatenated into a
 * 64-bit-class result. A single 32-bit hash hits the birthday bound at ~2^16
 * tokens, at which point two accounts' scopes can collide and one could be
 * served the other's cached conversation list. A cryptographic digest is not
 * reachable here: the query key is computed synchronously on every render, and
 * `crypto.subtle.digest` is async.
 */
function tokenScopeHash(value: string): string {
  let a = 0x811c9dc5; // FNV-1a: xor then multiply
  let b = 0x811c9dc5; // FNV-1: multiply then xor
  for (let i = 0; i < value.length; i++) {
    const c = value.charCodeAt(i);
    a = Math.imul(a ^ c, 0x01000193);
    b = Math.imul(b, 0x01000193) ^ c;
  }
  return (a >>> 0).toString(16).padStart(8, '0') + (b >>> 0).toString(16).padStart(8, '0');
}

/**
 * Full key for the conversations list *data*. Extends the invalidation prefix
 * above so prefix-based `invalidateQueries` calls still match every credential
 * scope.
 */
export const conversationsDataQueryKey = (baseUrl: string, credentialsScope: string) =>
  [...conversationsQueryKey(baseUrl), credentialsScope] as const;

export function useConversationsInfiniteQuery(enabled: boolean = true) {
  const { api, connectionConfig } = useApi();
  const isConnected = use$(api.isConnected$);

  return useInfiniteQuery({
    // Remove isConnected from queryKey to avoid a second query identity when
    // isConnected flips from false→true on auto-connect. The `enabled` flag
    // already controls when the query fires. staleTime=30s prevents redundant
    // refetches within a fresh window (common during auto-connect handshake).
    // The credential scope IS part of the key, so pages cached for one set of
    // credentials can never be served under another.
    queryKey: conversationsDataQueryKey(
      connectionConfig.baseUrl,
      conversationsCredentialsScope(connectionConfig)
    ),
    queryFn: async ({ pageParam }: { pageParam: string | undefined }) => {
      try {
        return await api.getConversationsPaginated(pageParam, 50);
      } catch (err) {
        console.error('Failed to fetch conversations:', err);
        throw err;
      }
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage: {
      conversations: ConversationSummary[];
      nextCursor: string | undefined;
    }) => lastPage.nextCursor,
    enabled: isConnected && enabled,
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}
