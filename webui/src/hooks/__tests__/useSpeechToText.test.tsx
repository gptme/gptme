import { act, renderHook, waitFor } from '@testing-library/react';
import { useSpeechToText } from '../useSpeechToText';

const transcribeAudio = jest.fn();
const useUserSettingsMock = jest.fn();
const useSettingsMock = jest.fn();
const fetchMock = jest.fn();

jest.mock('@/contexts/ApiContext', () => ({
  useApi: () => ({
    api: {
      transcribeAudio,
    },
  }),
}));

jest.mock('@/hooks/useUserSettings', () => ({
  useUserSettings: () => useUserSettingsMock(),
}));

jest.mock('@/contexts/SettingsContext', () => ({
  useSettings: () => useSettingsMock(),
}));

class MockMediaStream {
  private tracks = [{ stop: jest.fn() }, { stop: jest.fn() }];

  getTracks(): MediaStreamTrack[] {
    return this.tracks as unknown as MediaStreamTrack[];
  }
}

class MockMediaRecorder {
  static isTypeSupported(type: string) {
    return type.startsWith('audio/webm');
  }

  mimeType: string;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onerror: (() => void) | null = null;
  onstop: (() => void) | null = null;
  state: 'inactive' | 'recording' = 'inactive';

  constructor(
    _stream: MediaStream,
    options?: {
      mimeType?: string;
    }
  ) {
    this.mimeType = options?.mimeType ?? 'audio/webm';
  }

  start() {
    this.state = 'recording';
  }

  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({
      data: new Blob(['audio'], { type: this.mimeType }),
    });
    this.onstop?.();
  }
}

