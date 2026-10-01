// ApiContext-level regression coverage for the concurrent-probe fix in
// webui/src/utils/api.ts (see PR #4041). The client-level suite in
// utils/__tests__/api.test.ts pins the ApiClient contract; this file exercises
// the real client *through* ApiContext.connect() so the side effects the
// reported production regression touched — setConnected, query invalidation,
// and the success/error toasts — are covered end to end.
jest.mock('@/utils/connectionConfig', () => ({
  getConnectionConfigFromSources: jest.fn(() => ({
    baseUrl: 'http://127.0.0.1:5700',
    authToken: null,
    useAuthToken: false,
  })),
  processConnectionFromHash: jest.fn(),
}));

jest.mock('@/stores/conversations', () => ({
  initConversation: jest.fn(),
  setGenerating: jest.fn(),
  setMaxTokens: jest.fn(),
  setTemperature: jest.fn(),
  setTopP: jest.fn(),
}));

jest.mock('@/stores/servers', () => ({
  serverRegistry$: jest.requireActual('@legendapp/state').observable({
    activeServerId: 'server-1',
    connectedServerIds: [],
    servers: [
      {
        id: 'server-1',
        name: 'Local',
        baseUrl: 'http://127.0.0.1:5700',
        authToken: null,
        useAuthToken: false,
        createdAt: 0,
        lastUsedAt: 0,
      },
    ],
  }),
  getActiveServer: () => null,
  updateServer: jest.fn(),
  setActiveServer: jest.fn(),
  connectServer: jest.fn(),
}));

const mockGetClientForServer = jest.fn();
const mockGetClientForServerConfig = jest.fn();
const mockGetPrimaryClient = jest.fn();
jest.mock('@/stores/serverClients', () => ({
  getClientForServer: (...args: unknown[]) => mockGetClientForServer(...args),
  getClientForServerConfig: (...args: unknown[]) => mockGetClientForServerConfig(...args),
  getPrimaryClient: () => mockGetPrimaryClient(),
}));

jest.mock('@/hooks/useTauriServerStatus', () => ({
  useTauriServerStatus: () => ({
    isLoading: false,
    managesLocalServer: false,
    serverStatus: {
      running: false,
      port: 5700,
      port_available: false,
      manages_local_server: false,
    },
  }),
}));

// Tauri + default-loopback + managesLocalServer=false means the provider skips
// its initial auto-connect, so the only probe in flight is the explicit one.
jest.mock('@/utils/tauri', () => ({
  isTauriEnvironment: () => true,
}));

jest.mock('@legendapp/state/react', () => ({
  use$: (obs: { get: () => unknown }) => obs.get(),
}));

const mockToastSuccess = jest.fn();
const mockToastError = jest.fn();
jest.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mockToastSuccess(...args),
    error: (...args: unknown[]) => mockToastError(...args),
  },
}));

import '@testing-library/jest-dom';
import { render, waitFor } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { ApiProvider, useApi } from '../ApiContext';
import { ApiClient, CLIENT_API_VERSION, CLIENT_MIN_CONTRACT_REVISION } from '@/utils/api';

function successResponse(): Response {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      api_version: CLIENT_API_VERSION,
      contract_revision: CLIENT_MIN_CONTRACT_REVISION,
    }),
  } as Response;
}

function deferred(): { promise: Promise<Response>; resolve: (r: Response) => void } {
  let resolve!: (r: Response) => void;
  const promise = new Promise<Response>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe('ApiContext.connect concurrent probe handling', () => {
  const originalCrypto = global.crypto;

  beforeAll(() => {
    // jsdom in this Jest config lacks crypto.randomUUID, which ApiClient's
    // constructor calls.
    Object.defineProperty(global, 'crypto', {
      value: {
        ...originalCrypto,
        randomUUID: jest.fn(() => 'test-client-id'),
      },
      configurable: true,
    });
  });

  afterAll(() => {
    Object.defineProperty(global, 'crypto', { value: originalCrypto, configurable: true });
  });

  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    jest.clearAllMocks();
  });

  it('keeps the client connected when a newer probe supersedes the explicit connect', async () => {
    // Root probe started by the explicit connect (older).
    const olderRoot = deferred();
    // Root + auth probes started by the superseding check (newer), which
    // mirrors what auto-connect does via client.checkConnection().
    const newerRoot = deferred();
    const newerAuth = deferred();
    const fetchMock = jest
      .fn()
      .mockReturnValueOnce(olderRoot.promise)
      .mockReturnValueOnce(newerRoot.promise)
      .mockReturnValueOnce(newerAuth.promise);
    global.fetch = fetchMock;

    // A real ApiClient, so the superseded-probe contract is genuinely exercised
    // rather than stubbed at the client boundary.
    const client = new ApiClient('https://instance.example.com');
    mockGetPrimaryClient.mockReturnValue(client);
    mockGetClientForServer.mockReturnValue(client);
    mockGetClientForServerConfig.mockReturnValue(client);

    const queryClient = new QueryClient();
    const invalidateSpy = jest.spyOn(queryClient, 'invalidateQueries');

    let connectFromProbe!: (config: {
      baseUrl: string;
      authToken: null;
      useAuthToken: false;
    }) => Promise<void>;
    function ConnectProbe() {
      connectFromProbe = useApi().connect;
      return null;
    }
    render(
      <ApiProvider queryClient={queryClient}>
        <ConnectProbe />
      </ApiProvider>
    );

    const explicitConnect = connectFromProbe({
      baseUrl: 'https://instance.example.com',
      authToken: null,
      useAuthToken: false,
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // Newer probe starts while the explicit connect's probe is still in flight.
    const newerProbe = client.checkConnection();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    // Newer probe wins first and connects the client.
    newerRoot.resolve(successResponse());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    newerAuth.resolve(successResponse());
    await expect(newerProbe).resolves.toBe(true);

    // The older probe's response lands last. It must adopt the winning result
    // instead of reporting a failure that would disconnect the client.
    olderRoot.resolve(successResponse());
    await expect(explicitConnect).resolves.toBeUndefined();

    expect(client.isConnected$.get()).toBe(true);
    expect(client.lastConnectionResult$.get()).toMatchObject({ ok: true });
    expect(mockToastError).not.toHaveBeenCalled();
    expect(mockToastSuccess).toHaveBeenCalled();
    expect(invalidateSpy).toHaveBeenCalled();
  });
});
