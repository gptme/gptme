import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { use$ } from '@legendapp/state/react';
import { AudioLines, AudioLinesOff, ChevronDown, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useApi } from '@/contexts/ApiContext';
import { serverRegistry$ } from '@/stores/servers';
import { isCallFor, useVoiceCall } from '@/contexts/VoiceCallContext';
import type { VoiceState } from '@/hooks/useVoiceSession';
import type { VoiceProviderStatus } from '@/types/api';

// While voice is unavailable (e.g. sidecars still starting), check again this often.
const UNAVAILABLE_REFETCH_MS = 15_000;

interface Props {
  /** Conversation the call is bound to. */
  conversationId?: string;
  /** Server owning the conversation; defaults to the primary server. */
  serverId?: string;
  /** New-chat view: create the conversation (and open it) when a call starts. */
  startConversation?: () => Promise<string>;
}

// Last-used provider is a per-device preference, not server configuration.
const PROVIDER_STORAGE_KEY = 'gptme_voice_provider';

function stateLabel(
  s: VoiceState,
  error: string | null,
  providerLabel: string | undefined,
  elsewhere: boolean
): string {
  if (error) return `Voice call error: ${error}`;
  switch (s) {
    case 'connecting':
      return 'Connecting voice call…';
    case 'recording':
      return elsewhere
        ? 'Voice call active in another chat. Click to end'
        : 'Voice call active. Click to end';
    case 'ended':
      return 'Ending voice call…';
    default:
      return providerLabel
        ? `Start realtime voice call (${providerLabel})`
        : 'Start realtime voice call';
  }
}

function readStoredProvider(): string | null {
  try {
    return localStorage.getItem(PROVIDER_STORAGE_KEY);
  } catch {
    return null;
  }
}

/**
 * Realtime voice call bound to the current conversation.
 *
 * Voice is configured on the server; the button only renders when the server
 * reports at least one working provider (GET /api/v2/voice).
 */
export function VoiceButton({ conversationId, serverId, startConversation }: Props) {
  const { getClient } = useApi();
  const client = getClient(serverId);
  // Record the concrete owning server, not "the primary": switching the active
  // server during a call must not make another server's same-named chat look
  // like the call's chat.
  const activeServerId = use$(serverRegistry$.activeServerId);
  const owner = serverId ?? activeServerId ?? null;
  const isConnected = use$(client.isConnected$);
  const { data: status } = useQuery({
    queryKey: ['voice-status', client.baseUrl],
    queryFn: () => client.getVoiceStatus(),
    enabled: isConnected,
    staleTime: 30_000,
    retry: false,
    refetchInterval: (query) => (query.state.data?.available ? false : UNAVAILABLE_REFETCH_MS),
  });

  const providers: VoiceProviderStatus[] = (status?.providers ?? []).filter((p) => p.available);
  const [chosen, setChosen] = useState<string | null>(readStoredProvider);
  const provider =
    providers.find((p) => p.id === chosen) ??
    providers.find((p) => p.id === status?.default_provider) ??
    providers[0];

  const call = useVoiceCall();
  const { state, error, level, stop } = call;

  if (!status?.available || !provider) return null;
  const target = conversationId ?? startConversation;
  if (!target) return null;

  const isActive = state === 'recording' || state === 'connecting';
  const isLoading = state === 'connecting';
  const here = isCallFor(call, conversationId, owner);
  const elsewhere = isActive && !!call.conversationId && !here;
  // An error belongs to the conversation whose call failed.
  const hasError = !!error && (!call.conversationId || here);
  const label = stateLabel(state, hasError ? error : null, provider.label, elsewhere);

  const handleClick = () => {
    if (isActive) stop();
    else call.start(target, { client, serverId: owner, provider: provider.id });
  };

  const chooseProvider = (id: string) => {
    setChosen(id);
    try {
      localStorage.setItem(PROVIDER_STORAGE_KEY, id);
    } catch {
      // storage unavailable: keep the choice for this session only
    }
  };

  // Mic level ring: scale from 1.0 to 1.3 based on audio level
  const ringScale = 1 + level * 0.3;

  return (
    <div className="flex items-center">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={handleClick}
            className={[
              'relative h-7 w-7 shrink-0 rounded-full transition-colors',
              hasError ? 'text-destructive' : '',
              isActive && !isLoading ? 'text-red-500 hover:text-red-600' : 'text-muted-foreground',
            ].join(' ')}
            aria-label={label}
            aria-pressed={isActive}
          >
            {/* Level ring behind the icon */}
            {state === 'recording' && !elsewhere && (
              <span
                className="absolute inset-0 rounded-full bg-red-500/20 transition-transform duration-75"
                style={{ transform: `scale(${ringScale})` }}
              />
            )}

            {isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : isActive ? (
              <AudioLines className="relative h-4 w-4" />
            ) : hasError ? (
              <AudioLinesOff className="h-4 w-4" />
            ) : (
              <AudioLines className="h-4 w-4" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="top">
          <p>{label}</p>
        </TooltipContent>
      </Tooltip>

      {providers.length > 1 && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              disabled={isActive}
              className="h-7 w-4 shrink-0 text-muted-foreground"
              aria-label="Choose voice provider"
            >
              <ChevronDown className="h-3 w-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>Voice provider</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={provider.id} onValueChange={chooseProvider}>
              {providers.map((p) => (
                <DropdownMenuRadioItem key={p.id} value={p.id}>
                  {p.label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}
