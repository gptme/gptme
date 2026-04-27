/**
 * AudioWorklet processor: downsamples mic input to 16 kHz PCM16 LE mono.
 *
 * Loaded at runtime via audioContext.audioWorklet.addModule('/pcm-recorder-worklet.js').
 * Must be plain JS (no ES module imports) — AudioWorklet scope has no module loader.
 */
class PCMRecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // ratio is computed on first process() call when sampleRate global is available
    this._ratio = null;
    this._targetRate = 16000;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const samples = input[0]; // Float32Array at AudioContext native rate

    if (this._ratio === null) {
      // sampleRate is a global in AudioWorklet scope
      this._ratio = sampleRate / this._targetRate;
    }

    // Simple nearest-neighbor downsampling (acceptable quality for speech)
    const outputLength = Math.floor(samples.length / this._ratio);
    if (outputLength === 0) return true;

    const output = new Int16Array(outputLength);
    for (let i = 0; i < outputLength; i++) {
      const srcIdx = Math.min(Math.round(i * this._ratio), samples.length - 1);
      const s = Math.max(-1, Math.min(1, samples[srcIdx]));
      output[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
    }

    this.port.postMessage(output.buffer, [output.buffer]);
    return true;
  }
}

registerProcessor('pcm-recorder-processor', PCMRecorderProcessor);
