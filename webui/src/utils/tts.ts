/**
 * Text-to-speech using the browser's Web Speech API.
 *
 * Speaks assistant messages aloud when ttsEnabled is set in localStorage.
 * Strips markdown formatting and code blocks before speaking so output
 * sounds natural. Long messages are truncated to avoid endless monologues.
 */

const MAX_CHARS = 500;

function isEnabled(): boolean {
  try {
    const saved = localStorage.getItem('gptme-settings');
    if (saved) {
      const settings = JSON.parse(saved);
      return settings.ttsEnabled === true;
    }
  } catch {
    // ignore
  }
  return false;
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

function speak(rawText: string): void {
  if (!window.speechSynthesis) return;

  const spoken = toSpokenText(rawText);
  if (!spoken) return;

  const truncated = spoken.length > MAX_CHARS ? spoken.slice(0, MAX_CHARS) + '…' : spoken;

  // Cancel any previous utterance so a new message interrupts the old one.
  window.speechSynthesis.cancel();

  const utterance = new SpeechSynthesisUtterance(truncated);
  utterance.rate = 1.1;
  window.speechSynthesis.speak(utterance);
}

/** Speak text if the global TTS toggle is enabled (auto-play on new messages). */
export function speakText(rawText: string): void {
  if (!isEnabled()) return;
  speak(rawText);
}

/** Speak text immediately, regardless of the global TTS toggle (per-message button). */
export function speakTextNow(rawText: string): void {
  speak(rawText);
}

export function stopSpeaking(): void {
  if (window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
}

export function isSpeechSupported(): boolean {
  return 'speechSynthesis' in window;
}
