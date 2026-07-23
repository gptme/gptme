// Mock modules that use import.meta (not available in jest)
jest.mock('@/utils/connectionConfig', () => ({
  getApiBaseUrl: jest.fn(() => 'http://127.0.0.1:5700'),
  CLOUD_BASE_URL: 'https://gptme.ai',
}));
jest.mock('@/stores/conversations', () => ({
  initConversation: jest.fn(),
  setMaxTokens: jest.fn(),
  setTemperature: jest.fn(),
  setTopP: jest.fn(),
}));
jest.mock('@/stores/servers', () => ({
  serverRegistry$: { get: jest.fn(() => ({ servers: [], activeServerId: null })) },
  getActiveServer: jest.fn(),
  getPrimaryClient: jest.fn(),
}));

import * as conversationsStore from '@/stores/conversations';

import {
  ApiClient,
  ApiClientError,
  CLIENT_API_VERSION,
  CLIENT_MIN_CONTRACT_REVISION,
  getApiErrorPresentation,
  isLikelyChromeCorsPna,
} from '../api';

class MockEventSource {
  static instances: MockEventSource[] = [];

  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = jest.fn();

  constructor(
    public url: string,
    public init?: EventSourceInit
  ) {
    MockEventSource.instances.push(this);
  }

  emitOpen() {
    this.onopen?.();
  }

  emitMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }

  emitError() {
    this.onerror?.(new Event('error'));
  }
}

const createSseCallbacks = () => ({
  onMessageStart: jest.fn(),
  onToken: jest.fn(),
  onMessageComplete: jest.fn(),
  onMessageAdded: jest.fn(),
  onToolPending: jest.fn(),
  onToolExecuting: jest.fn(),
  onInterrupted: jest.fn(),
  onError: jest.fn(),
  onConnectionState: jest.fn(),
});

describe('isLikelyChromeCorsPna', () => {
  const setHostname = (hostname: string) => {
    Object.defineProperty(window, 'location', {
      value: { ...window.location, hostname },
      writable: true,
      configurable: true,
    });
  };

  it('returns true when public origin connects to localhost', () => {
    setHostname('chat.gptme.org');
    expect(isLikelyChromeCorsPna('http://localhost:5700')).toBe(true);
  });

  it('returns true when public origin connects to 127.0.0.1', () => {
    setHostname('chat.gptme.org');
    expect(isLikelyChromeCorsPna('http://127.0.0.1:5700')).toBe(true);
  });

  it('returns true when public origin connects to private 192.168.x.x', () => {
    setHostname('example.com');
    expect(isLikelyChromeCorsPna('http://192.168.1.100:5700')).toBe(true);
  });

  it('returns false when already on localhost (no PNA concern)', () => {
    setHostname('localhost');
    expect(isLikelyChromeCorsPna('http://localhost:5700')).toBe(false);
  });

  it('returns false when public-to-public (not PNA)', () => {
    setHostname('chat.gptme.org');
    expect(isLikelyChromeCorsPna('https://api.example.com')).toBe(false);
  });

  it('returns false for invalid URL', () => {
    setHostname('chat.gptme.org');
    expect(isLikelyChromeCorsPna('not-a-url')).toBe(false);
  });
});

