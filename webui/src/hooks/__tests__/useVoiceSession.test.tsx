/**
 * useVoiceSession unit tests
 *
 * Covers: state machine transitions, WebSocket lifecycle, mic level, commit signal.
 * Uses mocked AudioContext, WebSocket, navigator.mediaDevices, and AudioWorkletNode.
 */
import { act } from '@testing-library/react';
import { renderHook, waitFor } from '@testing-library/react';
import { useVoiceSession } from '../useVoiceSession';

// ─── Mock AudioContext ───────────────────────────────────────────────────────

class MockAudioContext {
  state: AudioContextState = 'suspended';
  currentTime = 1;
  destination = {} as AudioDestinationNode;
  closed = false;

  async resume() {
    this.state = 'running';
  }
  async close() {
    this.closed = true;
  }
  createMediaStreamSource() {
    return { connect: jest.fn() } as unknown as MediaStreamAudioSourceNode;
  }
  createAnalyser() {
    return {
      fftSize: 256,
      frequencyBinCount: 128,
      getByteFrequencyData: jest.fn(),
    } as unknown as AnalyserNode;
  }
  createGain() {
    return { gain: { value: 0 }, connect: jest.fn() } as unknown as GainNode;
  }
  audioWorklet = { addModule: jest.fn().mockResolvedValue(undefined) };
}

// ─── Mock AudioWorkletNode (not available in jsdom) ──────────────────────────

let _lastWorkletPort: { onmessage: ((e: MessageEvent) => void) | null } | null = null;

class MockAudioWorkletNode {
  port: { onmessage: ((e: MessageEvent) => void) | null };
  constructor(_context: AudioContext, _name: string) {
    this.port = { onmessage: null };
    _lastWorkletPort = this.port;
  }
  connect() {
    return this;
  }
}

/** Return the worklet port from the last constructed MockAudioWorkletNode. */
function lastWorkletPort(): { onmessage: ((e: MessageEvent) => void) | null } | null {
  return _lastWorkletPort;
}

// ─── Mock WebSocket ──────────────────────────────────────────────────────────

type WSReadyState = 0 | 1 | 2 | 3;
const WS_OPEN = 1 as WSReadyState;

class MockWebSocket {
  static OPEN = 1 as WSReadyState;

  url: string;
  readyState: WSReadyState = WS_OPEN;
  binaryType: ArrayBuffer = new ArrayBuffer(0);

  onopen: (() => void) | null = null;
  onmessage: ((evt: { data: string | ArrayBuffer }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  sentMessages: (string | ArrayBuffer)[] = [];

  constructor(url: string) {
    this.url = url;
  }

  send(data: string | ArrayBuffer) {
    this.sentMessages.push(data);
  }

  /** Called by the hook in cleanup or by tests to simulate server-side close. */
  close() {
    // Avoid re-entrant onclose loops: the hook's cleanup nulls onclose then
    // calls close(), and onclose itself also calls cleanup(). Guard by
    // releasing the handler before calling it, similar to real WebSocket.
    this.readyState = 3 as WSReadyState;
    const cb = this.onclose;
    this.onclose = null;
    if (cb) cb();
  }

  // Test helpers — called by tests to simulate server events
  emitReady() {
    if (this.onmessage) {
      this.onmessage({
        data: JSON.stringify({
          type: 'ready',
          input_sample_rate: 16000,
          output_sample_rate: 24000,
        }),
      });
    }
  }

  emitAudioEnd() {
    if (this.onmessage) {
      this.onmessage({ data: JSON.stringify({ type: 'audio_end' }) });
    }
  }

  emitAudio(data: ArrayBuffer) {
    if (this.onmessage) this.onmessage({ data });
  }

  emitError() {
    if (this.onerror) this.onerror();
  }
}

// ─── Init / cleanup ─────────────────────────────────────────────────────────

/**
 * Wait for async microtasks (RAF callbacks scheduled via setTimeout(0)) to settle.
 * Must be called inside act() from the test.
 */
async function flushMicrotasks() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 10));
  });
}

let mockWs: MockWebSocket;
let mockCtx: MockAudioContext;

