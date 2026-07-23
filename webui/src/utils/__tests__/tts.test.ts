import { speakTextNow, stopSpeaking } from '../tts';

describe('toSpokenText (via speakTextNow)', () => {
  const originalFetch = global.fetch;
  const originalSpeechSynthesis = window.speechSynthesis;
  const originalSpeechSynthesisUtterance = global.SpeechSynthesisUtterance;

  beforeEach(() => {
    localStorage.clear();
    jest.spyOn(console, 'warn').mockImplementation(() => undefined);
    jest.spyOn(console, 'info').mockImplementation(() => undefined);
    global.SpeechSynthesisUtterance = jest.fn().mockImplementation((text: string) => ({
      text,
      rate: 1,
    })) as unknown as typeof SpeechSynthesisUtterance;
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 400,
      clone: () => ({
        json: async () => ({ error: 'OPENROUTER_API_KEY not configured.' }),
      }),
    } as Response);
  });

  afterEach(() => {
    stopSpeaking();
    global.fetch = originalFetch;
    Object.defineProperty(window, 'speechSynthesis', {
      value: originalSpeechSynthesis,
      configurable: true,
    });
    global.SpeechSynthesisUtterance = originalSpeechSynthesisUtterance;
    jest.restoreAllMocks();
  });

  it('strips <thinking> blocks before speaking', async () => {
    const speak = jest.fn();
    Object.defineProperty(window, 'speechSynthesis', {
      value: { speak, cancel: jest.fn() },
      configurable: true,
    });

    const flushPromises = () => new Promise((resolve) => setTimeout(resolve, 0));
    speakTextNow('<thinking>internal reasoning here</thinking>The actual answer.');
    await flushPromises();

    expect(speak).toHaveBeenCalledWith(expect.objectContaining({ text: 'The actual answer.' }));
  });

  it('strips <think> short-form blocks before speaking', async () => {
    const speak = jest.fn();
    Object.defineProperty(window, 'speechSynthesis', {
      value: { speak, cancel: jest.fn() },
      configurable: true,
    });

    const flushPromises = () => new Promise((resolve) => setTimeout(resolve, 0));
    speakTextNow('<think>step 1, step 2</think>Here is the result.');
    await flushPromises();

    expect(speak).toHaveBeenCalledWith(expect.objectContaining({ text: 'Here is the result.' }));
  });

  it('does not speak if content is only thinking', async () => {
    const speak = jest.fn();
    Object.defineProperty(window, 'speechSynthesis', {
      value: { speak, cancel: jest.fn() },
      configurable: true,
    });

    const flushPromises = () => new Promise((resolve) => setTimeout(resolve, 0));
    speakTextNow('<thinking>just reasoning, nothing else</thinking>');
    await flushPromises();

    expect(speak).not.toHaveBeenCalled();
  });
});

describe('tts fallback chain', () => {
  const originalFetch = global.fetch;
  const originalAudio = global.Audio;
  const originalCreateObjectURL = URL.createObjectURL;
  const originalRevokeObjectURL = URL.revokeObjectURL;
  const originalSpeechSynthesis = window.speechSynthesis;
  const originalSpeechSynthesisUtterance = global.SpeechSynthesisUtterance;

  const flushPromises = () => new Promise((resolve) => setTimeout(resolve, 0));

  beforeEach(() => {
    localStorage.clear();
    jest.spyOn(console, 'warn').mockImplementation(() => undefined);
    URL.createObjectURL = jest.fn(() => 'blob:tts-audio');
    URL.revokeObjectURL = jest.fn();
    global.SpeechSynthesisUtterance = jest.fn().mockImplementation((text: string) => ({
      text,
      rate: 1,
    })) as unknown as typeof SpeechSynthesisUtterance;
  });

  afterEach(() => {
    stopSpeaking();
    global.fetch = originalFetch;
    global.Audio = originalAudio;
    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
    Object.defineProperty(window, 'speechSynthesis', {
      value: originalSpeechSynthesis,
      configurable: true,
    });
    global.SpeechSynthesisUtterance = originalSpeechSynthesisUtterance;
    jest.restoreAllMocks();
  });

  it('falls back to Web Speech silently when the local endpoint is not configured', async () => {
    const speak = jest.fn();
    const cancel = jest.fn();
    Object.defineProperty(window, 'speechSynthesis', {
      value: { speak, cancel },
      configurable: true,
    });
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 400,
      clone: () => ({
        json: async () => ({
          error:
            'OPENROUTER_API_KEY not configured. Set the environment variable or add it to config.',
        }),
      }),
    } as Response);

    speakTextNow('hello');
    await flushPromises();

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/v2/audio/speech',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ text: 'hello' }),
      })
    );
    expect(console.warn).not.toHaveBeenCalled();
    expect(speak).toHaveBeenCalledWith(expect.objectContaining({ text: 'hello' }));
  });

  it('falls back to the configured external TTS server after local endpoint errors', async () => {
    localStorage.setItem(
      'gptme-settings',
      JSON.stringify({ ttsServerUrl: 'http://127.0.0.1:5000/' })
    );
    const play = jest.fn().mockResolvedValue(undefined);
    global.Audio = jest.fn().mockImplementation((src: string) => ({
      src,
      play,
      pause: jest.fn(),
      onended: null,
    })) as unknown as typeof Audio;
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 502,
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        blob: async () => new Blob(['audio'], { type: 'audio/wav' }),
      } as Response);

    speakTextNow('hello');
    await flushPromises();
    await flushPromises();

    expect(global.fetch).toHaveBeenNthCalledWith(1, '/api/v2/audio/speech', expect.any(Object));
    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://127.0.0.1:5000/tts?text=hello',
      expect.any(Object)
    );
    expect(play).toHaveBeenCalled();
  });
});