describe('ApiClient API compatibility', () => {
  const originalFetch = global.fetch;
  const originalCrypto = global.crypto;

  beforeEach(() => {
    Object.defineProperty(global, 'crypto', {
      value: {
        ...originalCrypto,
        randomUUID: jest.fn(() => 'test-client-id'),
      },
      configurable: true,
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    Object.defineProperty(global, 'crypto', {
      value: originalCrypto,
      configurable: true,
    });
    jest.restoreAllMocks();
  });

  it('records compatible server contract metadata during connection', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        api_version: CLIENT_API_VERSION,
        contract_revision: CLIENT_MIN_CONTRACT_REVISION,
      }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');

    await expect(client.checkConnection()).resolves.toBe(true);
    expect(client.compatibilityWarning$.get()).toBeNull();
  });

  it('warns but remains connected when the server contract is older', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        api_version: CLIENT_API_VERSION,
        contract_revision: CLIENT_MIN_CONTRACT_REVISION - 1,
      }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');

    await expect(client.checkConnection()).resolves.toBe(true);
    expect(client.isConnected$.get()).toBe(true);
    expect(client.compatibilityWarning$.get()).toEqual({
      kind: 'server_older',
      serverApiVersion: CLIENT_API_VERSION,
      serverContractRevision: CLIENT_MIN_CONTRACT_REVISION - 1,
      clientApiVersion: CLIENT_API_VERSION,
      minimumContractRevision: CLIENT_MIN_CONTRACT_REVISION,
    });
  });

  it('warns but remains connected when the server uses another API major', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        api_version: CLIENT_API_VERSION + 1,
        contract_revision: 1,
      }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');

    await expect(client.checkConnection()).resolves.toBe(true);
    expect(client.isConnected$.get()).toBe(true);
    expect(client.compatibilityWarning$.get()).toMatchObject({
      kind: 'api_major_mismatch',
      serverApiVersion: CLIENT_API_VERSION + 1,
      clientApiVersion: CLIENT_API_VERSION,
    });
  });

  it('clears a stale compatibility warning after reconnecting to a compatible server', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          api_version: CLIENT_API_VERSION,
          contract_revision: CLIENT_MIN_CONTRACT_REVISION - 1,
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          api_version: CLIENT_API_VERSION,
          contract_revision: CLIENT_MIN_CONTRACT_REVISION,
        }),
      } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');

    await client.checkConnection();
    expect(client.compatibilityWarning$.get()).not.toBeNull();
    await client.checkConnection();
    expect(client.compatibilityWarning$.get()).toBeNull();
  });

  it('clears a stale compatibility warning when a subsequent probe fails', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          api_version: CLIENT_API_VERSION + 1,
          contract_revision: CLIENT_MIN_CONTRACT_REVISION,
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        statusText: 'Service Unavailable',
      } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');

    await client.checkConnection();
    expect(client.compatibilityWarning$.get()).not.toBeNull();
    await client.checkConnection();
    expect(client.compatibilityWarning$.get()).toBeNull();
    expect(client.isConnected$.get()).toBe(false);
  });

  it('keeps legacy servers without version metadata compatible', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          api_version: CLIENT_API_VERSION,
          contract_revision: CLIENT_MIN_CONTRACT_REVISION - 1,
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ version: '0.30.0' }),
      } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');

    await client.checkConnection();
    expect(client.compatibilityWarning$.get()).not.toBeNull();
    await expect(client.checkConnection()).resolves.toBe(true);
    expect(client.compatibilityWarning$.get()).toBeNull();
  });

  it('discards stale probe results when a newer probe finishes first', async () => {
    // Simulate: probe A (older, incompatible) starts first; probe B (newer, compatible) starts
    // second and would finish next. Without a generation guard, probe A's catch-path
    // `compatibilityWarning$.set(null)` or success-path write would overwrite probe B's warning.
    // With the guard: probe A sees _probeNonce !== nonceA and silently returns false.
    let resolveOldProbe!: (r: Response) => void;
    let resolveNewProbe!: (r: Response) => void;

    const oldProbePromise = new Promise<Response>((res) => {
      resolveOldProbe = res;
    });
    const newProbePromise = new Promise<Response>((res) => {
      resolveNewProbe = res;
    });

    global.fetch = jest
      .fn()
      .mockReturnValueOnce(oldProbePromise)
      .mockReturnValueOnce(newProbePromise);

    const client = new ApiClient('http://127.0.0.1:5700');

    // Start probe A (nonce=1) — it won't resolve yet.
    const probeA = client.checkConnection();

    // Start probe B (nonce=2) — probe A is now stale.
    const probeB = client.checkConnection();

    // Probe B resolves first with an incompatible server.
    resolveNewProbe({
      ok: true,
      json: async () => ({
        api_version: CLIENT_API_VERSION + 1,
        contract_revision: CLIENT_MIN_CONTRACT_REVISION,
      }),
    } as Response);
    await probeB;
    expect(client.compatibilityWarning$.get()).not.toBeNull();

    // Probe A (stale) resolves with a network error — must NOT clear the warning.
    resolveOldProbe({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
    } as Response);
    await probeA;

    // Warning from probe B must survive.
    expect(client.compatibilityWarning$.get()).not.toBeNull();
  });
});

