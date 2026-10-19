import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { serverRegistry$ } from '@/stores/servers';
import { DEFAULT_SERVER_CONFIG } from '@/types/servers';
import { getClientForServer } from '@/stores/serverClients';
import { useSecondaryServerConversations } from '../useMultiServerConversations';

jest.mock('@/stores/serverClients', () => ({
  getClientForServer: jest.fn(),
}));

const mockGetClientForServer = getClientForServer as jest.MockedFunction<typeof getClientForServer>;

/** A pooled client that, like the real ApiClient, refuses to list until probed. */
function makeClient(reachable: boolean) {
  let connected = false;
  return {
    isConnected$: { get: () => connected },
    checkConnection: jest.fn(async () => {
      connected = reachable;
      return reachable;
    }),
    getConversationsPaginated: jest.fn(async () => {
      if (!connected) throw new Error('Not connected to API');
      return { conversations: [{ id: 'shared-id', name: 'Secondary chat' }], next_cursor: null };
    }),
  };
}

function wrapperFor(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

function persistSecondaryAsConnected() {
  // Registry as rehydrated from localStorage after a reload: the secondary is
  // still listed as connected, but its pooled client has never been probed.
  serverRegistry$.set({
    servers: [
      { ...DEFAULT_SERVER_CONFIG, id: 'primary', createdAt: 0, lastUsedAt: 0 },
      {
        ...DEFAULT_SERVER_CONFIG,
        id: 'secondary',
        name: 'Secondary',
        baseUrl: 'http://127.0.0.1:5901',
        createdAt: 1,
        lastUsedAt: 1,
      },
    ],
    activeServerId: 'primary',
    connectedServerIds: ['primary', 'secondary'],
  });
}

describe('useSecondaryServerConversations', () => {
  beforeEach(() => {
    serverRegistry$.set({
      servers: [
        {
          ...DEFAULT_SERVER_CONFIG,
          id: 'primary',
          createdAt: 0,
          lastUsedAt: 0,
        },
      ],
      activeServerId: 'primary',
      connectedServerIds: ['primary'],
    });
  });

  it('keeps unchanged combined query results referentially stable', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    const { result, rerender } = renderHook(() => useSecondaryServerConversations(), {
      wrapper,
    });
    const initialConversations = result.current.secondaryConversations;

    rerender();

    expect(result.current.secondaryConversations).toBe(initialConversations);
  });

  it('probes a persisted secondary client before listing its conversations', async () => {
    persistSecondaryAsConnected();
    const client = makeClient(true);
    mockGetClientForServer.mockReturnValue(client as never);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useSecondaryServerConversations(), {
      wrapper: wrapperFor(queryClient),
    });

    await waitFor(() => expect(result.current.secondaryConversations).toHaveLength(1));
    expect(client.checkConnection).toHaveBeenCalledTimes(1);
    expect(result.current.secondaryConversations[0]).toMatchObject({
      id: 'shared-id',
      serverId: 'secondary',
      serverName: 'Secondary',
    });
  });

  it('lists nothing when the connection probe throws', async () => {
    persistSecondaryAsConnected();
    const client = makeClient(false);
    client.checkConnection.mockRejectedValueOnce(new Error('network down'));
    mockGetClientForServer.mockReturnValue(client as never);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useSecondaryServerConversations(), {
      wrapper: wrapperFor(queryClient),
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(client.getConversationsPaginated).not.toHaveBeenCalled();
    expect(result.current.secondaryConversations).toEqual([]);
  });

  it('lists nothing for an unreachable persisted secondary without fetching', async () => {
    persistSecondaryAsConnected();
    const client = makeClient(false);
    mockGetClientForServer.mockReturnValue(client as never);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useSecondaryServerConversations(), {
      wrapper: wrapperFor(queryClient),
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(client.checkConnection).toHaveBeenCalledTimes(1);
    expect(client.getConversationsPaginated).not.toHaveBeenCalled();
    expect(result.current.secondaryConversations).toEqual([]);
  });
});
