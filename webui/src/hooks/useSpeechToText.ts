import { useState, useRef, useCallback, useEffect } from 'react';

export type STTState = 'idle' | 'listening' | 'error';

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

export function useSpeechToText(): UseSpeechToTextReturn {
  const [state, setState] = useState<STTState>('idle');
  const [interimTranscript, setInterimTranscript] = useState('');
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const recognitionRef = useRef<any>(null);
  const finalHandlerRef = useRef<((text: string) => void) | null>(null);
  const SpeechRecognitionClass = getSpeechRecognitionClass();
  const isSupported = SpeechRecognitionClass !== null;

  const startListening = useCallback(() => {
    if (!SpeechRecognitionClass) return;
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
      if (recognitionRef.current !== recognition) return; // stale closure guard
      setState('error');
      recognitionRef.current = null;
      setInterimTranscript('');
    };

    recognition.onend = () => {
      if (recognitionRef.current !== recognition) return; // stale closure guard
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
  }, [SpeechRecognitionClass]);

  const stopListening = useCallback(() => {
    if (recognitionRef.current) {
      recognitionRef.current.stop();
      recognitionRef.current = null;
    }
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
    },
    []
  );

  return { state, isSupported, interimTranscript, startListening, stopListening, onFinalResult };
}
