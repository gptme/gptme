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

  it('returns demo user info', async () => {
    const client = createDemoApiClient();
    const user = await client.getUserInfo();
    expect(user.name).toBe('Demo User');
    // userInfo$ observable is pre-seeded
    expect(client.userInfo$.get()?.name).toBe('Demo User');
  });

  it('includes the fixture demo conversation in list calls', async () => {
    const client = createDemoApiClient();
    const list = await client.getConversations();
    expect(list.length).toBeGreaterThanOrEqual(1);
    expect(list[0].id).toBe('demo/gptme-intro');
    expect(list[0].readonly).toBe(true);

    const paginated = await client.getConversationsPaginated();
    expect(paginated.conversations.length).toBeGreaterThanOrEqual(1);
    expect(paginated.conversations[0].id).toBe('demo/gptme-intro');
    expect(paginated.nextCursor).toBeUndefined();
  });

  it('serves the fixture conversation for the known demo ID', async () => {
    const client = createDemoApiClient();
    const conv = await client.getConversation('demo/gptme-intro');
    expect(conv.id).toBe('demo/gptme-intro');
    expect(conv.log.length).toBeGreaterThan(0);
    // Should include both user and assistant messages
    const roles = conv.log.map((m) => m.role);
    expect(roles).toContain('user');
    expect(roles).toContain('assistant');
  });

  it('throws DemoModeError for an unknown conversation ID', async () => {
    const client = createDemoApiClient();
    await expect(client.getConversation('unknown/chat')).rejects.toBeInstanceOf(DemoModeError);
  });

  it('returns a chat config for the demo', async () => {
    const client = createDemoApiClient();
    const config = await client.getChatConfig('any');
    expect(config.chat.model).toBe('gptme-demo');
    expect(config.mcp.enabled).toBe(false);
  });

  it('creates and retrieves in-memory conversations', async () => {
    const client = createDemoApiClient();
    const messages = [{ role: 'user' as const, content: 'hello' }];
    await client.createConversation('demo/my-conv', messages);
    const retrieved = await client.getConversation('demo/my-conv');
    expect(retrieved.id).toBe('demo/my-conv');
    expect(retrieved.log).toHaveLength(1);
    expect(retrieved.log[0].content).toBe('hello');
  });

  it('createConversationWithPlaceholder returns a logfile and is retrievable', async () => {
    const client = createDemoApiClient();
    const logfile = await client.createConversationWithPlaceholder('What is gptme?');
    expect(typeof logfile).toBe('string');
    expect(logfile.length).toBeGreaterThan(0);
    const conv = await client.getConversation(logfile);
    expect(conv.log[0].content).toBe('What is gptme?');
  });

  it('searches the demo conversation by name', async () => {
    const client = createDemoApiClient();
    const results = await client.searchConversations('fibonacci');
    expect(results.length).toBeGreaterThanOrEqual(1);
    const noResults = await client.searchConversations('zzznomatch');
    expect(noResults).toHaveLength(0);
  });

  it('throws DemoModeError for write paths without fixtures', async () => {
    const client = createDemoApiClient();
    await expect(
      client.sendMessage('chat-1', { role: 'user', content: 'hi' })
    ).rejects.toBeInstanceOf(DemoModeError);
    await expect(client.editMessage('chat-1', 0, 'new content')).rejects.toBeInstanceOf(
      DemoModeError
    );
    await expect(client.uploadFiles('chat-1', [] as never)).rejects.toBeInstanceOf(DemoModeError);
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
