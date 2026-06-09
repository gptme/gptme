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

function getSettings(): { ttsEnabled: boolean; ttsServerUrl: string } {
  try {
    const saved = localStorage.getItem('gptme-settings');
    if (saved) {
      const s = JSON.parse(saved);
      return {
        ttsEnabled: s.ttsEnabled === true,
        ttsServerUrl: typeof s.ttsServerUrl === 'string' ? s.ttsServerUrl.trim() : '',
      };
    }
  } catch {
    // ignore
  }
  return { ttsEnabled: false, ttsServerUrl: '' };
}

/** Strip markdown so the spoken text sounds natural. */
function toSpokenText(markdown: string): string {
  return (
    markdown
      // Remove fenced code blocks entirely
      .replace(/```[\s\S]*?```/g, '[code block]')
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
  if (response.status !== 400) return false;
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
    const response = await fetch('/api/v2/audio/speech', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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

async function speak(rawText: string, key: string): Promise<void> {
  const spoken = toSpokenText(rawText);
  if (!spoken) return;

  stopSpeaking();
  // Mark as playing immediately so the button shows a stop/loading state during
  // the network round-trip (playBlob/utterance re-affirm the same key on start).
  setSpeakingKey(key);

  // 1. Try the same-origin /api/v2/audio/speech endpoint first.
  try {
    await speakViaLocalEndpoint(spoken, key);
    return;
  } catch (err) {
    if (isAbortError(err)) return;
    if ((err as Error)?.message !== LOCAL_TTS_NOT_CONFIGURED) {
      console.warn('Local /api/v2/audio/speech unavailable, trying alternatives:', err);
    }
  }

  // 2. Try an external gptme-tts server if configured.
  const { ttsServerUrl } = getSettings();
  if (ttsServerUrl) {
    try {
      await speakViaExternalServer(spoken, ttsServerUrl, key);
      return;
    } catch (err) {
      if (isAbortError(err)) return;
      console.warn('External TTS server unavailable, falling back to Web Speech API:', err);
    }
  }

  // 3. Fall back to the browser's built-in speechSynthesis.
  if (!window.speechSynthesis) {
    // Nothing could play — clear the provisional speaking state.
    if (getSpeakingKey() === key) setSpeakingKey(null);
    return;
  }
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
  window.speechSynthesis.speak(utterance);
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