beforeEach(() => {
  _lastWorkletPort = null;
  mockWs = new MockWebSocket('');
  mockCtx = new MockAudioContext();

  // Mock AudioContext
  Object.defineProperty(window, 'AudioContext', {
    configurable: true,
    writable: true,
    value: jest.fn(() => mockCtx),
  });

  // Mock WebSocket constructor + preserve static OPEN property
  const MockWSConstructor: any = jest.fn(() => mockWs);
  MockWSConstructor.OPEN = MockWebSocket.OPEN;
  MockWSConstructor.CONNECTING = 0;
  MockWSConstructor.CLOSING = 2;
  MockWSConstructor.CLOSED = 3;
  Object.defineProperty(window, 'WebSocket', {
    configurable: true,
    writable: true,
    value: MockWSConstructor,
  });

  // Mock AudioWorkletNode
  Object.defineProperty(window, 'AudioWorkletNode', {
    configurable: true,
    writable: true,
    value: MockAudioWorkletNode,
  });

  // Mock getUserMedia
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    writable: true,
    value: {
      getUserMedia: jest.fn().mockResolvedValue(new MockMediaStream()),
    },
  });

  // Mock requestAnimationFrame / cancelAnimationFrame via macrotask
  // so calls inside the level-meter tick don't recurse infinitely.
  jest.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
    const id = setTimeout(() => cb(performance.now()), 0);
    return id as unknown as number;
  });
  jest.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
    clearTimeout(id);
  });

  // AudioBuffer constructors
  Object.defineProperty(window, 'AudioBuffer', {
    configurable: true,
    writable: true,
    value: jest.fn(),
  });
  Object.defineProperty(window, 'AudioBufferSourceNode', {
    configurable: true,
    writable: true,
    value: jest.fn(),
  });
});

afterEach(() => {
  delete (window as any).AudioContext;
  delete (window as any).WebSocket;
  delete (window as any).AudioWorkletNode;
  delete (window as any).AudioBuffer;
  delete (window as any).AudioBufferSourceNode;
  jest.restoreAllMocks();
});

// ─── Helpers ────────────────────────────────────────────────────────────────

function pcm16Buffer(samples: number[]): ArrayBuffer {
  return new Int16Array(samples).buffer;
}

// ─── Tests ──────────────────────────────────────────────────────────────────

