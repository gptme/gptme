/**
 * Text-to-speech via the browser's Web Speech API or a gptme-tts server.
 *
 * Priority:
 *   1. If `ttsServerUrl` is configured, POST to {url}/tts and play the WAV.
 *   2. Otherwise fall back to the browser's built-in speechSynthesis API.
 *
 * Both paths strip markdown before speaking so output sounds natural.
 */

let currentAudio: HTMLAudioElement | null = null;

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

async function speakViaServer(text: string, serverUrl: string): Promise<void> {
  const url = `${serverUrl.replace(/\/$/, '')}/tts?${new URLSearchParams({ text })}`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`TTS server error: ${response.status}`);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);

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
}

async function speak(rawText: string): Promise<void> {
  const spoken = toSpokenText(rawText);
  if (!spoken) return;

  const { ttsServerUrl } = getSettings();
  if (ttsServerUrl) {
    try {
      await speakViaServer(spoken, ttsServerUrl);
      return;
    } catch (err) {
      console.warn('gptme-tts server unavailable, falling back to Web Speech API:', err);
    }
  }

  if (!window.speechSynthesis) return;
  // Cancel any previous utterance so a new message interrupts the old one.
  window.speechSynthesis.cancel();
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