describe('ApiClient error parsing', () => {
  const originalFetch = global.fetch;
  const originalCrypto = global.crypto;

  beforeEach(() => {
    Object.defineProperty(global, 'crypto', {
      value: {
        ...originalCrypto,
        randomUUID: jest.fn(() => 'test-client-id'),
      },
      configurable: true,
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    Object.defineProperty(global, 'crypto', {
      value: originalCrypto,
      configurable: true,
    });
    jest.restoreAllMocks();
  });

  it('preserves nested API error messages and metadata on non-OK responses', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 402,
      json: async () => ({
        error: {
          message: 'Insufficient credits. Visit gptme.ai to add more.',
          type: 'payment_required',
          code: 'insufficient_credits',
        },
      }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    await expect(client.getServerInfo()).rejects.toMatchObject({
      message: 'Insufficient credits. Visit gptme.ai to add more.',
      status: 402,
      code: 'insufficient_credits',
      type: 'payment_required',
    } satisfies Partial<ApiClientError>);
  });

  it('handles null error responses without crashing', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({
        error: null,
      }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    // Should not throw TypeError; should surface a graceful error message
    let caught: ApiClientError | undefined;
    try {
      await client.getServerInfo();
    } catch (e) {
      caught = e as ApiClientError;
    }
    expect(caught).toBeInstanceOf(ApiClientError);
    expect(caught!.message).toBe('HTTP error! status: 500');
    expect(caught!.status).toBe(500);
  });

  it('preserves HTTP status for plain-string error responses', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({
        error: 'Not found',
      }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    await expect(client.getServerInfo()).rejects.toMatchObject({
      message: 'Not found',
      status: 404,
    } satisfies Partial<ApiClientError>);
  });

  it('preserves nested API errors even when the server replies with HTTP 200', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        error: {
          message: 'No active subscription. Visit gptme.ai to subscribe.',
          type: 'payment_required',
          code: 'no_subscription',
        },
        status: 402,
      }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    await expect(client.getServerInfo()).rejects.toMatchObject({
      message: 'No active subscription. Visit gptme.ai to subscribe.',
      status: 402,
      code: 'no_subscription',
      type: 'payment_required',
    } satisfies Partial<ApiClientError>);
  });
});

describe('ApiClient conversation list detail flag', () => {
  const originalFetch = global.fetch;
  const originalCrypto = global.crypto;

  beforeEach(() => {
    Object.defineProperty(global, 'crypto', {
      value: {
        ...originalCrypto,
        randomUUID: jest.fn(() => 'test-client-id'),
      },
      configurable: true,
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    Object.defineProperty(global, 'crypto', {
      value: originalCrypto,
      configurable: true,
    });
    jest.restoreAllMocks();
  });

  it('requests paginated conversation lists with cursor pagination', async () => {
    const mockResponse = { conversations: [], next_cursor: null };
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockResponse,
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    // First page (no cursor)
    await client.getConversationsPaginated(undefined, 50);
    // First page with detail
    await client.getConversationsPaginated(undefined, 50, true);
    // Second page with cursor
    await client.getConversationsPaginated('1717500000|conv-123', 50);

    expect(global.fetch).toHaveBeenNthCalledWith(
      1,
      'http://127.0.0.1:5700/api/v2/conversations?limit=50&paginated=1&detail=false',
      expect.any(Object)
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://127.0.0.1:5700/api/v2/conversations?limit=50&paginated=1&detail=true',
      expect.any(Object)
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      3,
      'http://127.0.0.1:5700/api/v2/conversations?limit=50&paginated=1&detail=false&cursor=1717500000%7Cconv-123',
      expect.any(Object)
    );
  });

  it('tolerates a legacy bare-list response from servers older than #2860', async () => {
    const legacyList = [
      { id: 'conv-a', name: 'conv-a' },
      { id: 'conv-b', name: 'conv-b' },
    ];
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => legacyList,
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    const result = await client.getConversationsPaginated(undefined, 50);

    expect(result.conversations).toEqual(legacyList);
    expect(result.nextCursor).toBeUndefined();
  });

  it('returns an empty list when the paginated response is missing the conversations field', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ next_cursor: null }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    const result = await client.getConversationsPaginated(undefined, 50);

    expect(result.conversations).toEqual([]);
    expect(result.nextCursor).toBeUndefined();
  });
});

