import { PCMPlayer } from './pcm-player';

class MockAudioBuffer {
  duration: number;
  copiedChannel: Float32Array | null = null;

  constructor(
    public channels: number,
    public length: number,
    public sampleRate: number
  ) {
    this.duration = length / sampleRate;
  }

  copyToChannel(data: Float32Array): void {
    this.copiedChannel = data;
  }
}

class MockAudioBufferSourceNode {
  buffer: MockAudioBuffer | null = null;
  onended: (() => void) | null = null;
  startTime: number | null = null;
  stopped = false;

  connect = jest.fn();

  start(when: number): void {
    this.startTime = when;
  }

  stop(): void {
    this.stopped = true;
  }
}

class MockAudioContext {
  state: AudioContextState = 'suspended';
  currentTime = 1;
  destination = {} as AudioDestinationNode;
  buffers: MockAudioBuffer[] = [];
  sources: MockAudioBufferSourceNode[] = [];
  closed = false;

  createBuffer(channels: number, length: number, sampleRate: number): MockAudioBuffer {
    const buffer = new MockAudioBuffer(channels, length, sampleRate);
    this.buffers.push(buffer);
    return buffer;
  }

  createBufferSource(): MockAudioBufferSourceNode {
    const source = new MockAudioBufferSourceNode();
    this.sources.push(source);
    return source;
  }

  async resume(): Promise<void> {
    this.state = 'running';
  }

  async close(): Promise<void> {
    this.closed = true;
  }
}

function pcm16Buffer(samples: number[]): ArrayBuffer {
  return new Int16Array(samples).buffer;
}

describe('PCMPlayer', () => {
  let originalAudioContext: typeof window.AudioContext;
  let contexts: MockAudioContext[];

  beforeEach(() => {
    originalAudioContext = window.AudioContext;
    contexts = [];

    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      writable: true,
      value: jest.fn(() => {
        const ctx = new MockAudioContext();
        contexts.push(ctx);
        return ctx;
      }),
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      writable: true,
      value: originalAudioContext,
    });
  });

  it('converts raw PCM16 LE samples into a mono AudioBuffer', () => {
    const player = new PCMPlayer(24000);

    player.feed(pcm16Buffer([-32768, 0, 32767]));

    const ctx = contexts[0];
    expect(ctx.buffers).toHaveLength(1);
    expect(ctx.buffers[0].channels).toBe(1);
    expect(ctx.buffers[0].length).toBe(3);
    expect(ctx.buffers[0].sampleRate).toBe(24000);
    expect(Array.from(ctx.buffers[0].copiedChannel ?? [])).toEqual([-1, 0, 32767 / 32768]);
  });

  it('schedules consecutive chunks without resetting between feed calls', () => {
    const player = new PCMPlayer(24000);

    player.feed(pcm16Buffer([1, 2, 3]));
    player.feed(pcm16Buffer([4, 5]));

    const [first, second] = contexts[0].sources;
    expect(first.startTime).toBeCloseTo(1.01, 5);
    expect(second.startTime).toBeCloseTo(1.01 + 3 / 24000, 5);
  });

  it('stops scheduled chunks and resets the scheduling cursor', () => {
    const player = new PCMPlayer(24000);

    player.feed(pcm16Buffer([1, 2, 3]));
    player.feed(pcm16Buffer([4, 5]));
    player.reset();
    player.feed(pcm16Buffer([6]));

    const [first, second, third] = contexts[0].sources;
    expect(first.stopped).toBe(true);
    expect(second.stopped).toBe(true);
    expect(third.startTime).toBeCloseTo(1.01, 5);
  });

  it('resumes the playback context on demand', async () => {
    const player = new PCMPlayer(24000);

    await player.resume();

    expect(contexts[0].state).toBe('running');
  });
});
