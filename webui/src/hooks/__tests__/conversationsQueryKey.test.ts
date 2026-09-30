import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { QueryClient } from '@tanstack/react-query';
import {
  conversationsCredentialsScope,
  conversationsDataQueryKey,
  conversationsQueryKey,
} from '../useConversationsInfiniteQuery';

const BASE_URL = 'http://localhost:5700';
const DATA_KEY = conversationsDataQueryKey(BASE_URL, 'anon');

const seedConversationsQuery = (client: QueryClient) => {
  client.setQueryData(DATA_KEY, {
    pages: [{ conversations: [], nextCursor: undefined }],
    pageParams: [undefined],
  });
};

const isInvalidated = (client: QueryClient) =>
  client.getQueryState(DATA_KEY)?.isInvalidated ?? false;

describe('conversationsQueryKey', () => {
  it('matches the conversations list query when used for invalidation', async () => {
    const client = new QueryClient();
    seedConversationsQuery(client);

    await client.invalidateQueries({ queryKey: conversationsQueryKey(BASE_URL) });

    expect(isInvalidated(client)).toBe(true);
  });

  it('does not match when callers append extra key segments', async () => {
    // Regression guard: invalidateQueries matches by key *prefix*, so the old
    // `['conversations', baseUrl, isConnected]` call sites silently matched
    // nothing after the query key dropped its isConnected segment — the
    // sidebar list was never refreshed after create/delete/import.
    const client = new QueryClient();
    seedConversationsQuery(client);

    await client.invalidateQueries({ queryKey: ['conversations', BASE_URL, true] });

    expect(isInvalidated(client)).toBe(false);
  });

  it('is stable for the same base URL and distinct across servers', () => {
    expect(conversationsQueryKey(BASE_URL)).toEqual(conversationsQueryKey(BASE_URL));
    expect(conversationsQueryKey(BASE_URL)).not.toEqual(
      conversationsQueryKey('http://localhost:5701')
    );
  });

  it('scopes cached pages to the credentials they were fetched with', () => {
    const anon = conversationsDataQueryKey(
      BASE_URL,
      conversationsCredentialsScope({ useAuthToken: false, authToken: null })
    );
    const accountA = conversationsDataQueryKey(
      BASE_URL,
      conversationsCredentialsScope({ useAuthToken: true, authToken: 'token-a' })
    );
    const accountB = conversationsDataQueryKey(
      BASE_URL,
      conversationsCredentialsScope({ useAuthToken: true, authToken: 'token-b' })
    );
    expect(new Set([anon.join('|'), accountA.join('|'), accountB.join('|')]).size).toBe(3);
    expect(conversationsCredentialsScope({ useAuthToken: true, authToken: 'token-a' })).toBe(
      conversationsCredentialsScope({ useAuthToken: true, authToken: 'token-a' })
    );
  });

  it('keeps one scope across a JWT refresh for the same account, splits different accounts', () => {
    const jwt = (sub: string, iat: number) =>
      `h.${btoa(JSON.stringify({ sub, iat })).replace(/=+$/, '')}.sig`;
    const scope = (authToken: string) =>
      conversationsCredentialsScope({ useAuthToken: true, authToken });
    expect(scope(jwt('user-1', 1))).toBe(scope(jwt('user-1', 2)));
    expect(scope(jwt('user-1', 1))).not.toBe(scope(jwt('user-2', 1)));
    expect(scope('opaque-token')).not.toContain('opaque-token');
  });

  it('scopes opaque tokens with a 64-bit-class hash, not a collidable 32-bit one', () => {
    // A 32-bit hash collides at the birthday bound (~2^16 tokens), which would
    // let one account be served another's cached conversation list.
    const scope = (authToken: string) =>
      conversationsCredentialsScope({ useAuthToken: true, authToken });
    expect(scope('opaque-token')).toMatch(/^token-[0-9a-f]{16}$/);
    expect(scope('opaque-token')).not.toBe(scope('opaque-tokeN'));
    expect(scope('opaque-token-1')).not.toBe(scope('opaque-token-2'));
  });

  it('still invalidates every credential scope through the prefix key', async () => {
    const client = new QueryClient();
    const key = conversationsDataQueryKey(
      BASE_URL,
      conversationsCredentialsScope({ useAuthToken: true, authToken: 'token-a' })
    );
    client.setQueryData(key, { pages: [], pageParams: [] });

    await client.invalidateQueries({ queryKey: conversationsQueryKey(BASE_URL) });

    expect(client.getQueryState(key)?.isInvalidated ?? false).toBe(true);
  });
});

describe('conversations invalidation call sites', () => {
  const srcDir = join(__dirname, '../..');

  const walk = (dir: string): string[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) return entry.name === '__tests__' ? [] : walk(full);
      return /\.tsx?$/.test(entry.name) ? [full] : [];
    });

  it('never inlines a conversations query key with extra segments', () => {
    // An inline `['conversations', baseUrl, somethingElse]` is longer than the
    // real query key, so prefix matching makes the invalidation a silent no-op.
    const offenders = walk(srcDir).filter((file) =>
      /queryKey:\s*\['conversations',[^\]]+\]/.test(readFileSync(file, 'utf8'))
    );

    expect(offenders).toEqual([]);
  });
});
