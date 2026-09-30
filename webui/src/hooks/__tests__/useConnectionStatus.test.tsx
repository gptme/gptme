import { act, renderHook } from '@testing-library/react';
import { observable } from '@legendapp/state';
import {
  CONNECTION_GRACE_MS,
  deriveConnectionStatus,
  useConnectionStatus,
} from '../useConnectionStatus';

type Probe =
  | { ok: true; url: string }
  | { ok: false; url: string; reason: 'network'; message: string };

const makeClient = () => ({
  isConnected$: observable(false),
  lastConnectionResult$: observable<Probe | null>(null),
});

const mockCtx = {
  api: makeClient(),
  isConnecting$: observable(false),
  isAutoConnecting$: observable(false),
  isExchangingAuthCode: false,
  autoConnectSkipped: false,
};

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => mockCtx,
}));

const FAILED: Probe = { ok: false, url: 'http://x/api/v2', reason: 'network', message: 'down' };

describe('deriveConnectionStatus', () => {
  const base = { isConnected: false, isAttempting: false, lastResult: null, graceExpired: false };

  it('is connected whenever the client is connected', () => {
    expect(deriveConnectionStatus({ ...base, isConnected: true, lastResult: FAILED })).toBe(
      'connected'
    );
  });

  it('is connecting while an attempt (or retry back-off) is in flight, even after a failure', () => {
    expect(deriveConnectionStatus({ ...base, isAttempting: true, lastResult: FAILED })).toBe(
      'connecting'
    );
  });

  it('is disconnected only after a probe actually failed', () => {
    expect(deriveConnectionStatus({ ...base, lastResult: FAILED })).toBe('disconnected');
  });

  it('treats an un-probed client as connecting until the grace period expires', () => {
    expect(deriveConnectionStatus(base)).toBe('connecting');
    expect(deriveConnectionStatus({ ...base, graceExpired: true })).toBe('disconnected');
  });
});

describe('useConnectionStatus', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockCtx.api = makeClient();
    mockCtx.isConnecting$.set(false);
    mockCtx.isAutoConnecting$.set(false);
    mockCtx.isExchangingAuthCode = false;
    mockCtx.autoConnectSkipped = false;
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('never reports disconnected during a normal load: un-probed → connecting → connected', () => {
    const seen: string[] = [];
    const { result } = renderHook(() => {
      const status = useConnectionStatus();
      seen.push(status);
      return status;
    });
    expect(result.current).toBe('connecting');

    act(() => {
      jest.advanceTimersByTime(CONNECTION_GRACE_MS / 2);
      mockCtx.isConnecting$.set(true);
    });
    act(() => {
      mockCtx.api.lastConnectionResult$.set({ ok: true, url: 'http://x/api/v2' });
      mockCtx.api.isConnected$.set(true);
      mockCtx.isConnecting$.set(false);
    });
    expect(result.current).toBe('connected');
    expect(seen).not.toContain('disconnected');
  });

  it('keeps connecting through a client swap (token refresh) instead of flashing disconnected', () => {
    // gptme.ai swaps in a fresh ApiClient on every Supabase token refresh; the
    // new client starts not-connected with no probe result.
    const connected = makeClient();
    connected.isConnected$.set(true);
    mockCtx.api = connected;
    const seen: string[] = [];
    const { result, rerender } = renderHook(() => {
      const status = useConnectionStatus();
      seen.push(status);
      return status;
    });
    expect(result.current).toBe('connected');

    mockCtx.api = makeClient();
    rerender();
    expect(result.current).toBe('connecting');

    act(() => {
      jest.advanceTimersByTime(CONNECTION_GRACE_MS - 1);
    });
    act(() => {
      mockCtx.api.isConnected$.set(true);
    });
    expect(result.current).toBe('connected');
    expect(seen).not.toContain('disconnected');
  });

  it('reports disconnected when nothing connects within the grace period', () => {
    const { result } = renderHook(() => useConnectionStatus());
    act(() => {
      jest.advanceTimersByTime(CONNECTION_GRACE_MS);
    });
    expect(result.current).toBe('disconnected');
  });

  it('reports disconnected immediately when a probe failed and nothing is retrying', () => {
    mockCtx.api.lastConnectionResult$.set(FAILED);
    const { result } = renderHook(() => useConnectionStatus());
    expect(result.current).toBe('disconnected');
  });

  it('skips the grace period when auto-connect is intentionally skipped', () => {
    mockCtx.autoConnectSkipped = true;
    const { result } = renderHook(() => useConnectionStatus());
    expect(result.current).toBe('disconnected');
  });

  it('restarts the grace period when a connected client drops', () => {
    mockCtx.api.isConnected$.set(true);
    const { result } = renderHook(() => useConnectionStatus());
    act(() => {
      jest.advanceTimersByTime(CONNECTION_GRACE_MS * 2);
    });
    act(() => {
      mockCtx.api.isConnected$.set(false);
    });
    expect(result.current).toBe('connecting');
    act(() => {
      jest.advanceTimersByTime(CONNECTION_GRACE_MS);
    });
    expect(result.current).toBe('disconnected');
  });
});
