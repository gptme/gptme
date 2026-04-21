import '@testing-library/jest-dom';
import { render, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { QueryClient } from '@tanstack/react-query';
import { ApiProvider } from '../ApiContext';

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

const mockClient = {
  isConnected$,
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
    mockGetConnectionConfigFromSources.mockImplementation(() => ({
      baseUrl: getActiveServerBaseUrl(),
      authToken: null,
      useAuthToken: false,
    }));
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
});
