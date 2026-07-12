import '@testing-library/jest-dom';
import { render, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { QueryClient } from '@tanstack/react-query';
import { ApiProvider, shouldSkipHostedLoopbackAutoConnect } from '../ApiContext';
import type { ConnectionProbeResult } from '@/utils/api';

const mockCheckConnection = jest.fn();
const mockSetConnected = jest.fn();
const mockGetConnectionConfigFromSources = jest.fn();
const mockProcessConnectionFromHash = jest.fn();
const mockGetClientForServer = jest.fn();
const mockGetPrimaryClient = jest.fn();
const mockGetActiveServer = jest.fn();
const mockUpdateServer = jest.fn();
const mockSetActiveServer = jest.fn();
const mockConnectServer = jest.fn();
const mockUseTauriServerStatus = jest.fn();
const mockIsTauriEnvironment = jest.fn();
const mockToastSuccess = jest.fn();
const mockToastError = jest.fn();

const isConnected$ = observable(false);
const lastConnectionResult$ = observable<ConnectionProbeResult | null>(null);

const mockClient = {
  isConnected$,
  lastConnectionResult$,
  checkConnection: (...args: unknown[]) => mockCheckConnection(...args),
  setConnected: (...args: [boolean]) => mockSetConnected(...args),
};

jest.mock('@/utils/connectionConfig', () => ({
  getConnectionConfigFromSources: (...args: unknown[]) =>
    mockGetConnectionConfigFromSources(...args),
  processConnectionFromHash: (...args: unknown[]) => mockProcessConnectionFromHash(...args),
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
  getActiveServer: () => mockGetActiveServer(),
  updateServer: (...args: unknown[]) => mockUpdateServer(...args),
  setActiveServer: (...args: unknown[]) => mockSetActiveServer(...args),
  connectServer: (...args: unknown[]) => mockConnectServer(...args),
}));

jest.mock('@/stores/serverClients', () => ({
  getClientForServer: (...args: unknown[]) => mockGetClientForServer(...args),
  getPrimaryClient: () => mockGetPrimaryClient(),
}));

jest.mock('@/hooks/useTauriServerStatus', () => ({
  useTauriServerStatus: () => mockUseTauriServerStatus(),
}));

jest.mock('@/utils/tauri', () => ({
  isTauriEnvironment: () => mockIsTauriEnvironment(),
}));

jest.mock('@legendapp/state/react', () => ({
  use$: (obs: { get: () => unknown }) => obs.get(),
}));

jest.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mockToastSuccess(...args),
    error: (...args: unknown[]) => mockToastError(...args),
  },
}));

function setActiveServerBaseUrl(baseUrl: string) {
  const { serverRegistry$ } = jest.requireMock('@/stores/servers') as {
    serverRegistry$: { set: (value: unknown) => void };
  };

  serverRegistry$.set({
    activeServerId: 'server-1',
    connectedServerIds: [],
    servers: [
      {
        id: 'server-1',
        name: 'Server',
        baseUrl,
        authToken: null,
        useAuthToken: false,
        createdAt: 0,
        lastUsedAt: 0,
      },
    ],
  });
}

function getActiveServerBaseUrl() {
  const { serverRegistry$ } = jest.requireMock('@/stores/servers') as {
    serverRegistry$: { get: () => { servers: Array<{ baseUrl: string }> } };
  };

  return serverRegistry$.get().servers[0].baseUrl;
}

function renderProvider() {
  const queryClient = new QueryClient();
  return render(
    <ApiProvider queryClient={queryClient}>
      <div>child</div>
    </ApiProvider>
  );
}

