// Mock modules that use import.meta (not available in jest)
jest.mock('@/utils/connectionConfig', () => ({
  getApiBaseUrl: jest.fn(() => 'http://127.0.0.1:5700'),
  CLOUD_BASE_URL: 'https://gptme.ai',
}));
jest.mock('@/stores/conversations', () => ({}));
jest.mock('@/stores/servers', () => ({
  serverRegistry$: { get: jest.fn(() => ({ servers: [], activeServerId: null })) },
  getActiveServer: jest.fn(),
  getPrimaryClient: jest.fn(),
}));

import { ApiClient, ApiClientError, isLikelyChromeCorsPna } from '../api';

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
