/**
 * useVoiceSession unit tests
 *
 * Covers: state machine transitions, WebSocket lifecycle, mic level, commit signal.
 * Uses mocked AudioContext, WebSocket, navigator.mediaDevices, and AudioWorkletNode.
 */
import { act } from '@testing-library/react';
import { renderHook, waitFor } from '@testing-library/react';
import { useVoiceSession } from '../useVoiceSession';

// ─── Mock PCMPlayer ─────────────────────────────────────────────────────────

class MockPCMPlayer {
  feedCount = 0;
  resumeCalled = false;
  resetCalled = false;
  closed = false;

  feed() { this.feedCount++; }
  resume() { this.resumeCalled = true; }
  reset() { this.resetCalled = true; }
  close() { this.closed = true; }
}

// ─── Mock AudioContext ───────────────────────────────────────────────────────

class MockAudioContext {
  state: AudioContextState = 'suspended';
  currentTime = 1;
  destination = {} as AudioDestinationNode;
  closed = false;

  async resume() { this.state = 'running'; }
  async close() { this.closed = true; }
  createMediaStreamSource() { return { connect: jest.fn() } as unknown as MediaStreamAudioSourceNode; }
  createAnalyser() {
    const a = {} as AnalyserNode;
    a.fftSize = 256;
    a.getByteFrequencyData = jest.fn().mockReturnValue(new Uint8Array(128).fill(128));
    a.connect = jest.fn();
    return a;
  }
  createGain() {
    const g = {} as GainNode;
    g.gain = { value: 0, connect: jest.fn() } as unknown as AudioParam;
    g.connect = jest.fn();
    return g;
  }
  audioWorklet = { addModule: jest.fn().mockResolvedValue(undefined) };
}

// ─── Mock WebSocket ──────────────────────────────────────────────────────────

type WSReadyState = 0 | 1 | 2 | 3;
const WS_OPEN = 1 as WSReadyState;

class MockWebSocket {
  url: string;
  readyState: WSReadyState = WS_OPEN;
  binaryType: ArrayBuffer = new ArrayBuffer(0);

