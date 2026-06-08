/**
 * Text-to-speech via the browser's Web Speech API, the gptme server's
 * built-in /api/v2/tts endpoint, or an external gptme-tts server.
 *
 * Priority:
 *   1. POST /api/v2/tts (same-origin, uses OpenRouter via gptme server config).
 *   2. If `ttsServerUrl` is configured, GET {url}/tts?text=... and play the WAV.
 *   3. Fall back to the browser's built-in speechSynthesis API.
 *
 * All paths strip markdown before speaking so output sounds natural.
 */

let currentAudio: HTMLAudioElement | null = null;
let currentFetchController: AbortController | null = null;
const LOCAL_TTS_NOT_CONFIGURED = 'tts-local-not-configured';

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

/** POST text to the gptme server's built-in /api/v2/tts endpoint. */
async function speakViaLocalEndpoint(text: string): Promise<void> {
  const controller = new AbortController();
  currentFetchController = controller;
  try {
    const response = await fetch('/api/v2/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
      signal: controller.signal,
    });
    if (!response.ok) {
      // 400 = no API key configured locally; signal speak() to fall through silently
      if (response.status === 400) throw new Error(LOCAL_TTS_NOT_CONFIGURED);
      throw new Error(`TTS endpoint error: ${response.status}`);
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);

    if (controller.signal.aborted) {
      URL.revokeObjectURL(objectUrl);
      return;
    }

    if (currentAudio) {
      currentAudio.pause();
      URL.revokeObjectURL(currentAudio.src);
    }
    const audio = new Audio(objectUrl);
    currentAudio = audio;
    audio.onended = () => {
      URL.revokeObjectURL(objectUrl);
      if (currentAudio === audio) currentAudio = null;
    };
    await audio.play();
  } finally {
    if (currentFetchController === controller) {
      currentFetchController = null;
    }
  }
}

async function speakViaExternalServer(text: string, serverUrl: string): Promise<void> {
  const url = `${serverUrl.replace(/\/$/, '')}/tts?${new URLSearchParams({ text })}`;
  const controller = new AbortController();
  currentFetchController = controller;
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) throw new Error(`TTS server error: ${response.status}`);
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);

    if (controller.signal.aborted) {
      URL.revokeObjectURL(objectUrl);
      return;
    }

    if (currentAudio) {
      currentAudio.pause();
      URL.revokeObjectURL(currentAudio.src);
    }
    const audio = new Audio(objectUrl);
    currentAudio = audio;
    audio.onended = () => {
      URL.revokeObjectURL(objectUrl);
      if (currentAudio === audio) currentAudio = null;
    };
    await audio.play();
  } finally {
    if (currentFetchController === controller) {
      currentFetchController = null;
    }
  }
}

async function speak(rawText: string): Promise<void> {
  const spoken = toSpokenText(rawText);
  if (!spoken) return;

  stopSpeaking();

  // 1. Try the same-origin /api/v2/tts endpoint first.
  try {
    await speakViaLocalEndpoint(spoken);
    return;
  } catch (err) {
    if (isAbortError(err)) return;
    if ((err as Error)?.message !== LOCAL_TTS_NOT_CONFIGURED) {
      console.warn('Local /api/v2/tts unavailable, trying alternatives:', err);
    }
  }

  // 2. Try an external gptme-tts server if configured.
  const { ttsServerUrl } = getSettings();
  if (ttsServerUrl) {
    try {
      await speakViaExternalServer(spoken, ttsServerUrl);
      return;
    } catch (err) {
      if (isAbortError(err)) return;
      console.warn('External TTS server unavailable, falling back to Web Speech API:', err);
    }
  }

  // 3. Fall back to the browser's built-in speechSynthesis.
  if (!window.speechSynthesis) return;
  const utterance = new SpeechSynthesisUtterance(spoken);
  utterance.rate = 1.1;
  window.speechSynthesis.speak(utterance);
}

/** Speak text if the global TTS toggle is enabled (auto-play on new messages). */
export function speakText(rawText: string): void {
  const { ttsEnabled } = getSettings();
  if (!ttsEnabled) return;
  void speak(rawText);
}

/** Speak text immediately, regardless of the global TTS toggle (per-message button). */
export function speakTextNow(rawText: string): void {
  void speak(rawText);
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
}

export function isSpeechSupported(): boolean {
  return 'speechSynthesis' in window || typeof Audio !== 'undefined';
}
