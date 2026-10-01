// Mock modules that use import.meta (not available in jest)
jest.mock('@/utils/connectionConfig', () => ({
  getApiBaseUrl: jest.fn(() => 'http://127.0.0.1:5700'),
  CLOUD_BASE_URL: 'https://gptme.ai',
}));
jest.mock('@/stores/conversations', () => ({
  initConversation: jest.fn(),
  setGenerating: jest.fn(),
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
  isRetryableConnectionFailure,
} from '../api';

class MockEventSource {
  static instances: MockEventSource[] = [];

  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  readyState = 0;
  close = jest.fn(() => {
    this.readyState = 2;
  });

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

describe('isRetryableConnectionFailure', () => {
  it('retries network and timeout probes, not CORS or HTTP failures', () => {
    expect(isRetryableConnectionFailure(null)).toBe(false);
    expect(isRetryableConnectionFailure({ ok: true, url: 'http://127.0.0.1:5700' })).toBe(false);
    expect(
      isRetryableConnectionFailure({
        ok: false,
        url: 'http://127.0.0.1:5700',
        reason: 'timeout',
        message: 'Request timed out after 3s — server may be slow or unreachable',
      })
    ).toBe(true);
    expect(
      isRetryableConnectionFailure({
        ok: false,
        url: 'http://127.0.0.1:5700',
        reason: 'network',
        message: 'Could not reach server (connection refused or no DNS)',
      })
    ).toBe(true);
    expect(
      isRetryableConnectionFailure({
        ok: false,
        url: 'http://127.0.0.1:5700',
        reason: 'cors',
        message: 'Network or CORS error — server may not allow requests from this origin',
      })
    ).toBe(false);
    expect(
      isRetryableConnectionFailure({
        ok: false,
        url: 'http://127.0.0.1:5700',
        reason: 'http_error',
        status: 401,
        message: 'Server is running but requires a bearer token.',
      })
    ).toBe(false);
  });
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

  it('does not report connected when /api/v2 is open but conversations return 401', async () => {
    global.fetch = jest.fn().mockImplementation(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes('/api/v2/conversations')) {
        return {
          ok: false,
          status: 401,
          statusText: 'UNAUTHORIZED',
          json: async () => ({ error: 'Missing authentication credentials' }),
        } as Response;
      }
      return {
        ok: true,
        json: async () => ({
          api_version: CLIENT_API_VERSION,
          contract_revision: CLIENT_MIN_CONTRACT_REVISION,
        }),
      } as Response;
    });

    const client = new ApiClient('http://127.0.0.1:5700');

    await expect(client.checkConnection()).resolves.toBe(false);
    expect(client.isConnected$.get()).toBe(false);
    expect(client.lastConnectionResult$.get()).toMatchObject({
      ok: false,
      reason: 'http_error',
      status: 401,
    });
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
    const okConversations = {
      ok: true,
      status: 200,
      json: async () => [],
    } as Response;
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          api_version: CLIENT_API_VERSION,
          contract_revision: CLIENT_MIN_CONTRACT_REVISION - 1,
        }),
      } as Response)
      .mockResolvedValueOnce(okConversations)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          api_version: CLIENT_API_VERSION,
          contract_revision: CLIENT_MIN_CONTRACT_REVISION,
        }),
      } as Response)
      .mockResolvedValueOnce(okConversations);

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
        ok: true,
        status: 200,
        json: async () => [],
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
    const okConversations = {
      ok: true,
      status: 200,
      json: async () => [],
    } as Response;
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          api_version: CLIENT_API_VERSION,
          contract_revision: CLIENT_MIN_CONTRACT_REVISION - 1,
        }),
      } as Response)
      .mockResolvedValueOnce(okConversations)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ version: '0.30.0' }),
      } as Response)
      .mockResolvedValueOnce(okConversations);

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
      .mockReturnValueOnce(newProbePromise)
      .mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => [],
      } as Response);

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

  // gptme-cloud#1063 follow-up: a tab left open through an outage exhausted its
  // reconnect budget, never got a session id, and every later send failed with
  // "Session ID not found for conversation" until a manual reload.
  const exhaustReconnectBudget = async (instance: () => MockEventSource) => {
    for (let attempt = 1; attempt <= 5; attempt++) {
      instance().emitError();
      jest.advanceTimersByTime(Math.pow(2, attempt - 1) * 1000);
      await Promise.resolve();
      await Promise.resolve();
    }
    instance().emitError(); // sixth failure: fast budget exhausted
  };

  it('keeps retrying slowly after the reconnect budget is exhausted', async () => {
    const client = new ApiClient('http://127.0.0.1:5700');
    const callbacks = createSseCallbacks();
    await client.subscribeToEvents('conv-1', callbacks);
    await exhaustReconnectBudget(() => MockEventSource.instances.at(-1)!);

    expect(callbacks.onConnectionState).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'disconnected' })
    );
    const before = MockEventSource.instances.length;
    jest.advanceTimersByTime(30_000);
    await Promise.resolve();
    await Promise.resolve();
    expect(MockEventSource.instances.length).toBe(before + 1);

    MockEventSource.instances.at(-1)!.emitMessage({ type: 'connected', session_id: 'fresh' });
    expect(client.sessions$.get('conv-1').get()).toBe('fresh');
  });

  // The pool swaps in a fresh client whenever the auth header changes (gptme.ai
  // rotates its session token hourly), so a dropped client must release its
  // wake listeners and reconnect timers instead of leaving them attached for
  // the life of the page.
  it('dispose() detaches wake listeners and clears retry timers', async () => {
    const addSpy = jest.spyOn(window, 'addEventListener');
    const removeSpy = jest.spyOn(window, 'removeEventListener');
    const docAddSpy = jest.spyOn(document, 'addEventListener');
    const docRemoveSpy = jest.spyOn(document, 'removeEventListener');
    const client = new ApiClient('http://127.0.0.1:5700');
    const callbacks = createSseCallbacks();
    await client.subscribeToEvents('conv-1', callbacks);
    await exhaustReconnectBudget(() => MockEventSource.instances.at(-1)!);

    expect(addSpy.mock.calls.map(([type]) => type)).toEqual(
      expect.arrayContaining(['online', 'focus'])
    );
    expect(docAddSpy.mock.calls.map(([type]) => type)).toEqual(
      expect.arrayContaining(['visibilitychange'])
    );

    const before = MockEventSource.instances.length;
    client.dispose();

    expect(removeSpy.mock.calls.map(([type]) => type)).toEqual(
      expect.arrayContaining(['online', 'focus'])
    );
    expect(docRemoveSpy.mock.calls.map(([type]) => type)).toEqual(
      expect.arrayContaining(['visibilitychange'])
    );

    // Neither the slow retry timer nor a focus/online event may re-open a stream.
    jest.advanceTimersByTime(300_000);
    window.dispatchEvent(new Event('focus'));
    await Promise.resolve();
    await Promise.resolve();
    expect(MockEventSource.instances.length).toBe(before);

    addSpy.mockRestore();
    removeSpy.mockRestore();
    docAddSpy.mockRestore();
    docRemoveSpy.mockRestore();
  });

  it('step() re-opens a dead stream and sends instead of failing on a missing session', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', message: 'Step started', session_id: 'fresh' }),
    }) as unknown as typeof fetch;
    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);
    const callbacks = createSseCallbacks();
    await client.subscribeToEvents('conv-1', callbacks);
    await exhaustReconnectBudget(() => MockEventSource.instances.at(-1)!);
    const before = MockEventSource.instances.length;

    const step = client.step('conv-1');
    await Promise.resolve();
    await Promise.resolve();
    // The send re-opened the stream immediately (no waiting for the slow retry).
    expect(MockEventSource.instances.length).toBe(before + 1);
    MockEventSource.instances.at(-1)!.emitMessage({ type: 'connected', session_id: 'fresh' });
    jest.advanceTimersByTime(200);
    await step;

    const [url, init] = (global.fetch as jest.Mock).mock.calls.at(-1)!;
    expect(url).toContain('/api/v2/conversations/conv-1/step');
    expect(JSON.parse(init.body).session_id).toBe('fresh');
  });

  it('step() renews a session the server no longer knows and retries once', async () => {
    const sessionGone = {
      ok: false,
      status: 404,
      statusText: 'Not Found',
      json: async () => ({ error: 'Session not found: old' }),
      text: async () => JSON.stringify({ error: 'Session not found: old' }),
      headers: new Headers({ 'content-type': 'application/json' }),
    };
    const ok = {
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', message: 'Step started', session_id: 'new' }),
    };
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce(sessionGone)
      .mockResolvedValueOnce(ok) as unknown as typeof fetch;
    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);
    const callbacks = createSseCallbacks();
    await client.subscribeToEvents('conv-1', callbacks);
    MockEventSource.instances[0].emitMessage({ type: 'connected', session_id: 'old' });

    const step = client.step('conv-1');
    for (let i = 0; i < 10; i++) await Promise.resolve();
    MockEventSource.instances.at(-1)!.emitMessage({ type: 'connected', session_id: 'new' });
    jest.advanceTimersByTime(200);
    await step;

    const bodies = (global.fetch as jest.Mock).mock.calls.map(([, init]) => JSON.parse(init.body));
    expect(bodies.map((b) => b.session_id)).toEqual(['old', 'new']);
  });

  it('step() returns silently when superseded by a newer step', async () => {
    // The first step's request is in flight when a newer step aborts it. The
    // first step must return silently, not leak its AbortError to the caller
    // (the outer catch used to check this.controller — the newer step's live
    // controller — instead of the first step's own captured controller).
    let rejectFirst: (e: unknown) => void = () => {};
    const firstPending = new Promise((_resolve, reject) => {
      rejectFirst = reject;
    });
    global.fetch = jest
      .fn()
      .mockReturnValueOnce(firstPending)
      .mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ status: 'ok', message: 'Step started', session_id: 's2' }),
      }) as unknown as typeof fetch;
    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);
    await client.subscribeToEvents('conv-1', createSseCallbacks());
    MockEventSource.instances[0].emitMessage({ type: 'connected', session_id: 's1' });

    const first = client.step('conv-1');
    // Let the first step reach its in-flight fetch before the newer step aborts it.
    await Promise.resolve();
    await Promise.resolve();
    const second = client.step('conv-1'); // aborts the first step's in-flight request
    await second;
    rejectFirst(new DOMException('The operation was aborted.', 'AbortError'));

    await expect(first).resolves.toBeUndefined();
  });

  it('confirmTool() renews a session the server no longer knows and retries once', async () => {
    const sessionGone = {
      ok: false,
      status: 404,
      statusText: 'Not Found',
      json: async () => ({ error: 'Session not found: old' }),
      text: async () => JSON.stringify({ error: 'Session not found: old' }),
      headers: new Headers({ 'content-type': 'application/json' }),
    };
    const ok = { ok: true, status: 200, json: async () => ({ status: 'ok' }) };
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce(sessionGone)
      .mockResolvedValueOnce(ok) as unknown as typeof fetch;
    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);
    await client.subscribeToEvents('conv-1', createSseCallbacks());
    MockEventSource.instances[0].emitMessage({ type: 'connected', session_id: 'old' });

    const confirm = client.confirmTool('conv-1', 'tool-1', 'confirm');
    for (let i = 0; i < 10; i++) await Promise.resolve();
    MockEventSource.instances.at(-1)!.emitMessage({ type: 'connected', session_id: 'new' });
    jest.advanceTimersByTime(200);
    await confirm;

    const sessions = (global.fetch as jest.Mock).mock.calls.map(
      ([, init]) => JSON.parse(init.body).session_id
    );
    expect(sessions).toEqual(['old', 'new']);
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

  it('handles watch_event without warning and surfaces it as a system message', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const client = new ApiClient('http://127.0.0.1:5700');
    const callbacks = createSseCallbacks();

    await client.subscribeToEvents('conv-1', callbacks);

    const first = MockEventSource.instances[0];
    first.emitOpen();
    first.emitMessage({
      type: 'watch_event',
      kind: 'subagent',
      status: 'running',
      ref: 'worker-1',
      message: 'halfway',
    });

    expect(warn).not.toHaveBeenCalledWith(
      expect.stringContaining('Unknown event type'),
      expect.anything()
    );
    expect(callbacks.onMessageAdded).toHaveBeenCalledWith(
      expect.objectContaining({
        role: 'system',
        content: "⏳ Subagent 'worker-1' progress: halfway",
      })
    );
  });

  it('keeps sseToken on reconnect after initial failure when no cookie fallback exists', async () => {
    // Cross-origin in jsdom (127.0.0.1 vs localhost origin) → authCookieSet stays false.
    // Bypassing the sseToken here would leave the retry with no credentials at all, so a
    // transient network failure (onerror carries no status code) must not strip the token.
    const client = new ApiClient('http://127.0.0.1:5700', null, 'my-sse-token');
    const callbacks = createSseCallbacks();

    await client.subscribeToEvents('conv-1', callbacks);

    const first = MockEventSource.instances[0];
    // First attempt uses the sseToken in the URL query param
    expect(first.url).toContain('token=my-sse-token');
    // withCredentials is always true
    expect(first.init).toMatchObject({ withCredentials: true });

    // Fail before connecting (wasConnected=false, reconnectCount=0)
    first.emitError();
    jest.advanceTimersByTime(1000);
    await Promise.resolve();

    expect(MockEventSource.instances).toHaveLength(2);
    const second = MockEventSource.instances[1];
    // No cookie fallback exists, so the retry keeps the sseToken
    expect(second.url).toContain('token=my-sse-token');
    expect(second.init).toMatchObject({ withCredentials: true });
  });

  it('keeps sseToken and never exposes the JWT on reconnect when both are set and initial attempt fails', async () => {
    // Cross-origin in jsdom (127.0.0.1 vs localhost origin) so authCookieSet stays false.
    const client = new ApiClient('http://127.0.0.1:5700', 'Bearer jwt-token', 'my-sse-token');
    const callbacks = createSseCallbacks();

    await client.subscribeToEvents('conv-1', callbacks);

    const first = MockEventSource.instances[0];
    // First attempt uses the sseToken (preferred over JWT)
    expect(first.url).toContain('token=my-sse-token');
    expect(first.url).not.toContain('jwt-token');

    // Fail before connecting (wasConnected=false, reconnectCount=0)
    first.emitError();
    jest.advanceTimersByTime(1000);
    await Promise.resolve();

    expect(MockEventSource.instances).toHaveLength(2);
    const second = MockEventSource.instances[1];
    // The retry keeps the sseToken (credential isolation: the JWT is still never in the URL)
    expect(second.url).toContain('token=my-sse-token');
    expect(second.url).not.toContain('jwt-token');
    expect(second.init).toMatchObject({ withCredentials: true });
  });

  it('bypasses sseToken on reconnect after initial failure when cookie auth is available', async () => {
    // Same-origin (jsdom origin) + authHeader → the cookie endpoint succeeds, so the retry
    // can safely fall back to cookie auth instead of re-sending a possibly-bad sseToken.
    const originalFetch = global.fetch;
    global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200 } as Response);
    try {
      const client = new ApiClient(window.location.origin, 'Bearer jwt-token', 'my-sse-token');
      const callbacks = createSseCallbacks();

      await client.subscribeToEvents('conv-1', callbacks);

      const first = MockEventSource.instances[0];
      expect(first.url).toContain('token=my-sse-token');

      // Fail before connecting (wasConnected=false, reconnectCount=0) with cookie available
      first.emitError();
      jest.advanceTimersByTime(1000);
      await Promise.resolve();

      expect(MockEventSource.instances).toHaveLength(2);
      const second = MockEventSource.instances[1];
      // Cookie auth is available, so the sseToken is bypassed (and the JWT is never in the URL)
      expect(second.url).not.toContain('token=');
      expect(second.init).toMatchObject({ withCredentials: true });
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('re-uses sseToken when skipSseToken is set but no cookie is available at retry time', async () => {
    // Mirrors the state after resetAuthCookie clears an expired cookie on reconnect while
    // skipSseToken is still carried forward from a prior attempt. Cross-origin in jsdom →
    // authCookieSet is false, so honoring the skip would leave the retry with no credentials
    // (no cookie, and the JWT fallback is suppressed by the same flag). The sseToken must win.
    const client = new ApiClient('http://127.0.0.1:5700', 'Bearer jwt-token', 'my-sse-token');
    const callbacks = createSseCallbacks();

    await client.subscribeToEvents('conv-1', callbacks, 1, true);

    const first = MockEventSource.instances[0];
    expect(first.url).toContain('token=my-sse-token');
    expect(first.url).not.toContain('jwt-token');
    expect(first.init).toMatchObject({ withCredentials: true });
  });

  it('preserves sseToken on session-ID timeout for cross-origin server', async () => {
    // Cross-origin in jsdom (127.0.0.1 vs localhost origin) → authCookieSet stays false.
    // Without the authCookieSet guard, a 5s session-ID timeout would set skipSseOnTimeout=true,
    // leaving the retry with no credentials (no cookie, no sseToken, no JWT).
    const client = new ApiClient('http://127.0.0.1:5700', null, 'my-sse-token');
    const callbacks = createSseCallbacks();

    await client.subscribeToEvents('conv-1', callbacks);

    const first = MockEventSource.instances[0];
    // First attempt uses the sseToken
    expect(first.url).toContain('token=my-sse-token');

    // Open the EventSource but never emit the session_id — simulates a slow initial handshake
    first.emitOpen();
    // Advance past the 5-second session-ID timeout
    jest.advanceTimersByTime(6000);
    await Promise.resolve();

    expect(MockEventSource.instances).toHaveLength(2);
    const second = MockEventSource.instances[1];
    // For cross-origin servers, sseToken must be preserved on the retry (authCookieSet=false,
    // no cookie fallback available — dropping sseToken leaves the stream unauthenticated)
    expect(second.url).toContain('token=my-sse-token');
  });

  it('preserves sseToken on session-ID timeout for a same-origin server (timeout is not an auth failure)', async () => {
    // Same-origin (jsdom origin) with an authHeader → the cookie endpoint succeeds and
    // authCookieSet=true. A session-ID timeout must still keep the valid instance-scoped
    // sseToken on the retry: the timeout means the stream connected but no session_id
    // arrived (or is still connecting), NOT that the token was rejected. Downgrading to
    // cookie auth here is a credential swap with no evidence of failure.
    const originalFetch = global.fetch;
    global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200 } as Response);
    try {
      const client = new ApiClient(window.location.origin, 'Bearer jwt-token', 'my-sse-token');
      const callbacks = createSseCallbacks();

      await client.subscribeToEvents('conv-1', callbacks);

      const first = MockEventSource.instances[0];
      expect(first.url).toContain('token=my-sse-token');
      expect(first.url).not.toContain('jwt-token');

      // Open but never emit the session_id — a slow handshake, not an auth failure.
      first.emitOpen();
      jest.advanceTimersByTime(6000);
      await Promise.resolve();

      expect(MockEventSource.instances).toHaveLength(2);
      const second = MockEventSource.instances[1];
      // The sseToken must still be used on the retry, even though cookie auth is available.
      expect(second.url).toContain('token=my-sse-token');
      expect(second.url).not.toContain('jwt-token');
    } finally {
      global.fetch = originalFetch;
    }
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
    (conversationsStore.setGenerating as jest.Mock).mockClear();
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

    const conversationId = await client.createConversationWithPlaceholder('hello', {
      workspace: '.',
    });
    // Server creation runs in background; await it explicitly so the fetch
    // mock has been called before we inspect its arguments.
    await client.waitForConversationCreation(conversationId);

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

    const conversationId = await client.createConversationWithPlaceholder('hello');
    await client.waitForConversationCreation(conversationId);

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
    // initConversation is called synchronously, so no need to await server creation here
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

    const conversationId = await client.createConversationWithPlaceholder('hello', {
      workspace: '/workspace/project',
    });
    await client.waitForConversationCreation(conversationId);

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
    // initConversation is called synchronously, so no need to await server creation here
    const [, initData] = (conversationsStore.initConversation as jest.Mock).mock.calls[0];
    expect(initData.workspace).toBe('/home/user/project');
  });

  it('returns the conversation ID before the server responds (no-block navigation)', async () => {
    // Verify the core UX fix: createConversationWithPlaceholder must return the
    // conversation ID before the server fetch resolves so that the UI can navigate
    // to the chat page without waiting for the server round-trip.
    // The server call starts in the background; the returned promise resolves
    // immediately with the local ID so the caller can navigate.
    let fetchResolve!: () => void;
    let fetchWasCalled = false;
    global.fetch = jest.fn().mockImplementation(() => {
      fetchWasCalled = true;
      return new Promise<Response>((res) => {
        fetchResolve = () =>
          res({
            ok: true,
            status: 200,
            json: async () => ({ status: 'ok', session_id: 'session-1' }),
          } as Response);
      });
    });

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    // createConversationWithPlaceholder MUST resolve (return the ID) before the
    // server fetch promise resolves.  We don't resolve fetchResolve until after
    // we have the ID, proving navigation can happen without waiting for the server.
    const idPromise = client.createConversationWithPlaceholder('hello');

    // initConversation is synchronous — must be called before any await.
    expect(conversationsStore.initConversation).toHaveBeenCalledTimes(1);

    // The idPromise should resolve immediately (before we resolve the fetch).
    // We collect the ID without resolving the server response first.
    let conversationId = '';
    let idResolved = false;
    idPromise.then((id) => {
      conversationId = id;
      idResolved = true;
    });

    // Flush microtasks — idPromise should be resolved by now.
    await Promise.resolve();
    expect(idResolved).toBe(true);
    expect(conversationId).toMatch(/^chat-/);
    expect(fetchWasCalled).toBe(true); // fetch started in background

    // Now let the server respond and verify the pending creation settles.
    fetchResolve();
    await client.waitForConversationCreation(conversationId);
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('pre-sets generating so Stop is visible before the SSE handshake', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', session_id: 'session-1' }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    const conversationId = await client.createConversationWithPlaceholder('hello');

    expect(conversationsStore.setGenerating).toHaveBeenCalledWith(conversationId, true);
  });
});

describe('ApiClient interruptGeneration', () => {
  const originalFetch = global.fetch;
  const originalCrypto = global.crypto;

  beforeEach(() => {
    Object.defineProperty(global, 'crypto', {
      value: { ...originalCrypto, randomUUID: jest.fn(() => 'test-client-id') },
      configurable: true,
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    Object.defineProperty(global, 'crypto', { value: originalCrypto, configurable: true });
    jest.restoreAllMocks();
  });

  it('posts interrupt when a session id exists', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok' }),
    } as Response);

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);
    client.sessions$.set('conv-1', 'session-1');

    await client.interruptGeneration('conv-1');

    expect(global.fetch).toHaveBeenCalledWith(
      'http://127.0.0.1:5700/api/v2/conversations/conv-1/interrupt',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ session_id: 'session-1' }),
      })
    );
  });

  it('no-ops without error when no session id exists yet', async () => {
    global.fetch = jest.fn();

    const client = new ApiClient('http://127.0.0.1:5700');
    client.setConnected(true);

    await expect(client.interruptGeneration('conv-1')).resolves.toBeUndefined();
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