describe('ApiClient forkConversation', () => {
  const originalFetch = global.fetch;
  const originalCrypto = global.crypto;

  beforeEach(() => {
    Object.defineProperty(global, 'crypto', {
      value: {
        ...originalCrypto,
        randomUUID: jest.fn(() => 'test-client-id'),
      },
      configurable: true,
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    Object.defineProperty(global, 'crypto', {
      value: originalCrypto,
      configurable: true,
    });
    jest.restoreAllMocks();
  });

  it('forks a conversation at the selected message index', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        status: 'ok',
        conversation_id: 'forked-conv',
        session_id: 'fork-session',
      }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    const forkedId = await client.forkConversation('conv-1', 7, 'main-edit-0');

    expect(forkedId).toBe('forked-conv');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://127.0.0.1:5700/api/v2/conversations/conv-1/fork?after_message=7&branch=main-edit-0',
      expect.objectContaining({ method: 'POST' })
    );
    expect(client.sessions$.get('forked-conv').get()).toBe('fork-session');
  });
});

describe('ApiClient rerunTools', () => {
  const originalFetch = global.fetch;
  const originalCrypto = global.crypto;

  beforeEach(() => {
    Object.defineProperty(global, 'crypto', {
      value: {
        ...originalCrypto,
        randomUUID: jest.fn(() => 'test-client-id'),
      },
      configurable: true,
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    Object.defineProperty(global, 'crypto', {
      value: originalCrypto,
      configurable: true,
    });
    jest.restoreAllMocks();
  });

  it('sends the concrete session id in the rerun request body', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', tool_ids: ['tool-1'] }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);
    client.sessions$.set('conv-1', 'session-1');

    const result = await client.rerunTools('conv-1');

    expect(result).toEqual({ status: 'ok', tool_ids: ['tool-1'] });
    expect(global.fetch).toHaveBeenCalledWith(
      'http://127.0.0.1:5700/api/v2/conversations/conv-1/rerun',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ session_id: 'session-1' }),
      })
    );
  });

  it('fails before sending when no session id is available', async () => {
    global.fetch = jest.fn();

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    await expect(client.rerunTools('conv-1')).rejects.toMatchObject({
      message: 'No active session for this conversation',
    });
    expect(global.fetch).not.toHaveBeenCalled();
  });
});

describe('ApiClient event stream reconnection', () => {
  const originalEventSource = global.EventSource;
  const originalCrypto = global.crypto;

  beforeEach(() => {
    jest.useFakeTimers();
    MockEventSource.instances = [];
    Object.defineProperty(global, 'EventSource', {
      value: MockEventSource,
      configurable: true,
    });
    Object.defineProperty(global, 'crypto', {
      value: {
        ...originalCrypto,
        randomUUID: jest.fn(() => 'test-client-id'),
      },
      configurable: true,
    });
  });

  afterEach(() => {
    jest.useRealTimers();
    Object.defineProperty(global, 'EventSource', {
      value: originalEventSource,
      configurable: true,
    });
    Object.defineProperty(global, 'crypto', {
      value: originalCrypto,
      configurable: true,
    });
    jest.restoreAllMocks();
  });

  it('reports reconnect state and reuses the session id after a transient drop', async () => {
    const client = new ApiClient('http://127.0.0.1:5700');
    const callbacks = createSseCallbacks();

    await client.subscribeToEvents('conv-1', callbacks);

    const first = MockEventSource.instances[0];
    first.emitOpen();
    first.emitMessage({ type: 'connected', session_id: 'session-1' });
    expect(callbacks.onConnectionState).toHaveBeenLastCalledWith({ status: 'connected' });

    first.emitError();

    expect(first.close).toHaveBeenCalled();
    expect(callbacks.onConnectionState).toHaveBeenLastCalledWith({
      status: 'reconnecting',
      attempt: 1,
      maxAttempts: 5,
      retryInMs: 1000,
    });

    first.emitMessage({ type: 'generation_progress', token: 'stale' });
    expect(callbacks.onToken).not.toHaveBeenCalled();

    jest.advanceTimersByTime(1000);
    await Promise.resolve();

    expect(MockEventSource.instances).toHaveLength(2);
    expect(MockEventSource.instances[1].url).toContain('session_id=session-1');
    expect(callbacks.onError).not.toHaveBeenCalled();
  });

  it('cancels pending reconnect timers when the stream is closed manually', async () => {
    const client = new ApiClient('http://127.0.0.1:5700');
    const callbacks = createSseCallbacks();

    await client.subscribeToEvents('conv-1', callbacks);

    const first = MockEventSource.instances[0];
    first.emitOpen();
    first.emitMessage({ type: 'connected', session_id: 'session-1' });
    first.emitError();

    client.closeEventStream('conv-1');
    jest.advanceTimersByTime(1000);
    await Promise.resolve();

    expect(MockEventSource.instances).toHaveLength(1);
  });
});