beforeEach(() => {
  transcribeAudio.mockReset();
  useUserSettingsMock.mockReset();
  useUserSettingsMock.mockReturnValue({
    settings: { providers_configured: ['openrouter'] },
    isLoading: false,
    error: null,
    refetch: jest.fn(),
  });
  useSettingsMock.mockReset();
  useSettingsMock.mockReturnValue({
    settings: { sttProvider: 'browser' },
    updateSettings: jest.fn(),
    resetSettings: jest.fn(),
  });
  transcribeAudio.mockResolvedValue({
    text: 'server transcript',
    model: 'openai/whisper-1',
  });
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({
    ok: true,
    json: jest.fn().mockResolvedValue({ text: 'cloud transcript' }),
  });
  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    writable: true,
    value: fetchMock,
  });

  Object.defineProperty(window, 'MediaRecorder', {
    configurable: true,
    writable: true,
    value: MockMediaRecorder,
  });
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    writable: true,
    value: {
      getUserMedia: jest.fn().mockResolvedValue(new MockMediaStream()),
    },
  });
  Object.defineProperty(navigator, 'language', {
    configurable: true,
    value: 'en-US',
  });
  delete (window as Window & { SpeechRecognition?: unknown }).SpeechRecognition;
  delete (window as Window & { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition;
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe('useSpeechToText', () => {
  it('reports unsupported when neither browser STT nor server fallback is available', () => {
    useUserSettingsMock.mockReturnValue({
      settings: { providers_configured: [] },
      isLoading: false,
      error: null,
      refetch: jest.fn(),
    });

    const { result } = renderHook(() => useSpeechToText());

    expect(result.current.isSupported).toBe(false);
  });

  it('reports supported when browser STT is available and server mode is configured', () => {
    useSettingsMock.mockReturnValue({
      settings: { sttProvider: 'server' },
      updateSettings: jest.fn(),
      resetSettings: jest.fn(),
    });
    // Simulate SpeechRecognition being available
    class MockSpeechRecognition {
      continuous = false;
      interimResults = false;
      lang = '';
      onstart: (() => void) | null = null;
      onresult: ((event: unknown) => void) | null = null;
      onerror: ((event: unknown) => void) | null = null;
      onend: (() => void) | null = null;
      start = jest.fn();
      stop = jest.fn();
      abort = jest.fn();
    }
    (window as Window & { SpeechRecognition?: unknown }).SpeechRecognition = MockSpeechRecognition;

    const { result } = renderHook(() => useSpeechToText());

    // isSupported should be true because browser STT is available (even though user prefers server)
    expect(result.current.isSupported).toBe(true);
  });

  it('uses server STT when sttProvider is set to server and browser STT is available', async () => {
    useSettingsMock.mockReturnValue({
      settings: { sttProvider: 'server' },
      updateSettings: jest.fn(),
      resetSettings: jest.fn(),
    });
    // Make SpeechRecognition available (normally would use browser path)
    class MockSpeechRecognition {
      continuous = false;
      interimResults = false;
      lang = '';
      onstart: (() => void) | null = null;
      onresult: ((event: unknown) => void) | null = null;
      onerror: ((event: unknown) => void) | null = null;
      onend: (() => void) | null = null;
      start = jest.fn();
      stop = jest.fn();
      abort = jest.fn();
    }
    (window as Window & { SpeechRecognition?: unknown }).SpeechRecognition = MockSpeechRecognition;

    const handler = jest.fn();
    const { result } = renderHook(() => useSpeechToText());

    act(() => {
      result.current.onFinalResult(handler);
      result.current.startListening();
    });

    // Should use the server recording path despite browser STT being available
    expect(result.current.state).toBe('listening');

    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      result.current.stopListening();
    });

    await waitFor(() => {
      expect(transcribeAudio).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(result.current.state).toBe('idle');
    });

    expect(handler).toHaveBeenCalledWith('server transcript');
  });

  it('sets error state when server mode is selected but OpenRouter is not configured', async () => {
    // User explicitly chose server but hasn't configured OpenRouter
    useUserSettingsMock.mockReturnValue({
      settings: { providers_configured: [] },
      isLoading: false,
      error: null,
      refetch: jest.fn(),
    });
    useSettingsMock.mockReturnValue({
      settings: { sttProvider: 'server' },
      updateSettings: jest.fn(),
      resetSettings: jest.fn(),
    });
    // Browser STT is also available (the problematic scenario)
    class MockSpeechRecognition {
      continuous = false;
      interimResults = false;
      lang = '';
      onstart: (() => void) | null = null;
      onresult: ((event: unknown) => void) | null = null;
      onerror: ((event: unknown) => void) | null = null;
      onend: (() => void) | null = null;
      start = jest.fn();
      stop = jest.fn();
      abort = jest.fn();
    }
    (window as Window & { SpeechRecognition?: unknown }).SpeechRecognition = MockSpeechRecognition;

    const { result } = renderHook(() => useSpeechToText());

    act(() => {
      result.current.startListening();
    });

    // Should surface an error instead of silently doing nothing
    expect(result.current.state).toBe('error');
    expect(transcribeAudio).not.toHaveBeenCalled();
  });

  it('records and transcribes through the server fallback when browser STT is unavailable', async () => {
    const handler = jest.fn();
    const { result } = renderHook(() => useSpeechToText());

    act(() => {
      result.current.onFinalResult(handler);
      result.current.startListening();
    });

    expect(result.current.state).toBe('listening');

    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      result.current.stopListening();
    });

    await waitFor(() => {
      expect(transcribeAudio).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(result.current.state).toBe('idle');
    });

    expect(transcribeAudio.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ language: 'en' }));
    expect(handler).toHaveBeenCalledWith('server transcript');
  });

  it.each(['constructor', 'start'] as const)(
    'releases every acquired track when recorder %s fails and permits retry',
    async (failure) => {
      const failedStream = new MockMediaStream();
      const retryStream = new MockMediaStream();
      jest
        .mocked(navigator.mediaDevices.getUserMedia)
        .mockResolvedValueOnce(failedStream as unknown as MediaStream)
        .mockResolvedValueOnce(retryStream as unknown as MediaStream);
      const setupError = new Error(`recorder ${failure} failed`);
      const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
      if (failure === 'constructor') {
        const recorder = jest.fn().mockImplementationOnce(() => {
          throw setupError;
        });
        Object.assign(recorder, { isTypeSupported: MockMediaRecorder.isTypeSupported });
        window.MediaRecorder = recorder as unknown as typeof MediaRecorder;
      } else {
        jest.spyOn(MockMediaRecorder.prototype, 'start').mockImplementationOnce(() => {
          throw setupError;
        });
      }
      const handler = jest.fn();
      const { result, unmount } = renderHook(() => useSpeechToText());

      await act(async () => {
        result.current.onFinalResult(handler);
        result.current.startListening();
      });

      expect(result.current.state).toBe('error');
      expect(warn).toHaveBeenCalledWith('Recorded speech-to-text setup failed:', setupError);
      for (const track of failedStream.getTracks()) {
        expect(track.stop).toHaveBeenCalledTimes(1);
      }
      expect(transcribeAudio).not.toHaveBeenCalled();
      expect(fetchMock).not.toHaveBeenCalled();

      window.MediaRecorder = MockMediaRecorder as unknown as typeof MediaRecorder;
      await act(async () => result.current.startListening());
      expect(result.current.state).toBe('listening');
      for (const track of retryStream.getTracks()) {
        expect(track.stop).not.toHaveBeenCalled();
      }
      await act(async () => result.current.stopListening());
      expect(result.current.state).toBe('idle');
      expect(handler).toHaveBeenCalledWith('server transcript');
      expect(transcribeAudio).toHaveBeenCalledTimes(1);
      unmount();
      for (const stream of [failedStream, retryStream]) {
        for (const track of stream.getTracks()) {
          expect(track.stop).toHaveBeenCalledTimes(1);
        }
      }
    }
  );

  it.each(['stop', 'unmount'] as const)(
    'releases a late permission stream after %s without starting a recorder',
    async (cancel) => {
      const stream = new MockMediaStream();
      let resolvePermission!: (stream: MediaStream) => void;
      jest.mocked(navigator.mediaDevices.getUserMedia).mockReturnValueOnce(
        new Promise((resolve) => {
          resolvePermission = resolve;
        })
      );
      const start = jest.spyOn(MockMediaRecorder.prototype, 'start');
      const { result, unmount } = renderHook(() => useSpeechToText());
      act(() => result.current.startListening());
      if (cancel === 'unmount') unmount();
      else act(() => result.current.stopListening());

      await act(async () => resolvePermission(stream as unknown as MediaStream));

      expect(start).not.toHaveBeenCalled();
      expect(transcribeAudio).not.toHaveBeenCalled();
      if (cancel === 'stop') {
        expect(result.current.state).toBe('idle');
        unmount();
      }
      for (const track of stream.getTracks()) {
        expect(track.stop).toHaveBeenCalledTimes(1);
      }
    }
  );

  it('releases an active recording on unmount without transcribing', async () => {
    const stream = new MockMediaStream();
    jest
      .mocked(navigator.mediaDevices.getUserMedia)
      .mockResolvedValueOnce(stream as unknown as MediaStream);
    const { result, unmount } = renderHook(() => useSpeechToText());
    await act(async () => result.current.startListening());
    expect(result.current.state).toBe('listening');
    unmount();
    expect(transcribeAudio).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
    for (const track of stream.getTracks()) expect(track.stop).toHaveBeenCalledTimes(1);
  });

  it('does not let an obsolete permission rejection clear a newer recording', async () => {
    let rejectPermission!: (error: Error) => void;
    const stream = new MockMediaStream();
    jest
      .mocked(navigator.mediaDevices.getUserMedia)
      .mockReturnValueOnce(
        new Promise((_resolve, reject) => {
          rejectPermission = reject;
        })
      )
      .mockResolvedValueOnce(stream as unknown as MediaStream);
    jest.spyOn(console, 'warn').mockImplementation(() => {});
    const { result, unmount } = renderHook(() => useSpeechToText());
    act(() => result.current.startListening());
    act(() => result.current.stopListening());
    await act(async () => result.current.startListening());
    await act(async () => rejectPermission(new Error('obsolete permission request')));

    expect(result.current.state).toBe('listening');
    for (const track of stream.getTracks()) expect(track.stop).not.toHaveBeenCalled();
    await act(async () => result.current.stopListening());
    expect(transcribeAudio).toHaveBeenCalledTimes(1);
    expect(result.current.state).toBe('idle');
    unmount();
    for (const track of stream.getTracks()) expect(track.stop).toHaveBeenCalledTimes(1);
  });

  it('uses the refreshed sttAuthToken for cloud transcription without recreating the callback', async () => {
    const handler = jest.fn();
    useUserSettingsMock.mockReturnValue({
      settings: { providers_configured: [] },
      isLoading: false,
      error: null,
      refetch: jest.fn(),
    });

    let settings = { sttProvider: 'server', sttAuthToken: 'token-1' };
    useSettingsMock.mockImplementation(() => ({
      settings,
      updateSettings: jest.fn(),
      resetSettings: jest.fn(),
    }));

    const { result, rerender } = renderHook(() => useSpeechToText());

    settings = { ...settings, sttAuthToken: 'token-2' };
    rerender();

    act(() => {
      result.current.onFinalResult(handler);
      result.current.startListening();
    });

    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      result.current.stopListening();
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(result.current.state).toBe('idle');
    });

    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer token-2' }),
      })
    );
    expect(handler).toHaveBeenCalledWith('cloud transcript');
    expect(transcribeAudio).not.toHaveBeenCalled();
  });
});
