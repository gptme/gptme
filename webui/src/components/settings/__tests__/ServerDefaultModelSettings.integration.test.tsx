import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { ApiProvider, useApi } from '@/contexts/ApiContext';
import { ApiClient } from '@/utils/api';
import { serverRegistry$, setActiveServer, updateServer } from '@/stores/servers';
import { getPrimaryClient } from '@/stores/serverClients';
import { ServerDefaultModelSettings } from '../ServerDefaultModelSettings';

jest.mock('@/hooks/useTauriServerStatus', () => ({
  useTauriServerStatus: () => ({
    isLoading: false,
    managesLocalServer: false,
    serverStatus: null,
  }),
}));
jest.mock('@/utils/tauri', () => ({ isTauriEnvironment: () => false }));
jest.mock('@/components/ModelPicker', () => ({
  ModelPickerButton: ({
    value,
    onSelect,
    disabled,
  }: {
    value: string;
    onSelect: (model: string) => void;
    disabled: boolean;
  }) => (
    <button disabled={disabled} onClick={() => onSelect('synthetic/draft-A')}>
      Pick: {value}
    </button>
  ),
}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const mockFetch = jest.fn();
const settings = (model: string, provider: string) => ({
  default_model: model,
  providers_configured: [provider],
});
function response(data: unknown): Response {
  return { ok: true, status: 200, json: async () => data } as Response;
}
function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

// This selector uses the real provider, registry, client pool and connection
// observable. Only transport and the visual model picker are substituted.
function Harness() {
  const { switchServer, updateConfig, api } = useApi();
  return (
    <>
      <button onClick={() => void switchServer('b')}>Switch B</button>
      <button onClick={() => updateConfig({ authToken: 'token-B', useAuthToken: true })}>
        Change account
      </button>
      <button onClick={() => api.setConnected(false)}>Disconnect</button>
      <ServerDefaultModelSettings />
    </>
  );
}

Object.defineProperty(crypto, 'randomUUID', {
  configurable: true,
  value: () => '00000000-0000-4000-8000-000000000001',
});

beforeEach(() => {
  localStorage.clear();
  serverRegistry$.set({
    activeServerId: 'a',
    connectedServerIds: ['a', 'b'],
    servers: [
      {
        id: 'a',
        name: 'A',
        baseUrl: 'https://a.synthetic.invalid',
        authToken: 'token-A',
        useAuthToken: true,
        createdAt: 0,
        lastUsedAt: 0,
      },
      {
        id: 'b',
        name: 'B',
        baseUrl: 'https://b.synthetic.invalid',
        authToken: null,
        useAuthToken: false,
        createdAt: 0,
        lastUsedAt: 0,
      },
    ],
  });
  jest.spyOn(ApiClient.prototype, 'checkConnection').mockImplementation(async function (
    this: ApiClient
  ) {
    this.setConnected(true);
    return true;
  });
  mockFetch.mockReset();
  Object.defineProperty(window, 'fetch', { writable: true, value: mockFetch });
});
afterEach(() => jest.restoreAllMocks());

it.each(['server', 'account', 'disconnect'])(
  'isolates drafts and settings through real provider %s transitions',
  async (transition) => {
    const pending = deferred();
    mockFetch.mockImplementation((url: string, options?: RequestInit) => {
      if (url.endsWith('/models'))
        return Promise.resolve(
          response({
            models: [{ id: 'synthetic/default-A' }, { id: 'synthetic/draft-A' }],
            recommended: [],
          })
        );
      if (url.endsWith('/user/settings')) {
        if (
          url.startsWith('https://a.') &&
          options?.headers &&
          (options.headers as Record<string, string>).Authorization === 'Bearer token-A'
        ) {
          return Promise.resolve(response(settings('synthetic/default-A', 'provider-A')));
        }
        return pending.promise;
      }
      return Promise.resolve(response({ restart_required: false }));
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { unmount } = render(
      <ApiProvider queryClient={queryClient}>
        <Harness />
      </ApiProvider>
    );
    await waitFor(() => expect(screen.getByText('provider-A')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Pick: synthetic/default-A' }));
    expect(screen.getByRole('button', { name: 'Pick: synthetic/draft-A' })).toBeEnabled();

    if (transition === 'server') fireEvent.click(screen.getByRole('button', { name: 'Switch B' }));
    if (transition === 'account')
      fireEvent.click(screen.getByRole('button', { name: 'Change account' }));
    if (transition === 'disconnect')
      fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(screen.queryByText('provider-A')).not.toBeInTheDocument());
    expect(
      screen.queryByRole('button', { name: 'Pick: synthetic/draft-A' })
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save default model/i })).toBeDisabled();
    expect(mockFetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(
      0
    );

    if (transition !== 'disconnect') {
      await act(async () =>
        pending.resolve(response(settings('synthetic/default-B', 'provider-B')))
      );
      await waitFor(() => expect(screen.getByText('provider-B')).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: /save default model/i }));
      await waitFor(() =>
        expect(mockFetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(true)
      );
      const post = mockFetch.mock.calls.find(([, options]) => options?.method === 'POST');
      expect(JSON.parse(post?.[1].body)).toEqual({ model: 'synthetic/default-B' });
      expect(post?.[0]).toBe(`${getPrimaryClient().baseUrl}/api/v2/user/default-model`);
    }
    unmount();
    queryClient.clear();
    // Restore a predictable active entry for the next case.
    act(() => {
      setActiveServer('a');
      updateServer('a', { authToken: 'token-A' });
    });
  }
);
