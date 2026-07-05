import { jest } from '@jest/globals';

const mockFindOrCreateServerByUrl = jest.fn((baseUrl: string, config: Record<string, unknown>) => ({
  id: 'server-1',
  baseUrl,
  authToken: config.authToken,
  useAuthToken: config.useAuthToken,
}));
const mockGetActiveServer = jest.fn(() => null);
const mockSetActiveServer = jest.fn();
const mockConnectServer = jest.fn();
const mockUpdateServer = jest.fn();

jest.mock('@/stores/servers', () => ({
  findOrCreateServerByUrl: mockFindOrCreateServerByUrl,
  connectServer: mockConnectServer,
  getActiveServer: mockGetActiveServer,
  setActiveServer: mockSetActiveServer,
  updateServer: mockUpdateServer,
}));

import { processConnectionFromHash, resolveCloudExchangeBaseUrl } from '../connectionConfig';

const runtimeEnvWindow = window as typeof window & {
  __GPTME_WEBUI_ENV__?: Record<string, string | undefined>;
};

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
  const mockFetch = jest.fn<typeof fetch>();

  beforeEach(() => {
    mockFindOrCreateServerByUrl.mockClear();
    mockConnectServer.mockClear();
    mockGetActiveServer.mockClear();
    mockSetActiveServer.mockClear();
    mockUpdateServer.mockClear();

    mockFetch.mockReset();
    mockFetch.mockImplementation(
      async (_input, _init) =>
        ({
          ok: true,
          json: async () => ({
            userToken: 'token-123',
            instanceUrl: 'https://instance-123.fleet.gptme.ai',
            instanceId: 'instance-123',
          }),
        }) as Response
    );
    global.fetch = mockFetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete runtimeEnvWindow.__GPTME_WEBUI_ENV__;
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
    expect(mockConnectServer).toHaveBeenCalledWith('server-1');
    expect(result).toEqual({
      baseUrl: 'https://instance-123.fleet.gptme.ai',
      authToken: 'token-123',
      useAuthToken: true,
    });
  });

  it('posts auth-code exchange to explicit browser runtime fleet URL', async () => {
    runtimeEnvWindow.__GPTME_WEBUI_ENV__ = {
      VITE_GPTME_FLEET_BASE_URL: 'http://fleet.gptme.local:8080',
    };

    await processConnectionFromHash('code=local-ci-code');

    expect(global.fetch).toHaveBeenCalledWith(
      'http://fleet.gptme.local:8080/api/v1/operator/auth/exchange',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: 'local-ci-code' }),
      })
    );
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
    expect(mockConnectServer).not.toHaveBeenCalled();
    expect(mockSetActiveServer).not.toHaveBeenCalled();
  });
});
