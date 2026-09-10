// Mock connectionConfig (uses import.meta, not parseable by jest) — see api.test.ts.
jest.mock('@/utils/connectionConfig', () => ({
  getApiBaseUrl: jest.fn(() => 'http://127.0.0.1:5700'),
  isDemoMode: jest.fn(() => false),
}));

import { createDemoApiClient, DemoModeError } from '@/utils/demoApiClient';
import { conversations$ } from '@/stores/conversations';

const createEventCallbacks = () => ({
  onMessageStart: jest.fn(),
  onToken: jest.fn(),
  onMessageComplete: jest.fn(),
  onMessageAdded: jest.fn(),
  onToolPending: jest.fn(),
  onToolExecuting: jest.fn(),
  onToolOutput: jest.fn(),
  onToolComplete: jest.fn(),
  onInterrupted: jest.fn(),
  onError: jest.fn(),
  onConnectionState: jest.fn(),
  onConnected: jest.fn(),
});

beforeEach(() => {
  sessionStorage.clear();
  conversations$.set(new Map());
});

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
    const logfile = await client.createConversationWithPlaceholder('What is gptme?', {
      stream: false,
    });
    expect(typeof logfile).toBe('string');
    expect(logfile.length).toBeGreaterThan(0);
    const conv = await client.getConversation(logfile);
    expect(conv.log[0].content).toBe('What is gptme?');
    expect(conversations$.get(logfile)?.needsInitialStep.get()).toBe(true);
    expect(conversations$.get(logfile)?.initialStepStream.get()).toBe(false);
  });

  it('searches the demo conversation by name', async () => {
    const client = createDemoApiClient();
    const results = await client.searchConversations('fibonacci');
    expect(results.length).toBeGreaterThanOrEqual(1);
    const noResults = await client.searchConversations('zzznomatch');
    expect(noResults).toHaveLength(0);
  });

  it('created conversations surface in list and search endpoints', async () => {
    const client = createDemoApiClient();
    const messages = [{ role: 'user' as const, content: 'unique query XYZ' }];
    await client.createConversation('demo/my-new-conv', messages);

    // Must appear in flat list
    const list = await client.getConversations();
    expect(list.some((c) => c.id === 'demo/my-new-conv')).toBe(true);

    // Must appear in paginated list
    const paginated = await client.getConversationsPaginated();
    expect(paginated.conversations.some((c) => c.id === 'demo/my-new-conv')).toBe(true);

    // Must be searchable by name
    const found = await client.searchConversations('my-new-conv');
    expect(found.some((c) => c.id === 'demo/my-new-conv')).toBe(true);
  });

  it('accepts demo messages and strips optimistic client-only status from history', async () => {
    const client = createDemoApiClient();
    await client.createConversation('chat-1', []);
    await client.sendMessage('chat-1', {
      role: 'user',
      content: 'hi',
      timestamp: '2026-01-01T00:00:00Z',
      _status: 'pending',
      _error: 'client-only',
    });

    const conv = await client.getConversation('chat-1');
    expect(conv.log).toHaveLength(1);
    expect(conv.log[0]).toMatchObject({ role: 'user', content: 'hi' });
    expect(conv.log[0]).not.toHaveProperty('_status');
    expect(conv.log[0]).not.toHaveProperty('_error');
  });

  it('throws DemoModeError for unsupported mutation paths', async () => {
    const client = createDemoApiClient();
    await expect(client.editMessage('chat-1', 0, 'new content')).rejects.toBeInstanceOf(
      DemoModeError
    );
    await expect(client.uploadFiles('chat-1', [] as never)).rejects.toBeInstanceOf(DemoModeError);
    await expect(client.rerunTools('chat-1')).rejects.toBeInstanceOf(DemoModeError);
  });

  it('subscribes and replays a demo assistant/tool-call flow on step', async () => {
    const client = createDemoApiClient();
    const callbacks = createEventCallbacks();

    await client.createConversation('chat-1', [{ role: 'user', content: 'show me a tool call' }]);
    await client.subscribeToEvents('chat-1', callbacks);

    expect(client.sessions$.get('chat-1').get()).toBe('demo-session-chat-1');
    expect(callbacks.onConnectionState).toHaveBeenCalledWith({ status: 'connected' });
    expect(callbacks.onConnected).toHaveBeenCalled();

    await client.step('chat-1');

    expect(callbacks.onMessageStart).toHaveBeenCalledTimes(2);
    expect(callbacks.onMessageComplete).toHaveBeenCalledTimes(2);
    expect(callbacks.onToolPending).toHaveBeenCalledWith(
      'demo-python-fibonacci',
      expect.objectContaining({ tool: 'shell' }),
      true
    );
    expect(callbacks.onToolExecuting).toHaveBeenCalledWith('demo-python-fibonacci');
    expect(callbacks.onToolOutput).toHaveBeenCalledWith(
      'demo-python-fibonacci',
      expect.stringContaining('[0, 1, 1, 2, 3, 5, 8, 13]')
    );
    expect(callbacks.onToolComplete).toHaveBeenCalledWith('demo-python-fibonacci', 420, true);
    expect(callbacks.onMessageAdded).toHaveBeenCalledWith(
      expect.objectContaining({ role: 'tool' })
    );

    const conv = await client.getConversation('chat-1');
    expect(conv.log.map((m) => m.role)).toEqual(['user', 'assistant', 'tool', 'assistant']);
  });

  it('keeps lifecycle calls harmless', async () => {
    const client = createDemoApiClient();
    const callbacks = createEventCallbacks();
    await expect(client.subscribeToEvents('chat-1', callbacks)).resolves.toBeUndefined();
    expect(() => client.closeEventStream('chat-1')).not.toThrow();
    await expect(client.interruptGeneration('chat-1')).resolves.toBeUndefined();
    client.setConnected(false);
    expect(client.isConnected$.get()).toBe(false);
  });
});

