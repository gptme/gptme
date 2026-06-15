import { useState, useRef, useCallback, useEffect } from 'react';
import { useApi } from '@/contexts/ApiContext';
import { useUserSettings } from '@/hooks/useUserSettings';
import { useSettings } from '@/contexts/SettingsContext';

export type STTState = 'idle' | 'listening' | 'transcribing' | 'error';

export interface UseSpeechToTextReturn {
  state: STTState;
  isSupported: boolean;
  interimTranscript: string;
  startListening: () => void;
  stopListening: () => void;
  onFinalResult: (handler: (text: string) => void) => void;
}

// SpeechRecognition is not in TypeScript's standard DOM lib on all targets.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyWindow = Window & { SpeechRecognition?: any; webkitSpeechRecognition?: any };

interface RecordingSession {
  chunks: Blob[];
  mediaRecorder: MediaRecorder;
  mimeType: string;
  skipTranscription: boolean;
  stream: MediaStream;
}

const getSpeechRecognitionClass = ():
  | (new () => {
      continuous: boolean;
      interimResults: boolean;
      lang: string;

      onstart: (() => void) | null;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      onresult: ((event: any) => void) | null;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      onerror: ((event: any) => void) | null;
      onend: (() => void) | null;
      start: () => void;
      stop: () => void;
      abort: () => void;
    })
  | null => {
  if (typeof window === 'undefined') return null;
  const w = window as AnyWindow;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
};

function canUseRecordedFallback(): boolean {
  return (
    typeof MediaRecorder !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    typeof navigator.mediaDevices?.getUserMedia === 'function'
  );
}

function getPreferredRecorderMimeType(): string {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/ogg',
    'audio/mp4',
  ];
  for (const candidate of candidates) {
    if (
      typeof MediaRecorder.isTypeSupported === 'function' &&
      MediaRecorder.isTypeSupported(candidate)
    ) {
      return candidate;
    }
  }
  return 'audio/webm';
}

function getLanguageCode(): string | undefined {
  const locale = navigator.language?.trim();
  if (!locale) return undefined;
  return locale.split('-', 1)[0]?.toLowerCase() || undefined;
}

/**
 * Transcribe audio via a same-origin cloud endpoint (e.g. a Cloudflare Pages
 * Function proxy to the billing-aware llm-proxy). Used when `sttAuthToken` is
 * set by a cloud host (e.g. gptme.ai) so STT is billed to the user's account.
 */
