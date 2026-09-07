import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { QueryClient } from '@tanstack/react-query';
import { conversationsQueryKey } from '../useConversationsInfiniteQuery';

const BASE_URL = 'http://localhost:5700';

const seedConversationsQuery = (client: QueryClient) => {
  client.setQueryData(conversationsQueryKey(BASE_URL), {
    pages: [{ conversations: [], nextCursor: undefined }],
    pageParams: [undefined],
  });
};

const isInvalidated = (client: QueryClient) =>
  client.getQueryState(conversationsQueryKey(BASE_URL))?.isInvalidated ?? false;

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