describe('useVoiceSession', () => {
  it('starts in idle state with no error', () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));
    expect(result.current.state).toBe('idle');
    expect(result.current.error).toBeNull();
    expect(result.current.level).toBe(0);
  });

  it('transitions idle → connecting → recording on start', async () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => {
      result.current.start();
    });
    expect(result.current.state).toBe('connecting');

    // Let async setup (getUserMedia, AudioContext, addModule, AudioWorkletNode) resolve
    await flushMicrotasks();

    // Simulate server ready frame
    act(() => {
      mockWs.emitReady();
    });

    await waitFor(() => {
      expect(result.current.state).toBe('recording');
    });
  });

  it('sets error and ends state on WebSocket error', async () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => {
      result.current.start();
    });
    await flushMicrotasks();

    act(() => {
      mockWs.emitError();
    });

    await waitFor(() => {
      expect(result.current.error).toBe('Voice connection error');
      expect(result.current.state).toBe('ended');
    });
  });

  it('closes WebSocket on stop', async () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => {
      result.current.start();
    });
    await flushMicrotasks();
    act(() => {
      mockWs.emitReady();
    });
    await waitFor(() => expect(result.current.state).toBe('recording'));

    act(() => {
      result.current.stop();
    });

    await waitFor(() => {
      expect(result.current.state).toBe('ended');
    });
    // stop() → cleanup() → s.ws.close() → readyState becomes 3 (CLOSED)
    expect(mockWs.readyState).toBe(3);
  });

  it('resets state to idle after ended timeout (1500ms)', async () => {
    jest.useFakeTimers();
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => {
      result.current.start();
    });
    await act(async () => {
      jest.advanceTimersByTime(10);
    });
    mockWs.emitReady();
    await waitFor(() => expect(result.current.state).toBe('recording'), { timeout: 1000 });

    act(() => {
      result.current.stop();
    });
    await waitFor(() => expect(result.current.state).toBe('ended'), { timeout: 1000 });

    await act(async () => {
      jest.advanceTimersByTime(1500);
    });
    expect(result.current.state).toBe('idle');
    jest.useRealTimers();
  });

  it('sends {"type":"commit"} when commit() is called during recording', async () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => {
      result.current.start();
    });
    await flushMicrotasks();
    mockWs.emitReady();
    await waitFor(() => expect(result.current.state).toBe('recording'));

    act(() => {
      result.current.commit();
    });

    expect(
      mockWs.sentMessages.some((m) => typeof m === 'string' && JSON.parse(m).type === 'commit')
    ).toBe(true);
  });

  it('does not send commit when not connected', () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));
    act(() => {
      result.current.commit();
    });
    expect(mockWs.sentMessages).toHaveLength(0);
  });

  it('closes WebSocket on server-initiated close', async () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => {
      result.current.start();
    });
    await flushMicrotasks();
    mockWs.emitReady();
    await waitFor(() => expect(result.current.state).toBe('recording'));

    act(() => {
      mockWs.close();
    });

    await waitFor(() => {
      expect(result.current.state).toBe('ended');
    });
    expect(mockWs.readyState).toBe(3);
  });

  it('does not start when voiceServerUrl is empty', () => {
    const { result } = renderHook(() => useVoiceSession(''));
    act(() => {
      result.current.start();
    });
    expect(result.current.state).toBe('idle');
    expect(result.current.error).toBeNull();
  });

  it('does not start a second session when one is already active', async () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => {
      result.current.start();
    });
    await flushMicrotasks();

    const firstState = result.current.state;

    act(() => {
      result.current.start();
    });

    // Should stay in same state, not restart
    expect(result.current.state).toBe(firstState);
  });

  it('forwards mic audio frames to WebSocket via worklet port', async () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => {
      result.current.start();
    });
    await flushMicrotasks();
    mockWs.emitReady();
    await waitFor(() => expect(result.current.state).toBe('recording'));

    // After emitReady, the hook's ws.onmessage handler sets
    // workletNode.port.onmessage = (e) => { ws.send(e.data) }.
    // Our mock captures the port as _lastWorkletPort.
    const port = lastWorkletPort();
    expect(port).not.toBeNull();

    const frameData = pcm16Buffer([1, 2, 3]);
    if (port?.onmessage) {
      (port.onmessage as (e: MessageEvent) => void)({ data: frameData } as MessageEvent);
    }

    expect(mockWs.sentMessages.some((m) => m === frameData)).toBe(true);
  });
});

// ─── Mock MediaStream for getUserMedia ───────────────────────────────────────

class MockMediaStreamTrack implements MediaStreamTrack {
  addEventListener = jest.fn();
  removeEventListener = jest.fn();
  contentHint = '';
  onmute: ((...args: unknown[]) => unknown) | null = null;
  onunmute: ((...args: unknown[]) => unknown) | null = null;
  kind = 'audio' as const;
  id = 'mock-track-id';
  label = 'Mock Microphone';
  enabled = true;
  muted = false;
  readyState = 'live' as MediaStreamTrackState;
  onended: ((...args: unknown[]) => unknown) | null = null;

  stop() {
    /* no-op */
  }
  getSettings() {
    return {} as MediaTrackSettings;
  }
  getCapabilities(): MediaTrackCapabilities {
    return {};
  }
  getConstraints() {
    return {} as MediaTrackConstraints;
  }
  applyConstraints() {
    return Promise.resolve();
  }
  clone() {
    return this as unknown as MediaStreamTrack;
  }
  dispatchEvent() {
    return false;
  }
}

class MockMediaStream implements MediaStream {
  id = 'mock-stream';
  active = true;
  tracks = [new MockMediaStreamTrack()];
  getTracks() {
    return this.tracks;
  }
  getAudioTracks() {
    return this.tracks as unknown as MediaStreamTrack[];
  }
  getVideoTracks() {
    return [] as MediaStreamTrack[];
  }
  addTrack() {
    /* no-op */
  }
  removeTrack() {
    /* no-op */
  }
  getTrackById() {
    return null;
  }
  clone() {
    return this as unknown as MediaStream;
  }
  addEventListener() {
    /* no-op */
  }
  removeEventListener() {
    /* no-op */
  }
  dispatchEvent() {
    return false;
  }
  onaddtrack: ((...args: unknown[]) => unknown) | null = null;
  onremovetrack: ((...args: unknown[]) => unknown) | null = null;
}