async function transcribeViaCloudEndpoint(
  audio: Blob,
  authToken: string,
  options?: { language?: string; signal?: AbortSignal }
): Promise<string> {
  const format = audio.type.split(';', 1)[0].split('/')[1] || 'webm';
  const formData = new FormData();
  formData.append('file', audio, `speech.${format}`);
  formData.append('format', format);
  if (options?.language) {
    formData.append('language', options.language);
  }
  const response = await fetch('/api/v2/audio/transcriptions', {
    method: 'POST',
    headers: { Authorization: `Bearer ${authToken}` },
    body: formData,
    signal: options?.signal,
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(
      (err as { error?: { message?: string } }).error?.message ??
        `Transcription failed: ${response.status}`
    );
  }
  const data = (await response.json()) as { text: string };
  return data.text;
}

export function useSpeechToText(): UseSpeechToTextReturn {
  const { api } = useApi();
  const { settings: userSettings } = useUserSettings();
  const { settings: clientSettings } = useSettings();
  const [state, setState] = useState<STTState>('idle');
  const [interimTranscript, setInterimTranscript] = useState('');
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const recognitionRef = useRef<any>(null);
  const recordingRef = useRef<RecordingSession | null>(null);
  const fallbackSetupGenRef = useRef(0);
  const finalHandlerRef = useRef<((text: string) => void) | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const sttAuthTokenRef = useRef(clientSettings.sttAuthToken);
  const SpeechRecognitionClass = getSpeechRecognitionClass();
  const browserSupported = SpeechRecognitionClass !== null;
  // Cloud hosts set sttAuthToken so the same-origin /api/v2/audio/transcriptions
  // proxy (e.g. Cloudflare Pages Function) handles billing — no OpenRouter key needed.
  const cloudSttConfigured = !!clientSettings.sttAuthToken && canUseRecordedFallback();
  const serverConfigured =
    (userSettings?.providers_configured.includes('openrouter') === true || cloudSttConfigured) &&
    canUseRecordedFallback();
  const prefersServerStt = clientSettings.sttProvider === 'server';
  const serverFallbackSupported = !browserSupported && serverConfigured;
  // When user explicitly chooses server mode, use server STT even if browser supports it
  const serverEnabled = serverConfigured && (prefersServerStt || serverFallbackSupported);
  const isSupported = browserSupported || serverConfigured;

  useEffect(() => {
    sttAuthTokenRef.current = clientSettings.sttAuthToken;
  }, [clientSettings.sttAuthToken]);

  const releaseRecordingSession = useCallback((session: RecordingSession | null) => {
    session?.stream.getTracks().forEach((track) => track.stop());
  }, []);

  const startListening = useCallback(() => {
    // When user explicitly chooses server STT, skip browser path
    if (SpeechRecognitionClass && !prefersServerStt) {
      if (recognitionRef.current) return;

      const recognition = new SpeechRecognitionClass();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = navigator.language || 'en-US';

      recognition.onstart = () => setState('listening');

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      recognition.onresult = (event: any) => {
        let interim = '';
        let final = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (result.isFinal) {
            final += result[0].transcript;
          } else {
            interim += result[0].transcript;
          }
        }
        setInterimTranscript(interim);
        if (final && finalHandlerRef.current) {
          finalHandlerRef.current(final);
        }
      };

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      recognition.onerror = (event: any) => {
        console.warn('SpeechRecognition error:', event.error);
        if (recognitionRef.current !== recognition) return;
        setState('error');
        recognitionRef.current = null;
        setInterimTranscript('');
      };

      recognition.onend = () => {
        if (recognitionRef.current !== recognition) return;
        recognitionRef.current = null;
        setState('idle');
        setInterimTranscript('');
      };

      recognitionRef.current = recognition;
      try {
        recognition.start();
      } catch {
        recognitionRef.current = null;
        setState('error');
      }
      return;
    }

    if (recordingRef.current) return;
    if (!serverEnabled) {
      // User explicitly chose server mode but server is not configured
      if (prefersServerStt) setState('error');
      return;
    }

    setInterimTranscript('');
    setState('listening');
    const gen = ++fallbackSetupGenRef.current;

    void (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (fallbackSetupGenRef.current !== gen) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }

        const mimeType = getPreferredRecorderMimeType();
        const mediaRecorder = new MediaRecorder(stream, { mimeType });
        const session: RecordingSession = {
          chunks: [],
          mediaRecorder,
          mimeType,
          skipTranscription: false,
          stream,
        };
        recordingRef.current = session;

        mediaRecorder.ondataavailable = (event: BlobEvent) => {
          if (event.data.size > 0) {
            session.chunks.push(event.data);
          }
        };

        mediaRecorder.onerror = () => {
          if (recordingRef.current !== session) return;
          recordingRef.current = null;
          releaseRecordingSession(session);
          setInterimTranscript('');
          setState('error');
        };

        mediaRecorder.onstop = () => {
          if (recordingRef.current !== session) return;

          recordingRef.current = null;
          releaseRecordingSession(session);
          setInterimTranscript('');

          if (session.skipTranscription) {
            abortControllerRef.current?.abort();
            setState('idle');
            return;
          }

          const audio = new Blob(session.chunks, {
            type: mediaRecorder.mimeType || session.mimeType || 'audio/webm',
          });
          if (audio.size === 0) {
            setState('error');
            return;
          }

          // Cancel any previous in-flight transcription before starting a new one
          abortControllerRef.current?.abort();
          const controller = new AbortController();
          abortControllerRef.current = controller;

          setState('transcribing');
          const sttAuthToken = sttAuthTokenRef.current;
          const transcribePromise: Promise<string> = sttAuthToken
            ? transcribeViaCloudEndpoint(audio, sttAuthToken, {
                language: getLanguageCode() ?? undefined,
                signal: controller.signal,
              })
            : api
                .transcribeAudio(audio, {
                  language: getLanguageCode(),
                  signal: controller.signal,
                })
                .then((r) => r.text);
          void transcribePromise
            .then((text) => {
              const trimmed = text.trim();
              if (trimmed && finalHandlerRef.current) {
                finalHandlerRef.current(trimmed);
              }
              setState('idle');
            })
            .catch((error: unknown) => {
              if (error instanceof DOMException && error.name === 'AbortError') return;
              console.warn('Server transcription failed:', error);
              setState('error');
            });
        };

        mediaRecorder.start();
      } catch (error) {
        console.warn('Recorded speech-to-text setup failed:', error);
        if (fallbackSetupGenRef.current === gen) {
          recordingRef.current = null;
          setInterimTranscript('');
          setState('error');
        }
      }
    })();
  }, [SpeechRecognitionClass, api, releaseRecordingSession, serverEnabled, prefersServerStt]);

  const stopListening = useCallback(() => {
    if (recognitionRef.current) {
      recognitionRef.current.stop();
      recognitionRef.current = null;
      setState('idle');
      setInterimTranscript('');
      return;
    }

    if (recordingRef.current) {
      recordingRef.current.mediaRecorder.stop();
      return;
    }

    fallbackSetupGenRef.current++;
    setState('idle');
    setInterimTranscript('');
  }, []);

  const onFinalResult = useCallback((handler: (text: string) => void) => {
    finalHandlerRef.current = handler;
  }, []);

  // Auto-reset error state after 1.5s
  useEffect(() => {
    if (state !== 'error') return;
    const t = setTimeout(() => setState('idle'), 1500);
    return () => clearTimeout(t);
  }, [state]);

  // Cleanup on unmount
  useEffect(
    () => () => {
      abortControllerRef.current?.abort();

      if (recognitionRef.current) {
        recognitionRef.current.abort();
        recognitionRef.current = null;
      }

      fallbackSetupGenRef.current++;
      if (recordingRef.current) {
        recordingRef.current.skipTranscription = true;
        if (recordingRef.current.mediaRecorder.state !== 'inactive') {
          recordingRef.current.mediaRecorder.stop();
        }
        releaseRecordingSession(recordingRef.current);
        recordingRef.current = null;
      }
    },
    [releaseRecordingSession]
  );

  return { state, isSupported, interimTranscript, startListening, stopListening, onFinalResult };
}
