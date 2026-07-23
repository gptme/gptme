/**
 * Text-to-speech via the browser's Web Speech API, the gptme server's
 * built-in /api/v2/audio/speech endpoint, or an external gptme-tts server.
 *
 * Priority:
 *   1. POST /api/v2/audio/speech (same-origin, uses OpenRouter via gptme server config).
 *   2. If `ttsServerUrl` is configured, GET {url}/tts?text=... and play the WAV.
 *   3. Fall back to the browser's built-in speechSynthesis API.
 *
 * All paths strip markdown before speaking so output sounds natural.
 */

let currentAudio: HTMLAudioElement | null = null;
let currentFetchController: AbortController | null = null;
const LOCAL_TTS_NOT_CONFIGURED = 'tts-local-not-configured';
let _ttsNotConfiguredHintShown = false;

// Which logical item (e.g. a message key) is currently being spoken, so the UI
// can show a stop/playing state. null = nothing playing.
let speakingKey: string | null = null;
const speakingListeners = new Set<() => void>();

function setSpeakingKey(key: string | null): void {
  if (speakingKey === key) return;
  speakingKey = key;
  for (const listener of speakingListeners) listener();
}

/** Subscribe to speaking-state changes (for useSyncExternalStore). */
export function subscribeSpeaking(listener: () => void): () => void {
  speakingListeners.add(listener);
  return () => speakingListeners.delete(listener);
}

/** Key of the item currently being spoken, or null. */
export function getSpeakingKey(): string | null {
  return speakingKey;
}

export type TtsProvider = 'auto' | 'browser' | 'server' | 'external';

function getSettings(): {
  ttsEnabled: boolean;
  ttsServerUrl: string;
  ttsProvider: TtsProvider;
  ttsAuthToken: string;
} {
  try {
    const saved = localStorage.getItem('gptme-settings');
    if (saved) {
      const s = JSON.parse(saved);
      const provider: TtsProvider = ['auto', 'browser', 'server', 'external'].includes(
        s.ttsProvider
      )
        ? s.ttsProvider
        : 'auto';
      return {
        ttsEnabled: s.ttsEnabled === true,
        ttsServerUrl: typeof s.ttsServerUrl === 'string' ? s.ttsServerUrl.trim() : '',
        ttsProvider: provider,
        ttsAuthToken: typeof s.ttsAuthToken === 'string' ? s.ttsAuthToken.trim() : '',
      };
    }
  } catch {
    // ignore
  }
  return { ttsEnabled: false, ttsServerUrl: '', ttsProvider: 'auto', ttsAuthToken: '' };
}

/**
 * Strip non-spoken assistant internals and Markdown for TTS.
 *
 * Keep this aligned with gptme-tts's Python `clean_for_speech()` in
 * `plugins/gptme-tts/src/gptme_tts/tts.py` (gptme-contrib): both reasoning and
 * tool-use are implementation details and must never be spoken.
 */
