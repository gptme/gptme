import { observable } from '@legendapp/state';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { useModels } from '../useModels';

const mockConnected$ = observable(true);
const mockApi = { baseUrl: 'http://models.test', authHeader: 'Bearer test' };
const mockFetch = jest.fn();

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({ api: mockApi, isConnected$: mockConnected$ }),
}));
jest.mock('@/utils/connectionConfig', () => ({ isDemoMode: () => false }));

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, client };
}

beforeEach(() => {
  mockApi.baseUrl = 'http://models.test';
  mockApi.authHeader = 'Bearer test';
  mockConnected$.set(true);
  mockFetch.mockReset();
  mockFetch.mockImplementation(async (_url: string, init?: RequestInit) => ({
    ok: true,
    json: async () =>
      init?.method === 'POST'
        ? JSON.parse(init.body as string)
        : { models: [], default: 'initial/model', favorites: [], recommended: [] },
  }));
  global.fetch = mockFetch;
});

it('shares favorites between mounted consumers without overwriting earlier saves', async () => {
  const { wrapper } = setup();
  const composer = renderHook(() => useModels(), { wrapper });
  const settings = renderHook(() => useModels(), { wrapper });
  await waitFor(() => expect(composer.result.current.isLoading).toBe(false));
  await waitFor(() => expect(settings.result.current.isLoading).toBe(false));

  await act(async () => {
    await composer.result.current.toggleFavorite('model/A');
  });
  await waitFor(() => expect(settings.result.current.favorites).toEqual(['model/A']));
  await act(async () => {
    await settings.result.current.toggleFavorite('model/B');
  });
  await waitFor(() => expect(composer.result.current.favorites).toEqual(['model/A', 'model/B']));
  expect(mockFetch).toHaveBeenLastCalledWith(
    'http://models.test/api/v2/user/favorites',
    expect.objectContaining({ body: JSON.stringify({ favorites: ['model/A', 'model/B'] }) })
  );
});

it('updates the composer default after saving from another mounted consumer', async () => {
  const { wrapper } = setup();
  const composer = renderHook(() => useModels(), { wrapper });
  const settings = renderHook(() => useModels(), { wrapper });
  await waitFor(() => expect(composer.result.current.defaultModel).toBe('initial/model'));
  await act(async () => {
    await settings.result.current.saveDefaultModel('new/model');
  });
  await waitFor(() => expect(composer.result.current.defaultModel).toBe('new/model'));
});

it('serializes rapid toggles from separate consumers', async () => {
  const { wrapper } = setup();
  const composer = renderHook(() => useModels(), { wrapper });
  const settings = renderHook(() => useModels(), { wrapper });
  await waitFor(() => expect(composer.result.current.isLoading).toBe(false));
  let finishFirst!: (value: unknown) => void;
  const firstSave = new Promise((resolve) => {
    finishFirst = resolve;
  });
  mockFetch.mockReturnValueOnce(firstSave);
  let first!: Promise<boolean>;
  let second!: Promise<boolean>;
  act(() => {
    first = composer.result.current.toggleFavorite('model/A');
    second = settings.result.current.toggleFavorite('model/B');
  });
  await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
  await act(async () => {
    finishFirst({ ok: true, json: async () => ({ favorites: ['model/A'] }) });
    await Promise.all([first, second]);
  });
  await waitFor(() => expect(settings.result.current.favorites).toEqual(['model/A', 'model/B']));
  expect(mockFetch).toHaveBeenLastCalledWith(
    'http://models.test/api/v2/user/favorites',
    expect.objectContaining({ body: JSON.stringify({ favorites: ['model/A', 'model/B'] }) })
  );
});

it('rolls back failed favorites for every consumer', async () => {
  const { wrapper } = setup();
  const composer = renderHook(() => useModels(), { wrapper });
  const settings = renderHook(() => useModels(), { wrapper });
  await waitFor(() => expect(composer.result.current.isLoading).toBe(false));
  const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
  mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });
  await act(async () => {
    expect(await composer.result.current.toggleFavorite('model/A')).toBe(false);
  });
  await waitFor(() => expect(settings.result.current.favorites).toEqual([]));
  consoleError.mockRestore();
});

it.each(['baseUrl', 'authHeader'] as const)('isolates cached settings by %s', async (field) => {
  const { wrapper } = setup();
  const composer = renderHook(() => useModels(), { wrapper });
  await waitFor(() => expect(composer.result.current.isLoading).toBe(false));
  await act(async () => {
    await composer.result.current.toggleFavorite('model/A');
  });
  await waitFor(() => expect(composer.result.current.favorites).toEqual(['model/A']));
  mockApi[field] = field === 'baseUrl' ? 'http://other.test' : 'Bearer other-user';
  composer.rerender();
  await waitFor(() => expect(composer.result.current.isLoading).toBe(false));
  expect(composer.result.current.favorites).toEqual([]);
});

it('refreshes every consumer after Settings changes the server default directly', async () => {
  const { wrapper } = setup();
  const composer = renderHook(() => useModels(), { wrapper });
  const settings = renderHook(() => useModels(), { wrapper });
  await waitFor(() => expect(composer.result.current.defaultModel).toBe('initial/model'));
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      models: [],
      default: 'settings/model',
      favorites: ['model/A'],
      recommended: [],
    }),
  });
  await act(async () => {
    await settings.result.current.refetch();
  });
  await waitFor(() => expect(composer.result.current.defaultModel).toBe('settings/model'));
  expect(composer.result.current.favorites).toEqual(['model/A']);
});

