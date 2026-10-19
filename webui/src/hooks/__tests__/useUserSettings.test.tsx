/**
 * Mounted regressions for useUserSettings identity scoping.
 *
 * A settings snapshot (settings/error/loading) belongs to one API identity:
 * the server URL, the auth credentials and the connected state together. When
 * any of those change — a different server, a different account, or a drop to
 * disconnected — the previous identity's snapshot must not leak into the new
 * render, even if the transport ignored the abort and a late success/failure
 * lands afterwards. A refetch for the *same* identity, by contrast, must keep
 * showing the current snapshot while it reloads.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { useUserSettings, type UserSettings } from '../useUserSettings';

const BASE_A = 'https://server-a.synthetic.invalid';
const BASE_B = 'https://server-b.synthetic.invalid';

const mockFetch = jest.fn();
const mockIsDemoMode = jest.fn(() => false);
// Created at module scope so the jest.mock() factory (hoisted before imports)
// can close over it; the factory body evaluates lazily.
const isConnected$ = observable(false);
const mockApi = { baseUrl: BASE_A, authHeader: null as string | null };

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({ api: mockApi, isConnected$ }),
}));

jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: () => mockIsDemoMode(),
}));

const settingsA: UserSettings = {
  providers_configured: ['anthropic'],
  default_model: 'claude-sonnet-4-6',
};
const settingsB: UserSettings = {
  providers_configured: ['openai'],
  default_model: 'gpt-5',
};

const okResponse = (data: UserSettings) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  json: async () => data,
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const connected = () => act(() => isConnected$.set(true));

describe('useUserSettings identity scoping', () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockIsDemoMode.mockReturnValue(false);
    mockApi.baseUrl = BASE_A;
    mockApi.authHeader = null;
    act(() => isConnected$.set(false));
    Object.defineProperty(window, 'fetch', {
      writable: true,
      value: mockFetch,
    });
  });

  afterEach(() => {
    act(() => isConnected$.set(false));
  });

  it('does not expose the previous server snapshot while the new server request is pending', async () => {
    connected();
    mockFetch.mockResolvedValueOnce(okResponse(settingsA));

    const { result, rerender } = renderHook(() => useUserSettings());
    await waitFor(() => expect(result.current.settings).toEqual(settingsA));

    const bobFetch = deferred<unknown>();
    mockFetch.mockReturnValueOnce(bobFetch.promise);
    mockApi.baseUrl = BASE_B;
    rerender();

    // Hidden synchronously on the identity change; the new load is pending.
    expect(result.current.settings).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.isLoading).toBe(true);

    await act(async () => {
      bobFetch.resolve(okResponse(settingsB));
    });
    await waitFor(() => expect(result.current.settings).toEqual(settingsB));
    expect(result.current.isLoading).toBe(false);
  });

  it('ignores a late success from the previous server identity', async () => {
    connected();
    const aliceFetch = deferred<unknown>();
    mockFetch.mockReturnValueOnce(aliceFetch.promise);

    const { result, rerender } = renderHook(() => useUserSettings());
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));

    const bobFetch = deferred<unknown>();
    mockFetch.mockReturnValueOnce(bobFetch.promise);
    mockApi.baseUrl = BASE_B;
    rerender();
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));

    // Alice's response ignores the abort and lands after Bob took over.
    await act(async () => {
      aliceFetch.resolve(okResponse(settingsA));
    });
    expect(result.current.settings).toBeNull();

    await act(async () => {
      bobFetch.resolve(okResponse(settingsB));
    });
    await waitFor(() => expect(result.current.settings).toEqual(settingsB));
  });

  it('ignores a late failure from the previous server identity', async () => {
    connected();
    const aliceFetch = deferred<unknown>();
    mockFetch.mockReturnValueOnce(aliceFetch.promise);

    const { result, rerender } = renderHook(() => useUserSettings());
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));

    const bobFetch = deferred<unknown>();
    mockFetch.mockReturnValueOnce(bobFetch.promise);
    mockApi.baseUrl = BASE_B;
    rerender();
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));

    await act(async () => {
      aliceFetch.reject(new Error('stale failure from server A'));
    });
    expect(result.current.error).toBeNull();

    await act(async () => {
      bobFetch.resolve(okResponse(settingsB));
    });
    await waitFor(() => expect(result.current.settings).toEqual(settingsB));
  });

  it('scopes by credentials, not just the server URL', async () => {
    connected();
    mockApi.authHeader = 'Bearer alice';
    mockFetch.mockResolvedValueOnce(okResponse(settingsA));

    const { result, rerender } = renderHook(() => useUserSettings());
    await waitFor(() => expect(result.current.settings).toEqual(settingsA));

    const bobFetch = deferred<unknown>();
    mockFetch.mockReturnValueOnce(bobFetch.promise);
    mockApi.authHeader = 'Bearer bob';
    rerender();

    // Same server, different account: Alice's snapshot must not appear.
    expect(result.current.settings).toBeNull();
    expect(result.current.isLoading).toBe(true);

    await act(async () => {
      bobFetch.resolve(okResponse(settingsB));
    });
    await waitFor(() => expect(result.current.settings).toEqual(settingsB));
  });

  it('clears the snapshot on disconnect and ignores a late completion', async () => {
    connected();
    mockFetch.mockResolvedValueOnce(okResponse(settingsA));

    const { result } = renderHook(() => useUserSettings());
    await waitFor(() => expect(result.current.settings).toEqual(settingsA));

    // Start a same-identity refetch and leave it in flight.
    const pendingFetch = deferred<unknown>();
    mockFetch.mockReturnValueOnce(pendingFetch.promise);
    act(() => {
      result.current.refetch();
    });
    expect(result.current.settings).toEqual(settingsA);
    expect(result.current.isLoading).toBe(true);

    // Then the server disconnects while that refetch is still pending.
    act(() => isConnected$.set(false));
    expect(result.current.settings).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.isLoading).toBe(false);

    await act(async () => {
      pendingFetch.resolve(okResponse(settingsB));
    });
    expect(result.current.settings).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });

  it('keeps the current snapshot visible across a same-owner refetch', async () => {
    connected();
    mockFetch.mockResolvedValueOnce(okResponse(settingsA));

    const { result } = renderHook(() => useUserSettings());
    await waitFor(() => expect(result.current.settings).toEqual(settingsA));

    const updated: UserSettings = {
      providers_configured: ['anthropic', 'openai'],
      default_model: 'gpt-5',
    };
    const pendingFetch = deferred<unknown>();
    mockFetch.mockReturnValueOnce(pendingFetch.promise);
    act(() => {
      result.current.refetch();
    });

    // Same identity => previous snapshot stays while the reload runs.
    expect(result.current.settings).toEqual(settingsA);
    expect(result.current.isLoading).toBe(true);
    expect(result.current.error).toBeNull();

    await act(async () => {
      pendingFetch.resolve(okResponse(updated));
    });
    await waitFor(() => expect(result.current.settings).toEqual(updated));
    expect(result.current.isLoading).toBe(false);
  });

  it('keeps the demo snapshot even while disconnected', async () => {
    mockIsDemoMode.mockReturnValue(true);

    const { result } = renderHook(() => useUserSettings());

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.settings).toEqual({ providers_configured: [], default_model: null });
    expect(result.current.error).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
