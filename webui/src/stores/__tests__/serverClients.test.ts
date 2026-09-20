import { createApiClient } from '@/utils/api';
import { getClientForServerConfig } from '../serverClients';

jest.mock('@/utils/api', () => ({
  createApiClient: jest.fn((baseUrl: string, authHeader: string | null) => ({
    baseUrl,
    authHeader,
  })),
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
    expect(createApiClient).toHaveBeenLastCalledWith(
      'http://127.0.0.1:5712',
      'Bearer sidecar-token'
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
});
