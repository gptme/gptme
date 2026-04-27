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
  private scheduledSources: AudioBufferSourceNode[] = [];

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
    this.scheduledSources.push(source);
    source.onended = () => {
      const idx = this.scheduledSources.indexOf(source);
      if (idx >= 0) this.scheduledSources.splice(idx, 1);
    };

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

  /** Stop in-flight audio and reset the scheduling cursor between utterances. */
  reset(): void {
    for (const src of this.scheduledSources) {
      try {
        src.stop();
      } catch {
        // already ended — ignore
      }
    }
    this.scheduledSources = [];
    this.nextStart = 0;
  }

  close(): void {
    void this.ctx.close();
  }

  get audioContext(): AudioContext {
    return this.ctx;
  }
}
