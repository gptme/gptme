import { act, renderHook } from '@testing-library/react';
import { observable } from '@legendapp/state';
import {
  CONNECTION_GRACE_MS,
  deriveConnectionStatus,
  useConnectionStatus,
} from '../useConnectionStatus';

type Probe =
  { ok: true; url: string } | { ok: false; url: string; reason: 'network'; message: string };

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

  it('is connecting while an explicit connect attempt is in flight, even after a failure', () => {
    expect(deriveConnectionStatus({ ...base, isAttempting: true, lastResult: FAILED })).toBe(
      'connecting'
    );
  });

  it('is connecting while auto-connecting within the grace period or before any probe failed', () => {
    const auto = { ...base, isAutoConnecting: true };
    expect(deriveConnectionStatus(auto)).toBe('connecting');
    expect(deriveConnectionStatus({ ...auto, lastResult: FAILED })).toBe('connecting');
    // First probe still in flight (no result yet) past the grace period, e.g. a waking instance.
    expect(deriveConnectionStatus({ ...auto, graceExpired: true })).toBe('connecting');
  });

  it('is disconnected once a probe failed and the grace period expired, even while auto-connect retries', () => {
    expect(
      deriveConnectionStatus({
        ...base,
        isAutoConnecting: true,
        lastResult: FAILED,
        graceExpired: true,
      })
    ).toBe('disconnected');
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

  it('shows disconnected after the grace period while auto-connect keeps retrying an unreachable server', () => {
    // Unreachable server: the first auto-connect probe fails fast, then the loop
    // backs off and retries for minutes with isAutoConnecting$ still true.
    mockCtx.isAutoConnecting$.set(true);
    const seen: string[] = [];
    const { result } = renderHook(() => {
      const status = useConnectionStatus();
      seen.push(status);
      return status;
    });
    act(() => {
      mockCtx.api.lastConnectionResult$.set(FAILED);
    });
    expect(result.current).toBe('connecting');
    act(() => {
      jest.advanceTimersByTime(CONNECTION_GRACE_MS - 1);
    });
    expect(seen).not.toContain('disconnected');
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(result.current).toBe('disconnected');
    expect(mockCtx.isAutoConnecting$.get()).toBe(true);
  });

  it('skips the grace period when auto-connect is intentionally skipped', () => {
    mockCtx.autoConnectSkipped = true;
    const { result } = renderHook(() => useConnectionStatus());
    expect(result.current).toBe('disconnected');
  });

  it('does not flash disconnected on a later swap after a slow first connect', () => {
    const seen: string[] = [];
    const { rerender } = renderHook(() => {
      const status = useConnectionStatus();
      seen.push(status);
      return status;
    });
    // First probe takes longer than the grace period...
    act(() => {
      jest.advanceTimersByTime(CONNECTION_GRACE_MS + 1);
    });
    act(() => {
      mockCtx.api.isConnected$.set(true);
    });
    seen.length = 0;
    // ...then a token refresh swaps in a fresh client.
    mockCtx.api = makeClient();
    rerender();
    expect(seen[0]).toBe('connecting');
    expect(seen).not.toContain('disconnected');
  });

  it("ignores the primary's attempt flags for a secondary client", () => {
    const secondary = makeClient();
    secondary.lastConnectionResult$.set(FAILED);
    mockCtx.isAutoConnecting$.set(true);
    const { result } = renderHook(() =>
      useConnectionStatus(secondary as unknown as Parameters<typeof useConnectionStatus>[0])
    );
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
