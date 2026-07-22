/**
 * Standalone external-session detail panel — renders the transcript for one
 * external session (Claude Code, Codex, etc.) and exposes an optional
 * steer_inject input when the session advertises that capability.
 *
 * Used in two contexts:
 *   - The dedicated /external-sessions page (two-panel catalog view)
 *   - Inline inside the native chat layout when a session is selected from the
 *     sidebar (the "native chat view" integration introduced in gptme#3217)
 */

import { type FC, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { AlertCircle, Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useApi } from '@/contexts/ApiContext';
import { use$ } from '@legendapp/state/react';
import { SessionReplayMessages } from '@/components/SessionReplayMessages';

interface Props {
  sessionId: string;
}

export const ExternalSessionDetail: FC<Props> = ({ sessionId }) => {
  const { api } = useApi();
  const isConnected = use$(api.isConnected$);
  const [steerMessage, setSteerMessage] = useState('');

  const { data, isLoading, error } = useQuery({
    queryKey: ['external-session-detail', sessionId],
    queryFn: () => api.getExternalSession(sessionId),
    enabled: isConnected && !!sessionId,
  });

  const steerMutation = useMutation({
    mutationFn: (message: string) => api.steerExternalSession(sessionId, message),
    onSuccess: () => setSteerMessage(''),
  });

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        Loading session…
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
        <AlertCircle className="h-8 w-8" />
        <p className="text-sm">Failed to load session details</p>
      </div>
    );
  }

  const messages = Array.isArray(data.transcript.messages) ? data.transcript.messages : null;
  const capabilities = Array.isArray(data.transcript.capabilities)
    ? (data.transcript.capabilities as string[])
    : [];
  const canSteer = capabilities.includes('steer_inject');

  const handleSteer = () => {
    const msg = steerMessage.trim();
    if (!msg || steerMutation.isPending) return;
    steerMutation.mutate(msg);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto p-4">
        {messages ? (
          <SessionReplayMessages messages={messages} />
        ) : (
          <pre className="whitespace-pre-wrap rounded-md bg-muted p-3 text-xs">
            {JSON.stringify(data.transcript, null, 2)}
          </pre>
        )}
      </div>
      {canSteer && (
        <div className="border-t p-3">
          <div className="flex gap-2">
            <Textarea
              className="min-h-[2.5rem] flex-1 resize-none text-sm"
              placeholder="Steer session — inject a user message…"
              value={steerMessage}
              onChange={(e) => setSteerMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSteer();
                }
              }}
              rows={2}
              aria-label="Steer message"
            />
            <Button
              size="icon"
              className="h-auto self-end"
              onClick={handleSteer}
              disabled={!steerMessage.trim() || steerMutation.isPending}
              aria-label="Send steer message"
            >
              <Send className="h-4 w-4" />
            </Button>
          </div>
          {steerMutation.isError && (
            <p className="mt-1 text-xs text-destructive">
              {steerMutation.error instanceof Error
                ? steerMutation.error.message
                : 'Failed to send message'}
            </p>
          )}
          {steerMutation.isSuccess && (
            <p className="mt-1 text-xs text-muted-foreground">Message injected.</p>
          )}
        </div>
      )}
    </div>
  );
};
