/**
 * PCMPlayer: buffers and schedules raw PCM16 LE playback at a fixed sample rate.
 *
 * Incoming binary frames from the voice server are raw Int16 mono PCM (no container).
 * We hand-build AudioBuffer objects (decodeAudioData is for container formats, not raw PCM)
 * and schedule them end-to-end so consecutive utterances never gap.
 */

// Extend Window to cover webkit-prefixed AudioContext
interface WindowWithWebkit extends Window {
  webkitAudioContext?: typeof AudioContext;
}

export class PCMPlayer {
  private ctx: AudioContext;
  private sampleRate: number;
  private nextStart: number;

  constructor(sampleRate = 24000) {
    const win = window as WindowWithWebkit;
    const AudioCtx = window.AudioContext ?? win.webkitAudioContext;
    if (!AudioCtx) throw new Error('AudioContext not supported');
    this.ctx = new AudioCtx();
    this.sampleRate = sampleRate;
    this.nextStart = 0;
  }

  /** Queue a raw PCM16 LE ArrayBuffer for gapless playback. */
  feed(buffer: ArrayBuffer): void {
    if (buffer.byteLength === 0) return;

    const int16 = new Int16Array(buffer);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 32768;
    }

    const audioBuffer = this.ctx.createBuffer(1, float32.length, this.sampleRate);
    audioBuffer.copyToChannel(float32, 0);

    const source = this.ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(this.ctx.destination);

    const now = this.ctx.currentTime;
    const when = Math.max(now + 0.01, this.nextStart); // 10 ms lookahead
    source.start(when);
    this.nextStart = when + audioBuffer.duration;
  }

  /** Resume suspended AudioContext (required after user gesture in some browsers). */
  async resume(): Promise<void> {
    if (this.ctx.state === 'suspended') {
      await this.ctx.resume();
    }
  }

  /** Reset scheduling cursor (call between utterances if needed). */
  reset(): void {
    this.nextStart = 0;
  }

  close(): void {
    void this.ctx.close();
  }

  get audioContext(): AudioContext {
    return this.ctx;
  }
}
