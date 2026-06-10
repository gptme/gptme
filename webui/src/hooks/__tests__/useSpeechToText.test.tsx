import { act, renderHook, waitFor } from '@testing-library/react';
import { useSpeechToText } from '../useSpeechToText';

const transcribeAudio = jest.fn();
const useUserSettingsMock = jest.fn();

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

class MockMediaStream {
  private tracks = [{ stop: jest.fn() }];

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
  transcribeAudio.mockResolvedValue({
    text: 'server transcript',
    model: 'openai/whisper-1',
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
});