function toSpokenText(markdown: string): string {
  return (
    markdown
      // Remove thinking blocks entirely (model reasoning, not output)
      .replace(/<think(?:ing)?>([\s\S]*?)<\/think(?:ing)?>/g, '')
      // As in clean_for_speech(), every fenced block is non-spoken. In assistant
      // messages these include gptme's Markdown-format tool calls.
      .replace(/```[\s\S]*?```/g, '')
      // Remove inline code
      .replace(/`[^`]+`/g, '[code]')
      // Remove bold/italic markers
      .replace(/\*{1,3}([^*]+)\*{1,3}/g, '$1')
      .replace(/_{1,3}([^_]+)_{1,3}/g, '$1')
      // Remove markdown links — keep label text
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      // Remove raw URLs
      .replace(/https?:\/\/\S+/g, '')
      // Remove heading markers
      .replace(/^#{1,6}\s+/gm, '')
      // Collapse whitespace
      .replace(/\s+/g, ' ')
      .trim()
  );
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

async function isLocalTtsNotConfigured(response: Response): Promise<boolean> {
  // 503 = new servers (no TTS provider configured), 400 = legacy servers
  if (response.status !== 503 && response.status !== 400) return false;
  try {
    const data = await response.clone().json();
    return (
      typeof data?.error === 'string' && data.error.includes('OPENROUTER_API_KEY not configured')
    );
  } catch {
    return false;
  }
}

/** Play an audio blob, wiring up cleanup + speaking-state clearing. */
function playBlob(blob: Blob, key: string): void {
  const objectUrl = URL.createObjectURL(blob);
  if (currentAudio) {
    currentAudio.pause();
    URL.revokeObjectURL(currentAudio.src);
  }
  const audio = new Audio(objectUrl);
  currentAudio = audio;
  const cleanup = () => {
    URL.revokeObjectURL(objectUrl);
    if (currentAudio === audio) {
      currentAudio = null;
      setSpeakingKey(null);
    }
  };
  audio.onended = cleanup;
  audio.onerror = cleanup;
  setSpeakingKey(key);
  void audio.play().catch(cleanup);
}

/** POST text to the gptme server's built-in /api/v2/audio/speech endpoint. */
async function speakViaLocalEndpoint(text: string, key: string): Promise<void> {
  const controller = new AbortController();
  currentFetchController = controller;
  try {
    const { ttsAuthToken } = getSettings();
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (ttsAuthToken) {
      headers['Authorization'] = `Bearer ${ttsAuthToken}`;
    }
    const response = await fetch('/api/v2/audio/speech', {
      method: 'POST',
      headers,
      body: JSON.stringify({ text }),
      signal: controller.signal,
    });
    if (!response.ok) {
      if (await isLocalTtsNotConfigured(response)) {
        throw new Error(LOCAL_TTS_NOT_CONFIGURED);
      }
      throw new Error(`TTS endpoint error: ${response.status}`);
    }
    const blob = await response.blob();
    if (controller.signal.aborted) return;
    playBlob(blob, key);
  } finally {
    if (currentFetchController === controller) {
      currentFetchController = null;
    }
  }
}

async function speakViaExternalServer(text: string, serverUrl: string, key: string): Promise<void> {
  const url = `${serverUrl.replace(/\/$/, '')}/tts?${new URLSearchParams({ text })}`;
  const controller = new AbortController();
  currentFetchController = controller;
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) throw new Error(`TTS server error: ${response.status}`);
    const blob = await response.blob();
    if (controller.signal.aborted) return;
    playBlob(blob, key);
  } finally {
    if (currentFetchController === controller) {
      currentFetchController = null;
    }
  }
}

const DEFAULT_SPEAK_KEY = '__tts__';

/** Start browser speechSynthesis. Returns false if unavailable. */
function speakViaBrowser(spoken: string, key: string): boolean {
  if (!window.speechSynthesis) return false;
  const utterance = new SpeechSynthesisUtterance(spoken);
  utterance.rate = 1.1;
  // Guard against a previous utterance's late onend/onerror (fired after a new
  // speak() already set its key) wiping the newer speaking state.
  utterance.onend = () => {
    if (getSpeakingKey() === key) setSpeakingKey(null);
  };
  utterance.onerror = () => {
    if (getSpeakingKey() === key) setSpeakingKey(null);
  };
  setSpeakingKey(key);
  window.speechSynthesis.speak(utterance);
  return true;
}

function clearIfStill(key: string): void {
  if (getSpeakingKey() === key) setSpeakingKey(null);
}

async function speak(rawText: string, key: string): Promise<void> {
  const spoken = toSpokenText(rawText);
  if (!spoken) return;

  stopSpeaking();
  // Mark as playing immediately so the button shows a stop/loading state during
  // the network round-trip (playBlob/utterance re-affirm the same key on start).
  setSpeakingKey(key);

  const { ttsServerUrl, ttsProvider } = getSettings();

  // Explicit engine selection. Each falls back to the browser if its engine
  // fails, so TTS still works (and aborts — from a newer call — bail silently).
  if (ttsProvider === 'browser') {
    if (!speakViaBrowser(spoken, key)) clearIfStill(key);
    return;
  }
  if (ttsProvider === 'server') {
    try {
      await speakViaLocalEndpoint(spoken, key);
      return;
    } catch (err) {
      if (isAbortError(err)) return;
      console.warn('gptme-server TTS unavailable, falling back to browser:', err);
    }
    if (!speakViaBrowser(spoken, key)) clearIfStill(key);
    return;
  }
  if (ttsProvider === 'external') {
    if (ttsServerUrl) {
      try {
        await speakViaExternalServer(spoken, ttsServerUrl, key);
        return;
      } catch (err) {
        if (isAbortError(err)) return;
        console.warn('External gptme-tts server unavailable, falling back to browser:', err);
      }
    } else {
      console.warn(
        'TTS engine "external" selected but no gptme-tts server URL set; using browser.'
      );
    }
    if (!speakViaBrowser(spoken, key)) clearIfStill(key);
    return;
  }

  // 'auto': gptme-server endpoint -> external gptme-tts server -> browser.
  try {
    await speakViaLocalEndpoint(spoken, key);
    return;
  } catch (err) {
    if (isAbortError(err)) return;
    if ((err as Error)?.message === LOCAL_TTS_NOT_CONFIGURED) {
      if (!_ttsNotConfiguredHintShown) {
        _ttsNotConfiguredHintShown = true;
        console.info(
          '[gptme TTS] Server TTS not configured (no OPENROUTER_API_KEY). ' +
            'For local TTS, install the gptme-tts server (pip install gptme-tts) ' +
            'and add its URL in Settings → TTS engine → gptme-tts server URL. ' +
            'Falling back to browser speech synthesis.'
        );
      }
    } else {
      console.warn('Local /api/v2/audio/speech unavailable, trying alternatives:', err);
    }
  }
  if (ttsServerUrl) {
    try {
      await speakViaExternalServer(spoken, ttsServerUrl, key);
      return;
    } catch (err) {
      if (isAbortError(err)) return;
      console.warn('External TTS server unavailable, falling back to Web Speech API:', err);
    }
  }
  if (!speakViaBrowser(spoken, key)) clearIfStill(key);
}

/** Speak text if the global TTS toggle is enabled (auto-play on new messages). */
export function speakText(rawText: string, key: string = DEFAULT_SPEAK_KEY): void {
  const { ttsEnabled } = getSettings();
  if (!ttsEnabled) return;
  void speak(rawText, key);
}

/** Speak text immediately, regardless of the global TTS toggle (per-message button). */
export function speakTextNow(rawText: string, key: string = DEFAULT_SPEAK_KEY): void {
  void speak(rawText, key);
}

export function stopSpeaking(): void {
  if (currentFetchController) {
    currentFetchController.abort();
    currentFetchController = null;
  }
  if (currentAudio) {
    currentAudio.pause();
    URL.revokeObjectURL(currentAudio.src);
    currentAudio = null;
  }
  if (window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
  setSpeakingKey(null);
}

export function isSpeechSupported(): boolean {
  return 'speechSynthesis' in window || typeof Audio !== 'undefined';
}
