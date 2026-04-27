import { useState, useRef, useCallback, useEffect } from 'react';
import { PCMPlayer } from '@/audio/pcm-player';

export type VoiceState = 'idle' | 'connecting' | 'recording' | 'ended';

export interface UseVoiceSessionReturn {
  state: VoiceState;
  error: string | null;
  /** Level 0–1 from mic analyser (0 when not recording). */
  level: number;
  start: () => void;
  stop: () => void;
  /** Flush the server's VAD input buffer (optional push-to-talk signal). */
  commit: () => void;
}

interface Session {
  ws: WebSocket;
  player: PCMPlayer;
  audioCtx: AudioContext;
  stream: MediaStream;
  rafId: number;
}

export function useVoiceSession(voiceServerUrl: string): UseVoiceSessionReturn {
  const [state, setState] = useState<VoiceState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState(0);

  const sessionRef = useRef<Session | null>(null);

  const cleanup = useCallback(() => {
    const s = sessionRef.current;
    if (!s) return;
    sessionRef.current = null;

    cancelAnimationFrame(s.rafId);
    s.stream.getTracks().forEach((t) => t.stop());
    s.ws.onopen = null;
    s.ws.onmessage = null;
    s.ws.onerror = null;
    s.ws.onclose = null;
    if (s.ws.readyState <= WebSocket.OPEN) s.ws.close();
    s.player.close();
    void s.audioCtx.close();
    setLevel(0);
  }, []);

  const start = useCallback(() => {
    if (!voiceServerUrl || sessionRef.current) return;

    setError(null);
    setState('connecting');

    void (async () => {
      // Declared outside try so catch can release them if setup fails
      // before sessionRef is assigned (cleanup() would be a no-op otherwise).
      let stream: MediaStream | null = null;
      let ctx: AudioContext | null = null;
      let player: PCMPlayer | null = null;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        ctx = new AudioContext();
        // Resume immediately — browsers may auto-suspend an AudioContext created
        // after an async getUserMedia() call (outside the user-gesture stack).
        if (ctx.state === 'suspended') await ctx.resume();

        await ctx.audioWorklet.addModule('/pcm-recorder-worklet.js');
        const workletNode = new AudioWorkletNode(ctx, 'pcm-recorder-processor');

        // Level meter
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;

        // Route mic → analyser + worklet; mute to speakers
        const silentGain = ctx.createGain();
        silentGain.gain.value = 0;
        const micSource = ctx.createMediaStreamSource(stream);
        micSource.connect(analyser);
        micSource.connect(workletNode);
        workletNode.connect(silentGain);
        silentGain.connect(ctx.destination);

        player = new PCMPlayer(24000);
        const ws = new WebSocket(voiceServerUrl);
        ws.binaryType = 'arraybuffer';

        // Stash session before async events can fire
        const session: Session = { ws, player: player!, audioCtx: ctx!, stream: stream!, rafId: 0 };
        sessionRef.current = session;

        ws.onmessage = (evt) => {
          if (typeof evt.data === 'string') {
            try {
              const msg = JSON.parse(evt.data) as { type: string };
              if (msg.type === 'ready') {
                void session.player.resume();
                setState('recording');

                // Wire worklet output → WebSocket
                workletNode.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
                  if (ws.readyState === WebSocket.OPEN) ws.send(e.data);
                };

                // Start level meter loop
                const levelBuf = new Uint8Array(analyser.frequencyBinCount);
                const tick = () => {
                  analyser.getByteFrequencyData(levelBuf);
                  const avg = levelBuf.reduce((sum, v) => sum + v, 0) / levelBuf.length;
                  setLevel(Math.min(1, avg / 64));
                  const id = requestAnimationFrame(tick);
                  if (sessionRef.current === session) session.rafId = id;
                };
                session.rafId = requestAnimationFrame(tick);
              } else if (msg.type === 'audio_end') {
                session.player.reset();
              }
            } catch {
              // non-JSON control frame — ignore
            }
          } else {
            // Binary: raw PCM from the model
            session.player.feed(evt.data as ArrayBuffer);
          }
        };

        ws.onerror = () => {
          setError('Voice connection error');
          setState('ended');
          cleanup();
        };

        ws.onclose = () => {
          setState('ended');
          cleanup();
        };
      } catch (err) {
        // If setup failed before sessionRef was assigned, cleanup() is a no-op;
        // release mic, recording context, and player explicitly to prevent leaks.
        if (!sessionRef.current) {
          stream?.getTracks().forEach((t) => t.stop());
          player?.close();
          void ctx?.close();
        }
        setError(err instanceof Error ? err.message : 'Voice setup failed');
        setState('ended');
        cleanup();
      }
    })();
  }, [voiceServerUrl, cleanup]);

  const stop = useCallback(() => {
    setState('ended');
    cleanup();
  }, [cleanup]);

  const commit = useCallback(() => {
    const ws = sessionRef.current?.ws;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'commit' }));
    }
  }, []);

  // Auto-reset ended → idle so the button becomes clickable again
  useEffect(() => {
    if (state !== 'ended') return;
    const t = setTimeout(() => setState('idle'), 1500);
    return () => clearTimeout(t);
  }, [state]);

  // Cleanup on unmount
  useEffect(() => () => cleanup(), [cleanup]);

  return { state, error, level, start, stop, commit };
}
