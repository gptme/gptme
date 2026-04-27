import { Mic, MicOff, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useVoiceSession, type VoiceState } from '@/hooks/useVoiceSession';

interface Props {
  voiceServerUrl: string;
}

function stateLabel(s: VoiceState, error: string | null): string {
  if (error) return `Voice error: ${error}`;
  switch (s) {
    case 'connecting':
      return 'Connecting to voice server…';
    case 'recording':
      return 'Recording — click to stop';
    case 'ended':
      return 'Ending session…';
    default:
      return 'Start voice session';
  }
}

export function VoiceButton({ voiceServerUrl }: Props) {
  const { state, error, level, start, stop } = useVoiceSession(voiceServerUrl);

  const isActive = state === 'recording' || state === 'connecting';
  const isLoading = state === 'connecting';
  const hasError = !!error;

  const handleClick = () => {
    if (isActive) stop();
    else start();
  };

  // Mic level ring: scale from 1.0 to 1.3 based on audio level
  const ringScale = 1 + level * 0.3;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={handleClick}
          className={[
            'relative h-8 w-8 shrink-0 rounded-full transition-colors',
            hasError ? 'text-destructive' : '',
            state === 'recording' ? 'text-red-500 hover:text-red-600' : 'text-muted-foreground',
          ].join(' ')}
          aria-label={stateLabel(state, error)}
          aria-pressed={isActive}
        >
          {/* Level ring behind the icon */}
          {state === 'recording' && (
            <span
              className="absolute inset-0 rounded-full bg-red-500/20 transition-transform duration-75"
              style={{ transform: `scale(${ringScale})` }}
            />
          )}

          {isLoading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : isActive ? (
            <Mic className="relative h-4 w-4" />
          ) : hasError ? (
            <MicOff className="h-4 w-4" />
          ) : (
            <Mic className="h-4 w-4" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="top">
        <p>{stateLabel(state, error)}</p>
      </TooltipContent>
    </Tooltip>
  );
}
