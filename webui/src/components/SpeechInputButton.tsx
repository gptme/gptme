import { Mic, MicOff, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useEffect } from 'react';
import { useSpeechToText } from '@/hooks/useSpeechToText';

interface Props {
  /** Called with each finalized speech segment; caller appends to textarea */
  onTranscript: (text: string) => void;
  /** Called when interim (unstable) transcript changes */
  onInterimTranscript?: (text: string) => void;
  disabled?: boolean;
}

export function SpeechInputButton({ onTranscript, onInterimTranscript, disabled }: Props) {
  const { state, isSupported, interimTranscript, startListening, stopListening, onFinalResult } =
    useSpeechToText();

  const isListening = state === 'listening';
  const isTranscribing = state === 'transcribing';

  useEffect(() => {
    onFinalResult(onTranscript);
  }, [onFinalResult, onTranscript]);

  useEffect(() => {
    onInterimTranscript?.(interimTranscript);
  }, [interimTranscript, onInterimTranscript]);

  // Stop mic when button becomes disabled (e.g. during response generation)
  useEffect(() => {
    if (disabled && isListening) stopListening();
  }, [disabled, isListening, stopListening]);

  if (!isSupported) return null;
  const isError = state === 'error';

  const handleClick = () => {
    if (isTranscribing) return;
    if (isListening) stopListening();
    else startListening();
  };

  const label = isError
    ? 'Mic error — click to retry'
    : isTranscribing
      ? 'Transcribing…'
      : isListening
        ? 'Listening… click to stop'
        : 'Dictate message (speech-to-text)';

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={handleClick}
          disabled={disabled || isTranscribing}
          className={[
            'relative h-7 w-7 shrink-0 rounded-full transition-colors',
            isError
              ? 'text-destructive'
              : isListening || isTranscribing
                ? 'text-blue-500 hover:text-blue-600'
                : 'text-muted-foreground',
          ].join(' ')}
          aria-label={label}
          aria-pressed={isListening}
        >
          {/* Pulse ring while listening */}
          {isListening && (
            <span className="absolute inset-0 animate-ping rounded-full bg-blue-500/20" />
          )}
          {state === 'error' ? (
            <MicOff className="h-4 w-4" />
          ) : isListening || isTranscribing ? (
            <Loader2 className="relative h-4 w-4 animate-spin" />
          ) : (
            <Mic className="h-4 w-4" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="top">
        <p>{label}</p>
      </TooltipContent>
    </Tooltip>
  );
}
