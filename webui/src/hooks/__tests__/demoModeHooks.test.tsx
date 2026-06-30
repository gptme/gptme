import { act, renderHook, waitFor } from '@testing-library/react';
import { observable } from '@legendapp/state';
import { useProviderHealth } from '../useProviderHealth';
import { useUserSettings } from '../useUserSettings';
import { providerHealth$ } from '@/stores/providerHealth';

const mockFetch = jest.fn();
const mockIsDemoMode = jest.fn();
// Created at module scope so it can be referenced inside the jest.mock() factory
// (jest.mock() is hoisted before imports, but factory closures evaluate lazily).
const isConnected$ = observable(false);

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {
      baseUrl: 'demo://offline',
      authHeader: null,
    },
    isConnected$,
  }),
}));

jest.mock('@/utils/connectionConfig', () => ({
  isDemoMode: () => mockIsDemoMode(),
}));

describe('demo-mode hooks', () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockIsDemoMode.mockReturnValue(true);
    providerHealth$.set({
      data: null,
      isLoading: false,
      error: null,
    });
    Object.defineProperty(window, 'fetch', {
      writable: true,
      value: mockFetch,
    });
  });

  it('useProviderHealth returns an empty stub without fetching in demo mode', async () => {
    const { result } = renderHook(() => useProviderHealth(true));

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.data).toEqual({ providers: {} });
    expect(result.current.error).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();

    await act(async () => {
      await result.current.refresh(true);
    });

    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('useUserSettings returns demo-safe defaults without fetching in demo mode', async () => {
    const { result } = renderHook(() => useUserSettings());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.settings).toEqual({
      providers_configured: [],
      default_model: null,
    });
    expect(result.current.error).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
