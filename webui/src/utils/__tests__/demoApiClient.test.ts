// Mock connectionConfig (uses import.meta, not parseable by jest) — see api.test.ts.
jest.mock('@/utils/connectionConfig', () => ({
  getApiBaseUrl: jest.fn(() => 'http://127.0.0.1:5700'),
  isDemoMode: jest.fn(() => false),
}));

import { createDemoApiClient, DemoModeError } from '@/utils/demoApiClient';

describe('createDemoApiClient', () => {
  it('reports a connected, no-auth offline client', async () => {
    const client = createDemoApiClient();
    expect(client.authHeader).toBeNull();
    expect(client.isConnected$.get()).toBe(true);
    expect(await client.checkConnection()).toBe(true);
    const probe = client.lastConnectionResult$.get();
    expect(probe?.ok).toBe(true);
  });

  it('serves empty collections for read paths', async () => {
    const client = createDemoApiClient();
    expect(await client.getConversations()).toEqual([]);
    expect(await client.searchConversations('anything')).toEqual([]);
    expect(await client.getConversationsPaginated()).toEqual({
      conversations: [],
      nextCursor: undefined,
    });
    expect(await client.getSessions()).toEqual([]);
    expect(await client.getExternalSessions()).toEqual([]);
  });

  it('throws DemoModeError for write paths without fixtures (slice 1)', async () => {
    const client = createDemoApiClient();
    await expect(
      client.sendMessage('chat-1', { role: 'user', content: 'hi' })
    ).rejects.toBeInstanceOf(DemoModeError);
    await expect(client.createConversation('chat-1', [])).rejects.toBeInstanceOf(DemoModeError);
    await expect(client.getConversation('chat-1')).rejects.toBeInstanceOf(DemoModeError);
  });

  it('keeps streaming and no-op lifecycle calls harmless', async () => {
    const client = createDemoApiClient();
    await expect(client.subscribeToEvents('chat-1', {} as never)).resolves.toBeUndefined();
    expect(() => client.closeEventStream('chat-1')).not.toThrow();
    await expect(client.interruptGeneration('chat-1')).resolves.toBeUndefined();
    client.setConnected(false);
    expect(client.isConnected$.get()).toBe(false);
  });
});