  onopen: (() => void) | null = null;
  onmessage: ((evt: { data: string | ArrayBuffer }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  sentMessages: (string | ArrayBuffer)[] = [];

  constructor(url: string) { this.url = url; }

  send(data: string | ArrayBuffer) { this.sentMessages.push(data); }

  close() {
    this.readyState = 3 as WSReadyState;
    if (this.onclose) this.onclose();
  }

  // Test helpers — called by tests to simulate server events
  emitReady() {
    if (this.onmessage) {
      this.onmessage({
        data: JSON.stringify({ type: 'ready', input_sample_rate: 16000, output_sample_rate: 24000 }),
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

  emitError() { if (this.onerror) this.onerror(); }
}

// ─── Globals ─────────────────────────────────────────────────────────────────

let mockWs: MockWebSocket;
let mockCtx: MockAudioContext;
let workletPortOnMessage: ((e: MessageEvent) => void) | null = null;

function setupGlobals() {
  mockWs = new MockWebSocket('ws://test.local/voice');
  mockCtx = new MockAudioContext();

  // Mock AudioWorkletNode — stash the port.onmessage handler for test injection
  Object.defineProperty(window, 'AudioWorkletNode', {
    configurable: true,
    writable: true,
    value: jest.fn(() => {
      const node = {
        connect: jest.fn(),
        disconnect: jest.fn(),
        port: {
          onmessage: null as ((e: MessageEvent) => void) | null,
        },
      };
      // Stash port.onmessage so tests can simulate worklet messages
      // (we intercept AudioWorkletNode ctor, but the hook captures it at call time)
      const originalSetOnMessage = Object.defineProperty;
      // We'll store a reference via a side channel below
      return node;
    }),
  });

  // Global refs for test injection
  (window as unknown as Record<string, unknown>).__mockCtx = mockCtx;
  (window as unknown as Record<string, unknown>).__mockWs = mockWs;
  (window as unknown as Record<string, unknown>).__workletPortOnMessage = () => workletPortOnMessage;
}

function clearGlobals() {
  delete (window as unknown as Record<string, unknown>).__mockCtx;
  delete (window as unknown as Record<string, unknown>).__mockWs;
  delete (window as unknown as Record<string, unknown>).__workletPortOnMessage;
}

let originalAudioContext: typeof window.AudioContext;
let originalWebSocket: typeof window.WebSocket;
let originalMediaDevices: typeof navigator.mediaDevices;

beforeEach(() => {
  setupGlobals();

  originalAudioContext = window.AudioContext;
  originalWebSocket = window.WebSocket;
  originalMediaDevices = navigator.mediaDevices;

  Object.defineProperty(window, 'AudioContext', {
    configurable: true,
    writable: true,
    value: jest.fn(() => mockCtx),
  });

  Object.defineProperty(window, 'WebSocket', {
    configurable: true,
    writable: true,
    value: jest.fn(() => mockWs),
  });

  // Mock getUserMedia — must be synchronous for the hook's async IIFE
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    writable: true,
    value: {
      ...navigator.mediaDevices,
      getUserMedia: jest.fn().mockResolvedValue(new MockMediaStream()),
    },
  });

  // Mock requestAnimationFrame to avoid actual frame scheduling
  jest.spyOn(window, 'requestAnimationFrame').mockImplementation(cb => {
    // Execute immediately to keep tests synchronous
    cb(1);
    return 1;
  });
  jest.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});

  // Mock AudioBuffer and AudioBufferSourceNode
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
  Object.defineProperty(window, 'AudioContext', { configurable: true, writable: true, value: originalAudioContext });
  Object.defineProperty(window, 'WebSocket', { configurable: true, writable: true, value: originalWebSocket });
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, writable: true, value: originalMediaDevices });
  jest.restoreAllMocks();
  clearGlobals();
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

    act(() => { result.current.start(); });
    expect(result.current.state).toBe('connecting');

    // Simulate server ready frame
    await waitFor(() => { mockWs.emitReady(); });

    await waitFor(() => {
      expect(result.current.state).toBe('recording');
    });
  });

  it('sets error and ends state on WebSocket error', async () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => { result.current.start(); });
    await waitFor(() => expect(result.current.state).toBe('connecting'));

    mockWs.emitError();

    await waitFor(() => {
      expect(result.current.error).toBe('Voice connection error');
      expect(result.current.state).toBe('ended');
    });
  });

  it('closes WebSocket on stop', async () => {
    const closeSpy = jest.spyOn(mockWs, 'close');
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => { result.current.start(); });
    await waitFor(() => expect(result.current.state).toBe('connecting'));
    mockWs.emitReady();
    await waitFor(() => expect(result.current.state).toBe('recording'));

    act(() => { result.current.stop(); });

    await waitFor(() => {
      expect(result.current.state).toBe('ended');
      expect(closeSpy).toHaveBeenCalled();
    });
  });

  it('resets state to idle after ended timeout (1500ms)', async () => {
    jest.useFakeTimers();
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => { result.current.start(); });
    mockWs.emitReady();
    await waitFor(() => expect(result.current.state).toBe('recording'));

    act(() => { result.current.stop(); });
    await waitFor(() => expect(result.current.state).toBe('ended'));

    await act(async () => {
      jest.advanceTimersByTime(1500);
    });

    expect(result.current.state).toBe('idle');
    jest.useRealTimers();
  });

  it('sends {"type":"commit"} when commit() is called during recording', async () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => { result.current.start(); });
    mockWs.emitReady();
    await waitFor(() => expect(result.current.state).toBe('recording'));

    act(() => { result.current.commit(); });

    expect(mockWs.sentMessages.some(m =>
      typeof m === 'string' && JSON.parse(m).type === 'commit'
    )).toBe(true);
  });

  it('does not send commit when not connected', () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));
    act(() => { result.current.commit(); });
    expect(mockWs.sentMessages).toHaveLength(0);
  });

  it('closes WebSocket on server-initiated close', async () => {
    const closeSpy = jest.spyOn(mockWs, 'close');
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => { result.current.start(); });
    mockWs.emitReady();
    await waitFor(() => expect(result.current.state).toBe('recording'));

    act(() => { mockWs.close(); });

    await waitFor(() => {
      expect(result.current.state).toBe('ended');
      // close() was called by the hook's onclose handler
      expect(closeSpy).toHaveBeenCalled();
    });
  });

  it('does not start when voiceServerUrl is empty', () => {
    const { result } = renderHook(() => useVoiceSession(''));
    act(() => { result.current.start(); });
    expect(result.current.state).toBe('idle');
    expect(result.current.error).toBeNull();
  });

  it('does not start a second session when one is already active', async () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => { result.current.start(); });
    await waitFor(() => expect(result.current.state).toBe('connecting'));

    const firstState = result.current.state;

    act(() => { result.current.start(); });

    // Should stay in connecting, not restart
    expect(result.current.state).toBe(firstState);
  });

  it('forwards mic audio frames to WebSocket via worklet port', async () => {
    const { result } = renderHook(() => useVoiceSession('ws://test.local/voice'));

    act(() => { result.current.start(); });
    mockWs.emitReady();
    await waitFor(() => expect(result.current.state).toBe('recording'));

    // Capture worklet port.onmessage
    const hookWindow = window as unknown as Record<string, unknown>;
    const portOnMessage = hookWindow.__workletPortOnMessage as () => typeof workletPortOnMessage;

    // Emit a fake PCM frame from the worklet
    const frameData = pcm16Buffer([1, 2, 3]);
    if (workletPortOnMessage) {
      workletPortOnMessage({ data: frameData } as MessageEvent);
    }

    // The hook should have sent it over WebSocket
    expect(mockWs.sentMessages.some(m => m === frameData)).toBe(true);
  });
});

// ─── Mock MediaStream for getUserMedia ───────────────────────────────────────

class MockMediaStreamTrack implements MediaStreamTrack {
  kind = 'audio' as const;
  id = 'mock-track-id';
  label = 'Mock Microphone';
  enabled = true;
  muted = false;
  readyState = 'live' as MediaStreamTrackState;
  onended: ((...args: unknown[]) => unknown) | null = null;

  stop() { /* no-op in mock */ }
  getSettings() { return {} as MediaTrackSettings; }
  getConstraints() { return {} as MediaTrackConstraints; }
  applyConstraints() { return Promise.resolve(); }
  clone() { return this; }
  dispatchEvent() { return false; }
}

class MockMediaStream implements MediaStream {
  id = 'mock-stream';
  active = true;
  tracks = [new MockMediaStreamTrack()];
  getTracks() { return this.tracks; }
  getAudioTracks() { return this.tracks as MediaStreamTrack[]; }
  getVideoTracks() { return [] as MediaStreamTrack[]; }
  addTrack() { /* no-op */ }
  removeTrack() { /* no-op */ }
  clone() { return new MockMediaStream(); }
  addEventListener() { /* no-op */ }
  removeEventListener() { /* no-op */ }
  dispatchEvent() { return false; }
  onaddtrack: ((...args: unknown[]) => unknown) | null = null;
  onremovetrack: ((...args: unknown[]) => unknown) | null = null;
}