it('refreshes preferences when returning to an already-connected server before toggling', async () => {
  const { wrapper } = setup();
  const composer = renderHook(() => useModels(), { wrapper });
  await waitFor(() => expect(composer.result.current.defaultModel).toBe('initial/model'));
  mockApi.baseUrl = 'http://other.test';
  composer.rerender();
  await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      models: [],
      default: 'external/model',
      favorites: ['model/A'],
      recommended: [],
    }),
  });
  mockApi.baseUrl = 'http://models.test';
  composer.rerender();
  await waitFor(() => expect(composer.result.current.defaultModel).toBe('external/model'));
  await act(async () => {
    await composer.result.current.toggleFavorite('model/B');
  });
  expect(mockFetch).toHaveBeenCalledWith(
    'http://models.test/api/v2/user/favorites',
    expect.objectContaining({ body: JSON.stringify({ favorites: ['model/A', 'model/B'] }) })
  );
});

it('does not let a catalog fetch started during a default save restore the old default', async () => {
  const { wrapper } = setup();
  const composer = renderHook(() => useModels(), { wrapper });
  await waitFor(() => expect(composer.result.current.defaultModel).toBe('initial/model'));
  let finishSave!: (value: unknown) => void;
  let finishRead!: (value: unknown) => void;
  mockFetch.mockReturnValueOnce(
    new Promise((resolve) => {
      finishSave = resolve;
    })
  );
  let save!: ReturnType<typeof composer.result.current.saveDefaultModel>;
  act(() => {
    save = composer.result.current.saveDefaultModel('new/model');
  });
  await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
  mockFetch.mockReturnValueOnce(
    new Promise((resolve) => {
      finishRead = resolve;
    })
  );
  let read!: ReturnType<typeof composer.result.current.refetch>;
  act(() => {
    read = composer.result.current.refetch();
  });
  await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(3));
  const signal = mockFetch.mock.calls[2][1].signal as AbortSignal;
  await act(async () => {
    finishSave({ ok: true, json: async () => ({ restart_required: false }) });
    await save;
    finishRead({
      ok: true,
      json: async () => ({ models: [], default: 'initial/model', favorites: [], recommended: [] }),
    });
    await read;
  });
  await waitFor(() => expect(composer.result.current.defaultModel).toBe('new/model'));
  expect(signal.aborted).toBe(true);
});

it('waits for a Settings refresh before replacing favorites', async () => {
  const { wrapper } = setup();
  const composer = renderHook(() => useModels(), { wrapper });
  await waitFor(() => expect(composer.result.current.defaultModel).toBe('initial/model'));
  let finishRead!: (value: unknown) => void;
  mockFetch.mockReturnValueOnce(
    new Promise((resolve) => {
      finishRead = resolve;
    })
  );
  let read!: ReturnType<typeof composer.result.current.refetch>;
  act(() => {
    read = composer.result.current.refetch();
  });
  await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
  let save!: Promise<boolean>;
  act(() => {
    save = composer.result.current.toggleFavorite('model/B');
  });
  await act(async () => {
    finishRead({
      ok: true,
      json: async () => ({
        models: [],
        default: 'settings/model',
        favorites: ['model/A'],
        recommended: [],
      }),
    });
    await read;
    await save;
  });
  await waitFor(() => expect(composer.result.current.defaultModel).toBe('settings/model'));
  expect(mockFetch).toHaveBeenCalledWith(
    'http://models.test/api/v2/user/favorites',
    expect.objectContaining({ body: JSON.stringify({ favorites: ['model/A', 'model/B'] }) })
  );
});

it('preserves a saved favorite when an older catalog read completes after the POST', async () => {
  const { wrapper } = setup();
  const composer = renderHook(() => useModels(), { wrapper });
  await waitFor(() => expect(composer.result.current.defaultModel).toBe('initial/model'));
  let finishSave!: (value: unknown) => void;
  let finishRead!: (value: unknown) => void;
  mockFetch.mockReturnValueOnce(
    new Promise((resolve) => {
      finishSave = resolve;
    })
  );
  let save!: Promise<boolean>;
  act(() => {
    save = composer.result.current.toggleFavorite('model/A');
  });
  await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
  mockFetch.mockReturnValueOnce(
    new Promise((resolve) => {
      finishRead = resolve;
    })
  );
  let read!: ReturnType<typeof composer.result.current.refetch>;
  act(() => {
    read = composer.result.current.refetch();
  });
  await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(3));
  await act(async () => {
    finishSave({ ok: true, json: async () => ({ favorites: ['model/A'] }) });
    // Let the POST response publish before the older catalog read arrives.
    await new Promise((resolve) => setTimeout(resolve, 0));
    finishRead({
      ok: true,
      json: async () => ({ models: [], default: 'initial/model', favorites: [], recommended: [] }),
    });
    await Promise.all([save, read]);
  });
  await waitFor(() => expect(composer.result.current.favorites).toEqual(['model/A']));
  await act(async () => {
    await composer.result.current.toggleFavorite('model/B');
  });
  expect(mockFetch).toHaveBeenLastCalledWith(
    'http://models.test/api/v2/user/favorites',
    expect.objectContaining({ body: JSON.stringify({ favorites: ['model/A', 'model/B'] }) })
  );
});
