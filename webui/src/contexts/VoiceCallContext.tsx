import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useVoiceSession, type VoiceState } from '@/hooks/useVoiceSession';
import type { IApiClient } from '@/utils/api';

/**
 * The app's single realtime voice call.
 *
 * Lives above the router so a call keeps running while the user navigates,
 * e.g. when starting a call from the new-chat page creates a conversation and
 * opens it.
 */
interface VoiceCallContextValue {
  state: VoiceState;
  error: string | null;
  level: number;
  /** Conversation the current (or last) call is bound to. */
  conversationId: string | null;
  /** Server owning that conversation (null = the primary server). */
  serverId: string | null;
  provider: string | null;
  /**
   * Start a call bound to *conversationId* (or the id it resolves to) on the
   * server *client* talks to.
   */
  start: (
    conversationId: string | (() => Promise<string>),
    options: { client: IApiClient; serverId?: string | null; provider?: string }
  ) => void;
  stop: () => void;
  /** Send a typed message into the live call; false if there is none. */
  sendText: (text: string) => boolean;
}

const VoiceCallContext = createContext<VoiceCallContextValue | null>(null);

export function VoiceCallProvider({ children }: { children: ReactNode }) {
  const session = useVoiceSession(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [serverId, setServerId] = useState<string | null>(null);
  const [provider, setProvider] = useState<string | null>(null);
  // Bumped by every start/stop. A call's async setup only updates the call's
  // identity while it is still the latest one, so a slow, superseded setup
  // cannot re-bind the current call to its conversation.
  const callGenRef = useRef(0);

  const { start: startSession, stop: stopSession, sendText } = session;
  const start = useCallback<VoiceCallContextValue['start']>(
    (target, { client, serverId: owner = null, provider: chosenProvider }) => {
      const gen = ++callGenRef.current;
      setServerId(owner);
      setProvider(chosenProvider ?? null);
      setConversationId(typeof target === 'string' ? target : null);
      startSession(async () => {
        const id = typeof target === 'string' ? target : await target();
        if (callGenRef.current !== gen) throw new Error('voice call superseded');
        setConversationId(id);
        const ticket = await client.createVoiceSession(id, chosenProvider);
        if (callGenRef.current !== gen) throw new Error('voice call superseded');
        setProvider(ticket.provider);
        return client.voiceWebSocketUrl(ticket);
      });
    },
    [startSession]
  );

  const stop = useCallback(() => {
    callGenRef.current++;
    stopSession();
  }, [stopSession]);

  const value = useMemo<VoiceCallContextValue>(
    () => ({
      state: session.state,
      error: session.error,
      level: session.level,
      conversationId,
      serverId,
      provider,
      start,
      stop,
      sendText,
    }),
    [
      session.state,
      session.error,
      session.level,
      conversationId,
      serverId,
      provider,
      start,
      stop,
      sendText,
    ]
  );

  return <VoiceCallContext.Provider value={value}>{children}</VoiceCallContext.Provider>;
}

/** The voice call, or null outside VoiceCallProvider (tests, embedded panels). */
export function useOptionalVoiceCall(): VoiceCallContextValue | null {
  return useContext(VoiceCallContext);
}

export function useVoiceCall(): VoiceCallContextValue {
  const ctx = useContext(VoiceCallContext);
  if (!ctx) throw new Error('useVoiceCall must be used inside VoiceCallProvider');
  return ctx;
}

/** Whether the live (or connecting) call belongs to this conversation on this server. */
export function isCallFor(
  call: Pick<VoiceCallContextValue, 'conversationId' | 'serverId'> | null,
  conversationId: string | undefined,
  serverId: string | undefined | null
): boolean {
  return (
    !!call &&
    !!conversationId &&
    call.conversationId === conversationId &&
    (call.serverId ?? null) === (serverId ?? null)
  );
}
