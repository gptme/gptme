import { jest } from '@jest/globals';

const mockFindOrCreateServerByUrl = jest.fn((baseUrl: string, config: Record<string, unknown>) => ({
  id: 'server-1',
  baseUrl,
  authToken: config.authToken,
  useAuthToken: config.useAuthToken,
}));
const mockGetActiveServer = jest.fn(() => null);
const mockSetActiveServer = jest.fn();
const mockUpdateServer = jest.fn();

jest.mock('@/stores/servers', () => ({
  findOrCreateServerByUrl: mockFindOrCreateServerByUrl,
  getActiveServer: mockGetActiveServer,
  setActiveServer: mockSetActiveServer,
  updateServer: mockUpdateServer,
}));

import { processConnectionFromHash, resolveCloudExchangeBaseUrl } from '../connectionConfig';

describe('resolveCloudExchangeBaseUrl', () => {
  it('routes the managed app default through fleet for auth-code exchange', () => {
    expect(resolveCloudExchangeBaseUrl('https://gptme.ai')).toBe('https://fleet.gptme.ai');
    expect(resolveCloudExchangeBaseUrl('https://gptme.ai/')).toBe('https://fleet.gptme.ai');
  });

  it('keeps custom single-origin deployments on their own origin by default', () => {
    expect(resolveCloudExchangeBaseUrl('https://cloud.example.com')).toBe(
      'https://cloud.example.com'
    );
  });

  it('prefers an explicit fleet override when provided', () => {
    expect(
      resolveCloudExchangeBaseUrl('https://cloud.example.com', 'https://fleet.example.com/')
    ).toBe('https://fleet.example.com');
  });
});

describe('processConnectionFromHash', () => {
  const originalFetch = global.fetch;
  const mockFetch = jest.fn();

  beforeEach(() => {
    mockFindOrCreateServerByUrl.mockClear();
    mockGetActiveServer.mockClear();
    mockSetActiveServer.mockClear();
    mockUpdateServer.mockClear();

    mockFetch.mockReset();
    mockFetch.mockImplementation(
      async () =>
        ({
          ok: true,
          json: async () => ({
            userToken: 'token-123',
            instanceUrl: 'https://instance-123.fleet.gptme.ai',
            instanceId: 'instance-123',
          }),
        }) as Response
    );
    global.fetch = mockFetch as unknown as typeof fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('posts auth-code exchange to fleet.gptme.ai by default', async () => {
    const result = await processConnectionFromHash('code=deadBEEF42');

    expect(global.fetch).toHaveBeenCalledWith(
      'https://fleet.gptme.ai/api/v1/operator/auth/exchange',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: 'deadBEEF42' }),
      })
    );
    expect(mockFindOrCreateServerByUrl).toHaveBeenCalledWith(
      'https://instance-123.fleet.gptme.ai',
      {
        authToken: 'token-123',
        useAuthToken: true,
      }
    );
    expect(mockSetActiveServer).toHaveBeenCalledWith('server-1');
    expect(result).toEqual({
      baseUrl: 'https://instance-123.fleet.gptme.ai',
      authToken: 'token-123',
      useAuthToken: true,
    });
  });

  it('rejects with error when exchange fails (non-2xx response)', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 405,
      text: async () => '',
    } as Response);

    await expect(processConnectionFromHash('code=expired-token')).rejects.toThrow(
      'Auth code exchange failed: HTTP 405'
    );

    // Registry should not be mutated on failed exchange
    expect(mockFindOrCreateServerByUrl).not.toHaveBeenCalled();
    expect(mockSetActiveServer).not.toHaveBeenCalled();
  });
});