describe('createDemoApiClient — page reload / session recovery', () => {
  it('restores pending initial-step state after client reinit', async () => {
    // First client instance — simulates the original page load where the user
    // typed a prompt and the demo created a generated conversation.
    const client1 = createDemoApiClient();
    const logfile = await client1.createConversationWithPlaceholder('What is gptme?', {
      stream: false,
    });
    expect(logfile).toMatch(/^demo\/conv-/);

    // Second client instance — simulates a page reload (fresh Map, same sessionStorage).
    conversations$.set(new Map());
    const client2 = createDemoApiClient();
    const conv = await client2.getConversation(logfile);
    expect(conv.id).toBe(logfile);
    expect(conv.log[0].content).toBe('What is gptme?');
    expect(conversations$.get(logfile)?.needsInitialStep.get()).toBe(true);
    expect(conversations$.get(logfile)?.initialStepStream.get()).toBe(false);

    await client2.step(logfile);
    conversations$.set(new Map());
    createDemoApiClient();
    expect(conversations$.get(logfile)?.peek()).toBeUndefined();
  });

  it('keeps pending initial-step state when generation fails', async () => {
    const client1 = createDemoApiClient();
    const logfile = await client1.createConversationWithPlaceholder('What is gptme?');
    const callbacks = createEventCallbacks();
    callbacks.onMessageStart.mockImplementation(() => {
      throw new Error('stream failed');
    });
    await client1.subscribeToEvents(logfile, callbacks);

    await expect(client1.step(logfile)).rejects.toThrow('stream failed');

    conversations$.set(new Map());
    createDemoApiClient();
    expect(conversations$.get(logfile)?.needsInitialStep.get()).toBe(true);
  });

  it('restores a conversation created via createConversation after client reinit', async () => {
    const client1 = createDemoApiClient();
    await client1.createConversation('demo/my-saved-conv', [
      { role: 'user', content: 'hello', timestamp: '2026-01-01T00:00:00Z' },
    ]);

    const client2 = createDemoApiClient();
    const conv = await client2.getConversation('demo/my-saved-conv');
    expect(conv.id).toBe('demo/my-saved-conv');
    expect(conv.log[0].content).toBe('hello');
  });

  it('restores a forked conversation after client reinit', async () => {
    const client1 = createDemoApiClient();
    const forkId = await client1.forkConversation('demo/gptme-intro', 2);
    expect(forkId).toMatch(/^demo\/conv-/);

    const client2 = createDemoApiClient();
    const conv = await client2.getConversation(forkId);
    expect(conv.id).toBe(forkId);
    expect(conv.log.length).toBeGreaterThan(0);
  });

  it('persists a recovered missing generated demo conversation', async () => {
    // A generated ID that was never created — e.g. the sessionStorage was cleared
    // or the URL was shared across browsers. The client must not throw here and
    // the recovered history must survive subsequent mutations and client reinit.
    const logfile = 'demo/conv-unknown-1234567890';
    const client1 = createDemoApiClient();
    const recovered = await client1.getConversation(logfile);
    expect(recovered.id).toBe(logfile);
    expect(recovered.name).toBe('Recovered demo conversation');
    expect(recovered.log.length).toBeGreaterThan(0);
    expect(recovered.log[0].role).toBe('system');
    expect(recovered.log[0].content).toMatch(/not found|expired/i);

    await client1.sendMessage(logfile, { role: 'user', content: 'continue' });

    const client2 = createDemoApiClient();
    const restored = await client2.getConversation(logfile);
    expect(restored.log).toEqual([
      ...recovered.log,
      expect.objectContaining({ role: 'user', content: 'continue' }),
    ]);
    await expect(client2.forkConversation(logfile, 0)).resolves.toMatch(/^demo\/conv-/);
  });

  it('does not persist non-demo conversations when later saving a demo conversation', async () => {
    const client1 = createDemoApiClient();
    await client1.createConversation('unknown/chat', []);
    await client1.sendMessage('unknown/chat', { role: 'user', content: 'hello' });
    await client1.createConversation('demo/saved-after-non-demo', []);

    const client2 = createDemoApiClient();
    await expect(client2.getConversation('unknown/chat')).rejects.toBeInstanceOf(DemoModeError);
    await expect(client2.getConversation('demo/saved-after-non-demo')).resolves.toMatchObject({
      id: 'demo/saved-after-non-demo',
    });
  });

  it('ignores non-demo entries already present in persisted storage', async () => {
    sessionStorage.setItem(
      'gptme:demo-conversations',
      JSON.stringify({
        'unknown/chat': {
          id: 'unknown/chat',
          name: 'invalid persisted conversation',
          logfile: 'unknown/chat',
          log: [],
          branches: { main: [] },
        },
      })
    );

    const client = createDemoApiClient();
    await expect(client.getConversation('unknown/chat')).rejects.toBeInstanceOf(DemoModeError);
  });

  it('does NOT apply graceful recovery to non-demo IDs (still throws)', async () => {
    const client = createDemoApiClient();
    await expect(client.getConversation('unknown/chat')).rejects.toBeInstanceOf(DemoModeError);
  });
});