describe('getApiErrorPresentation', () => {
  it('elevates payment errors to a payment-required title', () => {
    const error = new ApiClientError('Insufficient credits. Visit gptme.ai to add more.', 402, {
      type: 'payment_required',
      code: 'insufficient_credits',
    });

    expect(
      getApiErrorPresentation(error, {
        fallbackTitle: 'Failed to send',
        fallbackDescription: 'Failed to send message',
      })
    ).toEqual({
      title: 'Payment required',
      description: 'Insufficient credits. Visit gptme.ai to add more.',
    });
  });

  it('preserves fallback title for generic errors while surfacing the message', () => {
    expect(
      getApiErrorPresentation(new Error('Boom'), {
        fallbackTitle: 'Failed to send',
        fallbackDescription: 'Failed to send message',
      })
    ).toEqual({
      title: 'Failed to send',
      description: 'Boom',
    });
  });

  it('elevates authentication errors to an authentication-failed title', () => {
    const error = new ApiClientError('Invalid or expired token.', 401, {
      type: 'authentication_error',
    });

    expect(
      getApiErrorPresentation(error, {
        fallbackTitle: 'Failed to send',
        fallbackDescription: 'Failed to send message',
      })
    ).toEqual({
      title: 'Authentication failed',
      description: 'Invalid or expired token.',
    });
  });
});

describe('createConversationWithPlaceholder workspace defaults', () => {
  const originalFetch = global.fetch;
  const originalCrypto = global.crypto;

  beforeEach(() => {
    Object.defineProperty(global, 'crypto', {
      value: { ...originalCrypto, randomUUID: jest.fn(() => 'test-client-id') },
      configurable: true,
    });
    (conversationsStore.initConversation as jest.Mock).mockClear();
    (conversationsStore.setMaxTokens as jest.Mock).mockClear();
    (conversationsStore.setTemperature as jest.Mock).mockClear();
    (conversationsStore.setTopP as jest.Mock).mockClear();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    Object.defineProperty(global, 'crypto', { value: originalCrypto, configurable: true });
    jest.restoreAllMocks();
  });

  it('omits workspace from server request when workspace is "." (lets server use @log default)', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', session_id: 'session-1' }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    await client.createConversationWithPlaceholder('hello', { workspace: '.' });

    const request = (global.fetch as jest.Mock).mock.calls[0][1] as RequestInit;
    const body = JSON.parse(request.body as string);
    // workspace: '.' must NOT be forwarded — server's @log default should apply
    expect(body.config?.chat?.workspace).toBeUndefined();
  });

  it('omits workspace from server request when workspace is not provided', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', session_id: 'session-1' }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    await client.createConversationWithPlaceholder('hello');

    const request = (global.fetch as jest.Mock).mock.calls[0][1] as RequestInit;
    const body = JSON.parse(request.body as string);
    expect(body.config?.chat?.workspace).toBeUndefined();
  });

  it('uses @log as the placeholder workspace when no explicit workspace is given', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', session_id: 'session-1' }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    await client.createConversationWithPlaceholder('hello', { workspace: '.' });

    const [, initData] = (conversationsStore.initConversation as jest.Mock).mock.calls[0];
    expect(initData.workspace).toBe('@log');
  });

  it('forwards an explicit custom workspace to the server', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', session_id: 'session-1' }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    await client.createConversationWithPlaceholder('hello', {
      workspace: '/workspace/project',
    });

    const request = (global.fetch as jest.Mock).mock.calls[0][1] as RequestInit;
    const body = JSON.parse(request.body as string);
    expect(body.config?.chat?.workspace).toBe('/workspace/project');
  });

  it('uses the custom workspace in the placeholder too', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', session_id: 'session-1' }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    await client.createConversationWithPlaceholder('hello', {
      workspace: '/home/user/project',
    });

    const [, initData] = (conversationsStore.initConversation as jest.Mock).mock.calls[0];
    expect(initData.workspace).toBe('/home/user/project');
  });
});
