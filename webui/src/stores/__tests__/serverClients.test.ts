import { createApiClient } from '@/utils/api';
import { getClientForServerConfig } from '../serverClients';

jest.mock('@/utils/api', () => ({
  createApiClient: jest.fn(
    (baseUrl: string, authHeader: string | null, sseToken: string | null) => ({
      baseUrl,
      authHeader,
      sseToken: sseToken ?? null,
      dispose: jest.fn(),
    })
  ),
}));

jest.mock('@/utils/demoApiClient', () => ({
  createDemoApiClient: jest.fn(),
}));

jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: jest.fn(() => false),
}));

jest.mock('../servers', () => ({
  serverRegistry$: { get: jest.fn() },
}));

describe('getClientForServerConfig', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('replaces a cached unauthenticated client with the effective managed-sidecar config', () => {
    const unauthenticated = getClientForServerConfig('local', {
      baseUrl: 'http://127.0.0.1:5712',
      authToken: null,
      useAuthToken: false,
    });
    const authenticated = getClientForServerConfig('local', {
      baseUrl: 'http://127.0.0.1:5712',
      authToken: 'sidecar-token',
      useAuthToken: true,
    });

    expect(authenticated).not.toBe(unauthenticated);
    expect(authenticated).toMatchObject({
      baseUrl: 'http://127.0.0.1:5712',
      authHeader: 'Bearer sidecar-token',
    });
    // The replaced client must be disposed so its DOM listeners and reconnect
    // timers don't outlive the pool entry (hourly token refresh creates a new
    // client each time).
    expect((unauthenticated as unknown as { dispose: jest.Mock }).dispose).toHaveBeenCalled();
    expect(createApiClient).toHaveBeenLastCalledWith(
      'http://127.0.0.1:5712',
      'Bearer sidecar-token',
      null
    );
  });

  it('reuses a client when URL and credentials are unchanged', () => {
    const config = {
      baseUrl: 'http://127.0.0.1:5713',
      authToken: 'stable-token',
      useAuthToken: true,
    };

    const first = getClientForServerConfig('stable', config);
    jest.clearAllMocks();
    const second = getClientForServerConfig('stable', config);

    expect(second).toBe(first);
    expect(createApiClient).not.toHaveBeenCalled();
  });

  it('passes sseToken to createApiClient when provided', () => {
    const client = getClientForServerConfig('sse-token-test', {
      baseUrl: 'http://127.0.0.1:5714',
      authToken: 'user-token',
      useAuthToken: true,
      sseToken: 'instance-sse-token',
    });

    expect(createApiClient).toHaveBeenCalledWith(
      'http://127.0.0.1:5714',
      'Bearer user-token',
      'instance-sse-token'
    );
    expect(client).toMatchObject({ sseToken: 'instance-sse-token' });
  });

  it('replaces cached client when sseToken changes', () => {
    const baseConfig = {
      baseUrl: 'http://127.0.0.1:5715',
      authToken: 'user-token',
      useAuthToken: true,
    };

    const withoutToken = getClientForServerConfig('sse-change-test', baseConfig);
    const withToken = getClientForServerConfig('sse-change-test', {
      ...baseConfig,
      sseToken: 'new-sse-token',
    });

    expect(withToken).not.toBe(withoutToken);
    expect(createApiClient).toHaveBeenLastCalledWith(
      'http://127.0.0.1:5715',
      'Bearer user-token',
      'new-sse-token'
    );
  });

  it('disposes the replaced client when sseToken rotates', () => {
    const baseConfig = {
      baseUrl: 'http://127.0.0.1:5717',
      authToken: 'user-token',
      useAuthToken: true,
      sseToken: 'old-sse-token',
    };

    const oldClient = getClientForServerConfig('sse-dispose-test', baseConfig);
    getClientForServerConfig('sse-dispose-test', {
      ...baseConfig,
      sseToken: 'rotated-sse-token',
    });

    expect((oldClient as unknown as { dispose: jest.Mock }).dispose).toHaveBeenCalledTimes(1);
  });

  it('reuses client when sseToken is unchanged', () => {
    const config = {
      baseUrl: 'http://127.0.0.1:5716',
      authToken: 'user-token',
      useAuthToken: true,
      sseToken: 'stable-sse-token',
    };

    const first = getClientForServerConfig('sse-stable-test', config);
    jest.clearAllMocks();
    const second = getClientForServerConfig('sse-stable-test', config);

    expect(second).toBe(first);
    expect(createApiClient).not.toHaveBeenCalled();
  });
});