describe('ApiProvider mobile auto-connect', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    jest.clearAllMocks();
    isConnected$.set(false);
    lastConnectionResult$.set(null);
    setActiveServerBaseUrl('http://127.0.0.1:5700');

    mockSetConnected.mockImplementation((connected: boolean) => {
      isConnected$.set(connected);
    });
    mockCheckConnection.mockResolvedValue(true);
    mockGetPrimaryClient.mockReturnValue(mockClient);
    mockGetClientForServer.mockReturnValue(mockClient);
    mockGetActiveServer.mockImplementation(() => {
      const { serverRegistry$ } = jest.requireMock('@/stores/servers') as {
        serverRegistry$: { get: () => { servers: unknown[] } };
      };

      return serverRegistry$.get().servers[0] ?? null;
    });
    // Dynamic mock that reads from current registry state
    mockGetConnectionConfigFromSources.mockImplementation(() => {
      const currentUrl = getActiveServerBaseUrl();
      return {
        baseUrl: currentUrl,
        authToken: null,
        useAuthToken: false,
      };
    });
    mockProcessConnectionFromHash.mockResolvedValue({
      baseUrl: getActiveServerBaseUrl(),
      authToken: null,
      useAuthToken: false,
    });
    mockUseTauriServerStatus.mockReturnValue({
      isLoading: false,
      managesLocalServer: false,
      serverStatus: {
        running: false,
        port: 5700,
        port_available: false,
        manages_local_server: false,
      },
    });
    mockIsTauriEnvironment.mockReturnValue(true);
  });

  it('auto-connects when a mobile client already has a remote server configured', async () => {
    setActiveServerBaseUrl('https://bob.example.com');

    renderProvider();

    await waitFor(() => {
      expect(mockCheckConnection).toHaveBeenCalledTimes(1);
    });
  });

  it('skips the initial auto-connect when mobile is still pointed at the default local URL', async () => {
    renderProvider();

    await waitFor(() => {
      expect(mockGetPrimaryClient).toHaveBeenCalled();
    });

    expect(mockCheckConnection).not.toHaveBeenCalled();
  });

  it('syncs the default local URL to the Tauri-managed server port before auto-connect', async () => {
    mockUseTauriServerStatus.mockReturnValue({
      isLoading: false,
      managesLocalServer: true,
      serverStatus: {
        running: true,
        port: 5712,
        port_available: false,
        manages_local_server: true,
      },
    });

    renderProvider();

    // Verify: the sync effect triggers updateServer with the Tauri-managed port
    await waitFor(() => {
      expect(mockUpdateServer).toHaveBeenCalledWith('server-1', {
        baseUrl: 'http://127.0.0.1:5712',
      });
    });

    // Verify: auto-connect is skipped during the sync (needsTauriServerUrlSync is true)
    expect(mockCheckConnection).not.toHaveBeenCalled();

    // After the sync completes (registry updates), the next render should proceed
    // with auto-connect. This is tested in integration/e2e scenarios where the
    // full reactive chain (update → registry change → component re-render) works.
  });

  it('stops retrying after a CORS failure (permanent, not transient)', async () => {
    setActiveServerBaseUrl('https://bob.example.com');

    // Simulate Chrome's Private Network Access / CORS rejection — a hosted webapp
    // trying to reach a localhost server, or a misconfigured server CORS policy.
    // These do not recover by retrying within the session.
    mockCheckConnection.mockImplementation(async () => {
      lastConnectionResult$.set({
        ok: false,
        url: 'https://bob.example.com',
        reason: 'cors',
        message: 'CORS/PNA error',
      });
      return false;
    });

    renderProvider();

    await waitFor(() => {
      expect(mockCheckConnection).toHaveBeenCalledTimes(1);
    });

    // Wait past the would-be first retry (INITIAL_RETRY_DELAY = 1000ms)
    await new Promise((resolve) => setTimeout(resolve, 1500));

    expect(mockCheckConnection).toHaveBeenCalledTimes(1);
  });

  it('keeps retrying on transient network failures', async () => {
    setActiveServerBaseUrl('https://bob.example.com');

    // Generic network failure (server starting up, transient connectivity)
    // should still retry — the user shouldn't have to manually reconnect.
    mockCheckConnection.mockImplementation(async () => {
      lastConnectionResult$.set({
        ok: false,
        url: 'https://bob.example.com',
        reason: 'network',
        message: 'Could not reach server',
      });
      return false;
    });

    renderProvider();

    await waitFor(() => {
      expect(mockCheckConnection).toHaveBeenCalledTimes(1);
    });

    // Wait past the first retry delay (1000ms)
    await waitFor(
      () => {
        expect(mockCheckConnection).toHaveBeenCalledTimes(2);
      },
      { timeout: 2000 }
    );
  });
});

describe('shouldSkipHostedLoopbackAutoConnect', () => {
  it('skips loopback auto-connect on hosted browser origins', () => {
    expect(
      shouldSkipHostedLoopbackAutoConnect('http://127.0.0.1:5700', 'https://chat.gptme.org', false)
    ).toBe(true);
  });

  it('keeps loopback auto-connect for localhost dev origins', () => {
    expect(
      shouldSkipHostedLoopbackAutoConnect('http://127.0.0.1:5700', 'http://localhost:4173', false)
    ).toBe(false);
  });

  it('does not skip non-loopback targets', () => {
    expect(
      shouldSkipHostedLoopbackAutoConnect(
        'https://bob.example.com',
        'https://chat.gptme.org',
        false
      )
    ).toBe(false);
  });

  it('does not skip when running in Tauri (isTauri=true)', () => {
    expect(
      shouldSkipHostedLoopbackAutoConnect('http://127.0.0.1:5700', 'https://chat.gptme.org', true)
    ).toBe(false);
  });
});
