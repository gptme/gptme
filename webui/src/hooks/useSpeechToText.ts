import { useState, useRef, useCallback, useEffect } from 'react';
import { useApi } from '@/contexts/ApiContext';
import { useUserSettings } from '@/hooks/useUserSettings';

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

export function useSpeechToText(): UseSpeechToTextReturn {
  const { api } = useApi();
  const { settings } = useUserSettings();
  const [state, setState] = useState<STTState>('idle');
  const [interimTranscript, setInterimTranscript] = useState('');
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const recognitionRef = useRef<any>(null);
  const recordingRef = useRef<RecordingSession | null>(null);
  const fallbackSetupGenRef = useRef(0);
  const finalHandlerRef = useRef<((text: string) => void) | null>(null);
  const SpeechRecognitionClass = getSpeechRecognitionClass();
  const browserSupported = SpeechRecognitionClass !== null;
  const serverFallbackSupported =
    !browserSupported &&
    settings?.providers_configured.includes('openrouter') === true &&
    canUseRecordedFallback();
  const isSupported = browserSupported || serverFallbackSupported;

  const releaseRecordingSession = useCallback((session: RecordingSession | null) => {
    session?.stream.getTracks().forEach((track) => track.stop());
  }, []);

  const startListening = useCallback(() => {
    if (SpeechRecognitionClass) {
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

    if (!serverFallbackSupported || recordingRef.current) return;

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

          setState('transcribing');
          void api
            .transcribeAudio(audio, { language: getLanguageCode() })
            .then((result) => {
              const text = result.text.trim();
              if (text && finalHandlerRef.current) {
                finalHandlerRef.current(text);
              }
              setState('idle');
            })
            .catch((error: unknown) => {
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
  }, [SpeechRecognitionClass, api, releaseRecordingSession, serverFallbackSupported]);

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
