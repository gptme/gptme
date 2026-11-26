import { useState, useRef, useCallback, useEffect } from 'react';
import { PCMPlayer } from '@/audio/pcm-player';

export type VoiceState = 'idle' | 'connecting' | 'recording' | 'ended';

export interface UseVoiceSessionReturn {
  state: VoiceState;
  error: string | null;
  /** Level 0–1 from mic analyser (0 when not recording). */
  level: number;
  /** Start a call; *override* replaces the hook's target for this call only. */
  start: (override?: VoiceTarget) => void;
  stop: () => void;
  /** Flush the server's VAD input buffer (optional push-to-talk signal). */
  commit: () => void;
  /** Send a typed message into the call. Returns false when no call is live. */
  sendText: (text: string) => boolean;
}

interface Session {
  ws: WebSocket;
  player: PCMPlayer;
  audioCtx: AudioContext;
  stream: MediaStream;
  rafId: number;
}

/**
 * Where to connect: a fixed WebSocket URL, or a resolver called once per call
 * (e.g. to fetch a single-use ticket from the gptme server). Empty/null disables.
 */
export type VoiceTarget = string | (() => Promise<string>) | null;

function closeAudioContext(ctx: AudioContext | null | undefined): void {
  if (ctx && ctx.state !== 'closed') void ctx.close().catch(() => undefined);
}

export function useVoiceSession(target: VoiceTarget): UseVoiceSessionReturn {
  const [state, setState] = useState<VoiceState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState(0);

  const sessionRef = useRef<Session | null>(null);
  // Incremented on each start/stop call to cancel in-flight async setup.
  const setupGenRef = useRef(0);
  // Mic and audio context of a call that is still connecting (waiting for its
  // conversation or ticket), so stop() can release them at once instead of
  // when the pending request eventually settles.
  const pendingRef = useRef<{ stream: MediaStream; ctx: AudioContext | null } | null>(null);

  const releasePending = useCallback(() => {
    const pending = pendingRef.current;
    pendingRef.current = null;
    pending?.stream.getTracks().forEach((t) => t.stop());
    closeAudioContext(pending?.ctx);
  }, []);

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

  const start = useCallback(
    (override?: VoiceTarget) => {
      const callTarget = override ?? target;
      if (!callTarget || sessionRef.current) return;

      setError(null);
      setState('connecting');
      const gen = ++setupGenRef.current;

      void (async () => {
        // Declared outside try so catch can release them if setup fails
        // before sessionRef is assigned (cleanup() would be a no-op otherwise).
        let stream: MediaStream | null = null;
        let ctx: AudioContext | null = null;
        let player: PCMPlayer | null = null;
        // This setup's session once created; the shared sessionRef may already
        // belong to a newer call by the time this setup fails.
        let ownSession: Session | null = null;
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          // stop() may have been called while we were awaiting getUserMedia.
          if (setupGenRef.current !== gen) {
            stream.getTracks().forEach((t) => t.stop());
            return;
          }
          pendingRef.current = { stream, ctx: null };

          ctx = new AudioContext();
          if (pendingRef.current?.stream === stream) pendingRef.current.ctx = ctx;
          // Resume immediately — browsers may auto-suspend an AudioContext created
          // after an async getUserMedia() call (outside the user-gesture stack).
          if (ctx.state === 'suspended') await ctx.resume();
          if (setupGenRef.current !== gen) {
            stream.getTracks().forEach((t) => t.stop());
            closeAudioContext(ctx);
            return;
          }

          await ctx.audioWorklet.addModule('/pcm-recorder-worklet.js');
          if (setupGenRef.current !== gen) {
            stream.getTracks().forEach((t) => t.stop());
            closeAudioContext(ctx);
            return;
          }
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

          // Resolve after mic permission, so a slow permission prompt cannot
          // outlive a short-lived ticket.
          const url = typeof callTarget === 'string' ? callTarget : await callTarget();
          if (setupGenRef.current !== gen) {
            stream.getTracks().forEach((t) => t.stop());
            closeAudioContext(ctx);
            return;
          }

          player = new PCMPlayer(24000);
          const ws = new WebSocket(url);
          ws.binaryType = 'arraybuffer';

          // Stash session before async events can fire
          const session: Session = {
            ws,
            player: player!,
            audioCtx: ctx!,
            stream: stream!,
            rafId: 0,
          };
          sessionRef.current = session;
          ownSession = session;
          if (pendingRef.current?.stream === stream) pendingRef.current = null;

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
                } else if (msg.type === 'clear') {
                  // The user started talking over the reply: drop queued audio.
                  // ('audio_end' only means the provider finished streaming the
                  // reply, usually long before playback finishes, so it must not stop it.)
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
            // Guard against double-setState: onerror fires before onclose on errors,
            // and cleanup() already cleared sessionRef. Only act if session is still active.
            if (sessionRef.current === session) {
              setState('ended');
              cleanup();
            }
          };
        } catch (err) {
          if (ownSession && sessionRef.current === ownSession) {
            cleanup();
          } else {
            // Release only this setup's own mic, context and player: the shared
            // session (if any) belongs to a newer call, which must keep running.
            stream?.getTracks().forEach((t) => t.stop());
            player?.close();
            closeAudioContext(ctx);
            if (stream && pendingRef.current?.stream === stream) pendingRef.current = null;
          }
          // Don't update UI state if this setup was already cancelled by stop().
          if (setupGenRef.current === gen) {
            setError(err instanceof Error ? err.message : 'Voice setup failed');
            setState('ended');
          }
        }
      })();
    },
    [target, cleanup]
  );

  const stop = useCallback(() => {
    setupGenRef.current++; // cancel any in-flight async setup IIFE
    setState('ended');
    releasePending(); // a connecting call's mic must stop now, not when its request settles
    cleanup();
  }, [cleanup, releasePending]);

  const commit = useCallback(() => {
    const ws = sessionRef.current?.ws;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'commit' }));
    }
  }, []);

  const sendText = useCallback((text: string) => {
    const ws = sessionRef.current?.ws;
    if (ws?.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({ type: 'text', text }));
    return true;
  }, []);

  // Auto-reset ended → idle so the button becomes clickable again
  useEffect(() => {
    if (state !== 'ended') return;
    const t = setTimeout(() => setState('idle'), 1500);
    return () => clearTimeout(t);
  }, [state]);

  // Cleanup on unmount — call stop() not cleanup() so setupGenRef is incremented,
  // which cancels any in-flight async setup that is still awaiting getUserMedia/addModule.
  useEffect(() => () => stop(), [stop]);

  return { state, error, level, start, stop, commit, sendText };
}
