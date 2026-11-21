import { useCallback, useEffect, useRef, useState } from 'react';
import { useApi } from '@/contexts/ApiContext';
import { useToast } from '@/components/ui/use-toast';
import type { Message, StreamingMessage } from '@/types/conversation';
import type { ChatOptions } from '@/components/ChatInput';
import { DEMO_AGENT, demoConversations, getDemoMessages } from '@/democonversations';
import { isDemoMode } from '@/utils/connectionConfig';
import { serverRegistry$ } from '@/stores/servers';
import { getClientForServer } from '@/stores/serverClients';
import { use$, useObservable } from '@legendapp/state/react';
import {
  conversations$,
  updateConversation,
  updateConversationData,
  setGenerating,
  setConnected,
  setConnectionStatus,
  setPendingTool,
  setExecutingTool,
  setToolOutput,
  setToolComplete,
  addMessage,
  setMessageStatus,
  removeMessage,
  replaceLog,
  updateBranches,
  setCurrentBranch,
  initConversation,
  selectedConversation$,
  updateConversationName,
  clearInitialStepState,
  setMaxTokens,
  setTemperature,
  setTopP,
  prependLogPage,
} from '@/stores/conversations';
import { playChime } from '@/utils/audio';
import { speakText } from '@/utils/tts';
import { findLatestAssistantIndexForError } from '@/utils/conversationErrorHandling';
import { notifyGenerationComplete, notifyToolConfirmation } from '@/utils/notifications';
import {
  ApiClientError,
  AttachmentUploadError,
  getApiErrorPresentation,
  getClientGeneration,
  sameLogMessage,
} from '@/utils/api';
import { toastStepStartError } from '@/utils/stepErrorHandling';

const MAX_CONNECTED_CONVERSATIONS = 3;

// Delays between step() retries when the server still holds the generating
// reservation (409) after the message is persisted.
const STEP_409_BACKOFF_MS = [250, 500, 1000, 2000];

export function useConversation(conversationId: string, serverId?: string) {
  const { getClient, isConnected$ } = useApi();
  // Use the client for the specific server, or primary if no serverId
  const api = getClient(serverId);
  const { toast } = useToast();
  const conversation$ = conversations$.get(conversationId);
  const isConnected = use$(isConnected$);
  const maxTokens = use$(() => conversation$?.maxTokens.get());
  const temperature = use$(() => conversation$?.temperature.get());
  const topP = use$(() => conversation$?.topP.get());

  const messageJustCompleted = useRef(false);
  // Timer ID for the generation_complete 100ms fallback; cleared by step_complete
  const generationCompleteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Last completed message content; used by onStepComplete for chime/TTS/notification
  const lastCompletedMessageRef = useRef<Message | null>(null);
  // Stop during the new-chat handshake: onConnected / onMessageStart must not
  // restart generation after the user already cancelled the pending initial step.
  const stopRequestedRef = useRef(false);
  const generationEpochRef = useRef(0);
  const generationChainRef = useRef<Promise<void> | null>(null);
  // Deferred step: set when post-persistence 409 retries exhaust; fired by onStepComplete.
  const pendingStepRef = useRef<(() => Promise<void>) | null>(null);
  const loadingOlderMessagesRef = useRef(false);
  const isLoadingOlderMessages$ = useObservable(false);
  const isLoadingOlderMessages = use$(isLoadingOlderMessages$);
  // Bumped by retryLoad() to re-run the load+connect effect after a failure.
  const [retryNonce, setRetryNonce] = useState(0);

  // Share ownership with the retained subscription, not the hook that sends.
  // Keep an active turn's owner during 409 backoff. When idle, publish the
  // candidate before awaiting HTTP: SSE can report an early refusal first.
  const stepForPrompt = useCallback(
    async (
      timestamp: string | undefined,
      model?: string,
      stream?: boolean,
      branch = 'main',
      maxTokens?: number,
      temperature?: number,
      topP?: number,
      reasoningEffort?: string
    ) => {
      // Retrying the same saved prompt must not erase its outage recovery
      // candidate. A different prompt still invalidates the old candidate.
      if (conversation$?.disconnectedGenerationPrompt.peek() !== timestamp) {
        conversation$?.disconnectedGenerationPrompt.set(undefined);
      }
      const previous = conversation$?.generationPrompt.peek();
      const revision = conversation$?.generationRevision.peek();
      conversation$?.pendingGenerationPrompt.set(timestamp);
      if (!previous) conversation$?.generationPrompt.set(timestamp);
      try {
        await api.step(
          conversationId,
          model,
          stream,
          branch,
          maxTokens,
          temperature,
          topP,
          reasoningEffort
        );
        // Completion, refusal or Stop may arrive before this HTTP response.
        // An acknowledgment must not resurrect ownership released by that event.
        if (
          conversation$?.generationRevision.peek() === revision &&
          conversation$?.pendingGenerationPrompt.peek() === timestamp
        ) {
          conversation$?.generationPrompt.set(timestamp);
        }
      } catch (error) {
        const { title, description } = getApiErrorPresentation(error);
        if (timestamp && (title === 'Payment required' || title === 'Authentication failed')) {
          const log$ = conversation$?.data.log;
          const index = log$?.peek().findIndex((message) => message.timestamp === timestamp) ?? -1;
          const retry = index >= 0 ? log$?.[index]._retry.peek() : undefined;
          if (retry && log$) {
            log$[index]._retry.set({
              ...retry,
              diagnostics: [...new Set([...(retry.diagnostics ?? []), `Error: ${description}`])],
              continuation: log$
                .peek()
                .slice(index + 1)
                .some((message) => message.role === 'user')
                ? undefined
                : log$
                    .peek()
                    .slice(index + 1)
                    .map(({ role, content, timestamp }) => ({ role, content, timestamp })),
            });
          }
          setMessageStatus(conversationId, timestamp, 'failed', description);
        }
        if (conversation$?.generationPrompt.peek() === timestamp) {
          conversation$?.generationPrompt.set(
            conversation$?.generationRevision.peek() === revision ? previous : undefined
          );
        }
        throw error;
      } finally {
        if (conversation$?.pendingGenerationPrompt.peek() === timestamp) {
          conversation$?.pendingGenerationPrompt.set(undefined);
        }
      }
    },
    [api, conversation$, conversationId]
  );

  // Initialize conversation in store if needed
  useEffect(() => {
    if (!conversation$) {
      initConversation(conversationId);
    }
  }, [conversationId, conversation$]);

  // Record which server loaded this bare-ID store entry. Sidebar rows compare
  // this against their own owner before trusting the cached title or live state,
  // because the same conversation ID can exist on two servers.
  useEffect(() => {
    const requestedOwner = serverId ?? serverRegistry$.peek().activeServerId;
    const entry = conversation$?.peek();
    if (entry?.serverId !== undefined && entry.serverId !== requestedOwner) {
      // The same bare ID was previously loaded from another server. Its cached
      // payload does not belong to the requested owner, and the load effect
      // would skip the fetch because the entry looks hydrated. Start fresh so
      // the data is re-fetched from the requested server. Close the previous
      // owner's live stream first: its callbacks address the entry by bare ID
      // (setGenerating/addMessage/...), so a still-open stream would keep
      // writing the old server's events into the entry that now belongs to
      // the requested server.
      getClientForServer(entry.serverId)?.closeEventStream(conversationId);
      initConversation(conversationId);
    }
    conversation$?.serverId.set(requestedOwner);
  }, [conversation$, conversationId, serverId]);

  // Ensure the conversation's chat config is loaded whenever it's missing.
  // The main load+connect effect below early-returns for already-connected
  // conversations, so without this, switching to a previously-opened
  // conversation leaves chatConfig (and thus the model selector) stale until
  // the user opens Chat Settings.
  useEffect(() => {
    if (!isConnected || isDemoMode()) return;
    if (demoConversations.some((c) => c.id === conversationId)) return;
    // Only handle the already-connected case; the load+connect effect below
    // fetches chatConfig for not-yet-connected conversations, so skipping here
    // avoids a duplicate request on initial open.
    if (!conversation$?.isConnected.peek()) return;
    if (conversation$?.chatConfig.peek()) return;
    let cancelled = false;
    api
      .getChatConfig(conversationId)
      .then((chatConfig) => {
        if (!cancelled) updateConversation(conversationId, { chatConfig });
      })
      .catch((error) => {
        console.warn(
          `[useConversation] Failed to preload chat config for ${conversationId}:`,
          error
        );
        // Set chatConfig: null (not undefined) so the model selector skeleton
        // clears — null means "fetch attempted, no config", not "still loading".
        if (!cancelled) updateConversation(conversationId, { chatConfig: null });
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, isConnected, api, conversation$]);

  // Load conversation data and connect to event stream
  useEffect(() => {
    // A credential refresh swaps the pooled client (serverClients.ts disposes the
    // replaced one), closing its stream while the conversation still reports
    // "connected". The generation stored by the subscription then no longer
    // matches the current client, so the effect re-subscribes on the
    // replacement. Reading the generation from the shared store (rather than a
    // per-instance ref) also keeps a second split-view instance from restarting
    // the live stream: once the first instance subscribes, every instance sees
    // the same generation.
    const clientGeneration = getClientGeneration(api);
    const stillConnectedOnThisClient =
      conversation$?.isConnected.get() === true &&
      conversation$?.streamClientGeneration?.get() === clientGeneration;
    if (!isConnected || stillConnectedOnThisClient) {
      return;
    }

    let cancelled = false;
    const loadAndConnect = async () => {
      try {
        // Check if this is a demo conversation
        const demoConv = demoConversations.find((conv) => conv.id === conversationId);
        if (demoConv) {
          // Initialize with demo data
          const messages = getDemoMessages(demoConv.id);
          updateConversation(conversationId, {
            data: {
              id: conversationId,
              name: demoConv.name,
              log: messages,
              logfile: conversationId,
              branches: {},
              workspace: demoConv.workspace || '/demo/workspace',
              agent: DEMO_AGENT,
            },
          });
          return;
        }

        // Only fetch from API when the window has not been properly hydrated.
        // A non-zero log.length is not sufficient: a windowed prefetch or
        // a placeholder conversation with injected messages would bypass the
        // real API load and leave indices wrong. isWindowHydrated is set by
        // updateConversationData() and initConversation(data).
        const isWindowHydrated = conversation$?.isWindowHydrated?.peek() === true;
        if (!isWindowHydrated) {
          // Only load from API if we don't already have conversation data
          try {
            const data = await api.getConversation(conversationId, { limit: 50 });
            if (cancelled) return;

            // Also load the chat config
            try {
              const chatConfig = await api.getChatConfig(conversationId);
              if (cancelled) return;
              updateConversationData(conversationId, data);
              updateConversation(conversationId, { chatConfig, loadError: null });
            } catch (error) {
              if (cancelled) return;
              console.warn(
                `[useConversation] Failed to load chat config for ${conversationId}:`,
                error
              );
              // Still update with conversation data even if config fails.
              // Set chatConfig: null (not undefined) so the model selector skeleton
              // clears — null means "fetch attempted, no config", not "still loading".
              updateConversationData(conversationId, data);
              updateConversation(conversationId, { chatConfig: null, loadError: null });
            }
          } catch (error) {
            if (cancelled) return;
            console.warn(
              `[useConversation] Failed to load conversation ${conversationId} from API:`,
              error
            );
            // Don't overwrite existing placeholder data if API call fails, but
            // surface the failure so the user isn't left staring at a blank chat.
            const message = error instanceof Error ? error.message : 'Failed to load conversation';
            // Set chatConfig: null (not undefined) so the model selector shows the
            // fallback model rather than an inert loading skeleton indefinitely.
            updateConversation(conversationId, { chatConfig: null, loadError: message });
            toast({
              variant: 'destructive',
              title: 'Failed to load conversation',
              description: message,
            });
            return;
          }
        } else if (conversation$?.chatConfig?.peek() === undefined) {
          // Conversations created through createConversationWithPlaceholder()
          // arrive already hydrated, so the branch above — the only other place
          // that loads chatConfig on first open — is skipped for them.  The
          // "ensure chat config is loaded" effect above cannot cover this case
          // either: it bails on !conversation$.isConnected.peek(), and peek()
          // does not subscribe, so it never re-runs once the conversation
          // connects.  Without this fetch chatConfig stays `undefined` forever
          // and the model selector is stuck on its loading skeleton for the
          // whole life of every newly created conversation.
          try {
            // Gate on any in-flight background server creation before fetching
            // config: the config endpoint returns 404 until the PUT completes.
            await api.waitForConversationCreation(conversationId);
            const chatConfig = await api.getChatConfig(conversationId);
            if (!cancelled) updateConversation(conversationId, { chatConfig });
          } catch (error) {
            console.warn(
              `[useConversation] Failed to load chat config for ${conversationId}:`,
              error
            );
            // null (not undefined) = "fetch attempted, no config", which clears
            // the skeleton and falls back to the default model.
            if (!cancelled) updateConversation(conversationId, { chatConfig: null });
          }
        }

        // A superseded load must neither overwrite the new owner's entry nor
        // reconnect the old owner's stream after the ownership reset closed it.
        if (cancelled) return;

        // Check number of connected conversations
        const connectedConvs = Array.from(conversations$.get().entries())
          .filter(([_, state]) => state.isConnected)
          .map(([id]) => id);

        // If we're at the limit, disconnect the oldest one that isn't selected
        if (connectedConvs.length >= MAX_CONNECTED_CONVERSATIONS) {
          const selectedId = selectedConversation$.get();
          const toDisconnect = connectedConvs
            .filter((id) => id !== selectedId)
            .slice(0, connectedConvs.length - MAX_CONNECTED_CONVERSATIONS + 1);

          for (const id of toDisconnect) {
            api.closeEventStream(id);
            setConnected(id, false);
          }
        }

        // Keep stream ownership by identity: a late attachment POST can append
        // a user message after the assistant currently receiving tokens.
        let streamingTimestamp: string | undefined;
        const streamingMessage = () => {
          const messages = conversation$?.data.log.get() ?? [];
          const index = streamingTimestamp
            ? messages.findIndex(
                (message) =>
                  message.role === 'assistant' && message.timestamp === streamingTimestamp
              )
            : messages.length - 1;
          return index >= 0 ? conversation$?.data.log[index] : undefined;
        };

        // Connect to event stream. Record the client generation that owns the
        // stream so all instances of this conversation agree a live subscription
        // exists, and a replaced client is detected as stale.
        updateConversation(conversationId, { streamClientGeneration: clientGeneration });
        api
          .subscribeToEvents(conversationId, {
            onMessageStart: () => {
              if (stopRequestedRef.current) {
                // Handshake started generation after Stop. Keep the UI stopped
                // and interrupt now that a session is likely present.
                setGenerating(conversationId, false);
                void api.interruptGeneration(conversationId);
                return;
              }
              setGenerating(conversationId, true);
              setExecutingTool(conversationId, null, null); // Clear executing tool when starting new generation
              messageJustCompleted.current = false;

              // Add empty message placeholder if needed
              const messages$ = conversation$?.data.log;
              const lastMessage$ = messages$?.[messages$.length - 1];
              if (
                !lastMessage$ ||
                lastMessage$.role.get() !== 'assistant' ||
                lastMessage$.content.get() !== ''
              ) {
                const streamingMessage: StreamingMessage = {
                  role: 'assistant',
                  content: '',
                  timestamp: new Date().toISOString(),
                  isComplete: false,
                };
                addMessage(conversationId, streamingMessage);
                streamingTimestamp = streamingMessage.timestamp;
              } else {
                streamingTimestamp = lastMessage$.timestamp.get();
              }
            },
            onToken: (token) => {
              const lastMessage$ = streamingMessage();
              if (lastMessage$?.role?.get() === 'assistant') {
                lastMessage$.content.set((prev) => prev + token);
              } else {
                console.warn(
                  '[useConversation] No assistant message to append token to. Last message role:',
                  lastMessage$?.role?.get()
                );
              }
            },
            onMessageComplete: (message) => {
              messageJustCompleted.current = true;
              lastCompletedMessageRef.current = message;

              // Update the stream-owned message even if a late POST follows it.
              const lastMessage$ = streamingMessage();
              if (lastMessage$?.role.get() === 'assistant') {
                lastMessage$.content.set(message.content);
                if (message.metadata) {
                  lastMessage$.metadata.set(message.metadata);
                }
                if (message.timestamp) {
                  lastMessage$.timestamp.set(message.timestamp);
                }
                if ('isComplete' in lastMessage$.get()) {
                  lastMessage$.set({ ...lastMessage$.get(), isComplete: true } as StreamingMessage);
                }
              }

              // Fallback: clear generating state after 100ms to allow onToolPending to
              // fire first. On updated servers this timer is cancelled and superseded by
              // onStepComplete (which fires after generating=False on the server), closing
              // the race window between generation_complete and generating=False entirely.
              if (generationCompleteTimerRef.current !== null) {
                clearTimeout(generationCompleteTimerRef.current);
              }
              generationCompleteTimerRef.current = setTimeout(() => {
                generationCompleteTimerRef.current = null;
                if (messageJustCompleted.current) {
                  messageJustCompleted.current = false;
                  setGenerating(conversationId, false);
                  playChime().catch((error) => {
                    console.warn('Failed to play completion chime:', error);
                  });
                  speakText(message.content);
                  notifyGenerationComplete(conversation$?.data.name.get()).catch((error) => {
                    console.warn('Failed to show completion notification:', error);
                  });
                }
              }, 100);
            },
            onStepComplete: () => {
              // A step can end at a manual-confirmation pause. The next model
              // call belongs to the same prompt and starts without stepForPrompt.
              if (!conversation$?.pendingTool.peek() && !conversation$?.executingTool.peek()) {
                conversation$?.disconnectedGenerationPrompt.set(undefined);
                conversation$?.generationRevision.set((value) => (value ?? 0) + 1);
                const pending = conversation$?.pendingGenerationPrompt.peek();
                const owner = conversation$?.generationPrompt.peek();
                conversation$?.generationPrompt.set(pending !== owner ? pending : undefined);
              }
              // step_complete fires after session.generating=False on the server,
              // so it's safe to clear generating immediately — no need to guess.
              // Cancel the 100ms fallback timer from onMessageComplete if still pending;
              // we take over its responsibilities here (chime, TTS, notification).
              if (generationCompleteTimerRef.current !== null) {
                clearTimeout(generationCompleteTimerRef.current);
                generationCompleteTimerRef.current = null;
              }
              if (messageJustCompleted.current) {
                // No tool continuation — finalize the turn with chime/TTS/notification
                messageJustCompleted.current = false;
                const completedMessage = lastCompletedMessageRef.current;
                lastCompletedMessageRef.current = null;
                setGenerating(conversationId, false);
                playChime().catch((error) => {
                  console.warn('Failed to play completion chime:', error);
                });
                if (completedMessage) {
                  speakText(completedMessage.content);
                }
                notifyGenerationComplete(conversation$?.data.name.get()).catch((error) => {
                  console.warn('Failed to show completion notification:', error);
                });
              } else {
                // step_complete without a preceding generation_complete, OR after
                // onToolPending cleared messageJustCompleted (tool continuation that
                // ends without another LLM round).  In all cases the server has
                // already set generating=False before emitting step_complete, so
                // clearing it here is safe.  For non-auto-confirm tools,
                // onToolPending already cleared generating, making this a no-op.
                setGenerating(conversationId, false);
              }
              // Fire any step that was deferred because the retry window exhausted
              // while the server was still busy.  step_complete is the authoritative
              // idle signal, so the deferred call is safe to make now.
              const pendingStep = pendingStepRef.current;
              if (pendingStep) {
                pendingStepRef.current = null;
                pendingStep().catch((error) => {
                  if (ApiClientError.isApiError(error) && error.status === 409) {
                    // Server still busy; re-park and wait for the next step_complete.
                    pendingStepRef.current = pendingStep;
                  } else {
                    console.error('Deferred step failed:', error);
                    const { title, description } = getApiErrorPresentation(error, {
                      fallbackTitle: 'Step failed',
                      fallbackDescription: 'Failed to resume generation after retry',
                    });
                    toast({ variant: 'destructive', title, description });
                  }
                });
              }
            },
            onMessageAdded: (message) => {
              // Check if this message already exists (ignoring timestamp)
              const messages$ = conversation$?.data.log;
              const lastMessage$ = messages$?.[messages$.length - 1];
              if (
                lastMessage$?.role.get() === message.role &&
                lastMessage$?.content.get() === message.content
              ) {
                return;
              }
              addMessage(conversationId, message);
            },
            onToolPending: (toolId, tooluse, auto_confirm) => {
              if (messageJustCompleted.current) {
                messageJustCompleted.current = false;
                // Keep generating true as we're continuing with tool execution
              }

              // Always set pending tool state so onToolExecuting can find it
              setPendingTool(conversationId, toolId, tooluse);

              if (!auto_confirm) {
                // Always set generating to false and play chime for manual confirmation
                setGenerating(conversationId, false);
                playChime().catch((error) => {
                  console.warn('Failed to play tool confirmation chime:', error);
                });
                notifyToolConfirmation(tooluse?.tool, conversation$?.data.name.get()).catch(
                  (error) => {
                    console.warn('Failed to show tool confirmation notification:', error);
                  }
                );
              }
              // Auto-confirm tools are already scheduled by the server. Sending
              // another approval from every viewer races that execution.
            },
            onToolExecuting: (toolId) => {
              // Get the pending tool to move it to executing state
              const pendingTool = conversation$?.pendingTool.get();
              if (pendingTool && pendingTool.id === toolId) {
                // Move from pending to executing (generating stays false - tools can't be interrupted)
                setPendingTool(conversationId, null, null);
                setExecutingTool(conversationId, toolId, pendingTool.tooluse, Date.now());
              } else {
                console.warn(
                  '[useConversation] No matching pending tool found for executing tool:',
                  toolId
                );
              }
            },
            onToolOutput: (toolId, content) => {
              const executingTool = conversation$?.executingTool.get();
              if (executingTool && executingTool.id === toolId) {
                setToolOutput(conversationId, content);
              }
            },
            onToolComplete: (toolId, durationMs, success) => {
              const executingTool = conversation$?.executingTool.get();
              if (!executingTool) {
                console.warn(
                  '[useConversation] tool_complete received with no executing tool — ignoring stale event:',
                  { toolId }
                );
                return;
              }
              if (executingTool.id !== toolId) {
                console.warn(
                  '[useConversation] tool_complete ID mismatch — ignoring stale event:',
                  { received: toolId, executing: executingTool.id }
                );
                return;
              }
              const toolName = executingTool.tooluse.tool;
              setToolComplete(conversationId, toolName, durationMs, success);
            },
            onInterrupted: () => {
              conversation$?.disconnectedGenerationPrompt.set(undefined);
              conversation$?.generationRevision.set((value) => (value ?? 0) + 1);
              conversation$?.generationPrompt.set(undefined);
              conversation$?.pendingGenerationPrompt.set(undefined);
              setGenerating(conversationId, false);
              setPendingTool(conversationId, null, null);
              setExecutingTool(conversationId, null, null);
              messageJustCompleted.current = false;

              // Mark the last message as interrupted
              const messages$ = conversation$?.data.log;
              const lastMessage$ = messages$?.[messages$.length - 1];
              if (lastMessage$?.role.get() === 'assistant') {
                const content = lastMessage$.content.get();
                if (!content.toLowerCase().includes('[interrupted]')) {
                  lastMessage$.content.set(content + ' [INTERRUPTED]');
                }
              }
            },
            onError: (error) => {
              console.error('[useConversation] Error:', error);
              setGenerating(conversationId, false);
              setPendingTool(conversationId, null, null);
              setExecutingTool(conversationId, null, null);
              messageJustCompleted.current = false;

              const messages$ = conversation$?.data.log;
              const assistantIndex = findLatestAssistantIndexForError(messages$?.peek());
              const assistantMessage$ =
                assistantIndex >= 0 ? messages$?.[assistantIndex] : undefined;
              const streamingAssistantMessage$ = assistantMessage$ as
                | (typeof assistantMessage$ & { isComplete?: { set: (value: boolean) => void } })
                | undefined;

              if (assistantMessage$?.role.get() === 'assistant') {
                const content = assistantMessage$.content.get();
                if (content === '') {
                  const timestamp = assistantMessage$.timestamp?.get();
                  if (timestamp) {
                    removeMessage(conversationId, timestamp);
                  }
                } else if (streamingAssistantMessage$?.isComplete) {
                  streamingAssistantMessage$.isComplete.set(true);
                }
              }

              // Classify so a refusal the user must act on (HTTP 402 payment
              // required, 401) is labelled as such instead of a generic
              // "Error", and keep it visible after the toast is dismissed by
              // attaching it to the user message that triggered the generation.
              // ChatMessage renders `_error` inline with a Retry button, which
              // keeps the refusal diagnosable after the transient toast closes.
              const { title, description } = getApiErrorPresentation(error, {
                fallbackTitle: 'Generation failed',
                fallbackDescription: error,
              });

              // Transport errors share this callback but do not mean the
              // generation was refused. The server may still be working.
              if (title === 'Payment required' || title === 'Authentication failed') {
                // The server persists a system error before emitting `error`.
                // A step whose HTTP response has not landed yet is still the
                // newest request the server could be failing, so prefer the
                // pending owner over an older accepted one.
                const log = messages$?.peek() ?? [];
                const owner =
                  conversation$?.pendingGenerationPrompt.peek() ??
                  conversation$?.generationPrompt.peek();
                const index = owner ? log.findIndex((message) => message.timestamp === owner) : -1;
                const userMessage = log[index];
                const timestamp = userMessage?.timestamp;
                if (timestamp) {
                  if (userMessage._retry) {
                    messages$![index]._retry.set({
                      ...userMessage._retry,
                      // Snapshot only the active turn. A newer queued user prompt
                      // must never become part of an earlier prompt's Retry.
                      continuation: log.slice(index + 1).some((message) => message.role === 'user')
                        ? undefined
                        : log
                            .slice(index + 1)
                            .map(({ role, content, timestamp }) => ({ role, content, timestamp })),
                      diagnostics: [
                        ...new Set([
                          ...(userMessage._retry.diagnostics ?? []),
                          `Error: ${description}`,
                        ]),
                      ],
                    });
                  }
                  setMessageStatus(conversationId, timestamp, 'failed', description);
                }
              }
              // Keep a recovery candidate separate from active ownership. Only a
              // reconnect handshake reporting an active turn may restore it; a new
              // send/Stop invalidates it. Repeated connection errors retain it.
              const refused = title === 'Payment required' || title === 'Authentication failed';
              conversation$?.disconnectedGenerationPrompt.set(
                refused
                  ? undefined
                  : (conversation$?.generationPrompt.peek() ??
                      conversation$?.disconnectedGenerationPrompt.peek())
              );
              // Prevent late HTTP acknowledgments from restoring active ownership.
              conversation$?.generationRevision.set((value) => (value ?? 0) + 1);
              conversation$?.generationPrompt.set(undefined);
              conversation$?.pendingGenerationPrompt.set(undefined);

              toast({
                variant: 'destructive',
                title,
                description,
              });
            },
            onConfigChanged: (config, changedFields) => {
              // Update the full chat config in the conversation state
              updateConversation(conversationId, { chatConfig: config });

              // Check if the name was changed
              if (changedFields.includes('name') && config.chat.name) {
                updateConversationName(conversationId, config.chat.name);
              }
            },
            onConnected: () => {
              // Check if this conversation needs initial step (was created from WelcomeView)
              // This fixes the race condition where step() was called before subscription.
              // Re-read immediately before stepping so a Stop click during the handshake
              // (which clears needsInitialStep / stopRequestedRef) is honored.
              if (stopRequestedRef.current || !conversation$?.needsInitialStep?.get()) {
                return;
              }
              const initialStepStream = conversation$?.initialStepStream?.get();
              const log = conversation$?.data.log.get() ?? [];
              const index = log.length - 1;
              const prompt = log[index];
              if (prompt?._retry) {
                conversation$!.data.log[index]._retry.options.set({
                  ...prompt._retry.options,
                  stream: initialStepStream ?? true,
                  maxTokens: conversation$?.maxTokens?.get(),
                  temperature: conversation$?.temperature?.get(),
                  topP: conversation$?.topP?.get(),
                });
              }
              clearInitialStepState(conversationId);
              // The handshake snapshot precedes this request. Keep Stop available
              // while step() starts, before the first generation_started event.
              setGenerating(conversationId, true);
              stepForPrompt(
                prompt?.role === 'user' ? prompt.timestamp : undefined,
                undefined,
                initialStepStream ?? true,
                'main',
                conversation$?.maxTokens?.get(),
                conversation$?.temperature?.get(),
                conversation$?.topP?.get()
              ).catch((error) => {
                console.error('[useConversation] Error triggering initial step:', error);
                setGenerating(conversationId, false);
                const { title, description } = getApiErrorPresentation(error);
                if (
                  prompt?.timestamp &&
                  (title === 'Payment required' || title === 'Authentication failed')
                ) {
                  setMessageStatus(conversationId, prompt.timestamp, 'failed', description);
                }
                toastStepStartError(toast, error);
              });
            },
            onConnectionState: (state) => {
              switch (state.status) {
                case 'connected':
                  setConnectionStatus(conversationId, 'connected');
                  break;
                case 'reconnecting':
                  setConnectionStatus(conversationId, 'reconnecting', {
                    attempt: state.attempt,
                    maxAttempts: state.maxAttempts,
                    retryInMs: state.retryInMs,
                  });
                  break;
                case 'disconnected':
                  setConnectionStatus(conversationId, 'disconnected', { error: state.message });
                  break;
                case 'connecting':
                  setConnectionStatus(conversationId, 'connecting');
                  break;
              }
            },
            onReconnectState: (state) => {
              const recovery = conversation$?.disconnectedGenerationPrompt.peek();
              const log = conversation$?.data.log.peek() ?? [];
              // Timestamps are millisecond-precision, so two sends can share
              // one. Recovery must target the newest such message, not the
              // first, or a 402 is attributed to an already-finished prompt.
              let index = -1;
              for (let i = log.length - 1; i >= 0; i -= 1) {
                if (log[i].timestamp === recovery) {
                  index = i;
                  break;
                }
              }
              if (
                !stopRequestedRef.current &&
                (state.generating || state.pendingTools.length > 0) &&
                !conversation$?.generationPrompt.peek() &&
                !conversation$?.pendingGenerationPrompt.peek() &&
                index >= 0 &&
                // Local history can miss a newer peer prompt during an outage.
                // Older servers without turn identity cannot safely recover it.
                !!state.latestUserTimestamp &&
                !!recovery &&
                sameLogMessage(log[index], {
                  role: 'user',
                  content: '',
                  timestamp: state.latestUserTimestamp,
                }) &&
                log[index]._retry?.persistence === 'saved' &&
                !log.slice(index + 1).some((message) => message.role === 'user')
              ) {
                conversation$?.generationPrompt.set(recovery);
              }
              conversation$?.disconnectedGenerationPrompt.set(undefined);
              setGenerating(conversationId, state.generating);
              setPendingTool(conversationId, null, null);
              // Restore first pending tool (if any)
              if (state.pendingTools.length > 0) {
                if (state.pendingTools.length > 1) {
                  console.warn(
                    '[useConversation] Multiple pending tools on reconnect — only restoring the first'
                  );
                }
                const pt = state.pendingTools[0];
                setPendingTool(conversationId, pt.tool_id, pt.tooluse);
                if (!pt.auto_confirm) {
                  setGenerating(conversationId, false);
                }
              }
            },
            onConversationEdited: (data) => {
              updateBranches(conversationId, data.branches);
              // Reset to main branch (setCurrentBranch also replaces data.log)
              setCurrentBranch(conversationId, 'main');
            },
          })
          .catch((err) => {
            console.error('[useConversation] Failed to subscribe to events:', err);
            setGenerating(conversationId, false);
            toast({
              variant: 'destructive',
              title: 'Error',
              description: 'Failed to connect to event stream',
            });
          });
      } catch (error) {
        console.error('Error loading conversation:', error);
        toast({
          variant: 'destructive',
          title: 'Error',
          description: 'Failed to load conversation',
        });
      }
    };

    void loadAndConnect();

    // Cleanup function - only disconnect if page is being unloaded
    return () => {
      cancelled = true;
      if (document.hidden) {
        api.closeEventStream(conversationId);
        setConnected(conversationId, false);
      }
    };
  }, [conversationId, isConnected, api, conversation$, toast, retryNonce, stepForPrompt]);

  // A new user-initiated generation supersedes any prior Stop. Without this,
  // onMessageStart treats the next edit/rerun/regenerate as part of the
  // cancelled request and immediately interrupts it.
  //
  // Call this BEFORE the first await of the path, never after. Clearing the
  // flag once a request is already in flight would also swallow a *newer* Stop
  // the user pressed while waiting for that request.
  //
  // Epoch + chain: a later action may call this while an older rerunTools() is
  // still in flight. That clear is current user intent, but the older request
  // must still re-interrupt leftover auto-confirm tools before the newer path
  // starts its first API call. The chain serializes those paths so the
  // re-interrupt cannot kill the new generation.
  const beginGeneration = () => {
    generationEpochRef.current += 1;
    stopRequestedRef.current = false;
    return generationEpochRef.current;
  };

  const generationIsStale = (epoch: number) =>
    stopRequestedRef.current || generationEpochRef.current !== epoch;

  const runGeneration = async (work: (epoch: number) => Promise<void>) => {
    const epoch = beginGeneration();
    const previous = generationChainRef.current;
    let release: () => void = () => {};
    const current = new Promise<void>((resolve) => {
      release = resolve;
    });
    generationChainRef.current = current;
    try {
      // Only yield when another generation is actually in flight. Awaiting an
      // already-resolved tail would let Stop land before this path's first
      // API call — a different race than the one we serialize.
      if (previous) {
        await previous.catch(() => undefined);
      }
      if (generationIsStale(epoch)) return;
      await work(epoch);
    } finally {
      release();
      if (generationChainRef.current === current) {
        generationChainRef.current = null;
      }
    }
  };

  const sendMessage = async ({ message, options }: { message: string; options?: ChatOptions }) => {
    if (!conversation$) {
      throw new Error('Conversation not initialized');
    }

    await runGeneration(async (epoch) => {
      // Clear any pending or executing tool when sending a new message
      const pendingTool = conversation$?.pendingTool.get();
      const executingTool = conversation$?.executingTool.get();
      if (pendingTool) {
        setPendingTool(conversationId, null, null);
      }
      if (executingTool) {
        setExecutingTool(conversationId, null, null);
      }

      // Upload pending files first if any
      let filePaths = options?.files || [];
      if (options?.pendingFiles?.length) {
        try {
          const uploadResult = await api.uploadFiles(conversationId, options.pendingFiles);
          filePaths = [...filePaths, ...uploadResult.files.map((f) => f.path)];
        } catch (error) {
          console.error('[useConversation] File upload failed:', error);
          toast({
            variant: 'destructive',
            title: 'Upload failed',
            description: 'Your message was not sent. Check the files and try Send again.',
          });
          throw new AttachmentUploadError(error);
        }
        options.onFilesUploaded?.();
      }

      // Create user message with pending status
      const userMessage: Message = {
        role: 'user',
        content: message,
        timestamp: new Date().toISOString(),
        ...(filePaths.length > 0 ? { files: filePaths } : {}),
        _status: 'pending',
      };

      // Add message to conversation (optimistic)
      addMessage(conversationId, userMessage);

      await sendOrResumeMessage(userMessage, options, epoch, false);
    });
  };

  const sendOrResumeMessage = async (
    userMessage: Message,
    options: ChatOptions | undefined,
    epoch: number,
    messageSent: boolean,
    requeueOnBusy = true
  ) => {
    const resumingSavedMessage = messageSent;
    const checkRetryTurn = () => {
      // step() always uses current history, not the message selected for Retry.
      // Never truncate newer turns or silently answer them with older settings.
      const log = conversation$?.data.log.get() ?? [];
      const index = log.findIndex((message) => message.timestamp === userMessage.timestamp);
      // The persisted diagnostic for this refusal is not a newer turn. Keep
      // it in history, but withhold Retry for any unrelated/newer activity.
      // Diagnostics are stored with an `Error: ` prefix while the persisted
      // system message may carry only the description (or a provider variant),
      // so compare normalized text instead of raw strings.
      const normalizeDiagnostic = (text: string) => text.trim().replace(/^Error:\s*/i, '');
      const refusalDiagnostics = (userMessage._retry?.diagnostics ?? []).map(normalizeDiagnostic);
      const onlyRefusalDiagnostics =
        index >= 0 &&
        log
          .slice(index + 1)
          .every(
            (message) =>
              message.role === 'system' &&
              refusalDiagnostics.includes(normalizeDiagnostic(message.content))
          );
      const continuation = userMessage._retry?.continuation;
      const trailing = log.slice(index + 1);
      const sameRefusedTurn =
        index >= 0 &&
        continuation !== undefined &&
        JSON.stringify(
          trailing
            .slice(0, continuation.length)
            .map(({ role, content, timestamp }) => ({ role, content, timestamp }))
        ) === JSON.stringify(continuation) &&
        trailing
          .slice(continuation.length)
          .every(
            (message) =>
              message.role === 'system' &&
              refusalDiagnostics.includes(normalizeDiagnostic(message.content))
          );
      if (resumingSavedMessage && !onlyRefusalDiagnostics && !sameRefusedTurn) {
        throw new Error(
          'Conversation has moved on. Resend this prompt as a new message, or use Edit and regenerate to explicitly rerun the earlier turn.'
        );
      }
    };
    const retryOptions = {
      model: options?.model,
      reasoningEffort: options?.reasoningEffort,
      stream: options?.stream,
      maxTokens: options?.maxTokens,
      temperature: options?.temperature,
      topP: options?.topP,
    };
    const setRetryState = (persistence: 'saved' | 'rejected' | 'unknown') => {
      const log = conversation$?.data.log;
      const index = log?.get().findIndex((m) => m.timestamp === userMessage.timestamp);
      if (log && index !== undefined && index >= 0) {
        log[index]._retry.set({ ...log[index]._retry.get(), persistence, options: retryOptions });
      }
    };
    try {
      checkRetryTurn();
      if (!messageSent) {
        // A missing response does not prove rejection: the server may have saved it.
        setRetryState('unknown');
        await api.sendMessage(conversationId, userMessage);
        messageSent = true;
      }
      // Retry appends at save time. If another message already occupies the
      // original placeholder slot, local history stays [retried, newer] while
      // the server is [newer, retried] — reconcile before lifting the
      // positional-mutation guard.
      if (
        !requeueOnBusy &&
        !resumingSavedMessage &&
        userMessage.timestamp &&
        (conversations$.get(conversationId)?.data.log.get() ?? []).some(
          (entry) => entry.timestamp !== userMessage.timestamp
        )
      ) {
        const log = conversations$.get(conversationId)!.data.log.get();
        const current = log.find((entry) => entry.timestamp === userMessage.timestamp);
        if (current) {
          replaceLog(conversationId, [
            ...log.filter((entry) => entry.timestamp !== userMessage.timestamp),
            current,
          ]);
        }
        try {
          await api.reconcileLateUploadOrder(conversationId);
        } catch (error) {
          console.warn('[useConversation] Failed to reconcile retried message order:', error);
        }
      }
      options?.onPersistenceOutcome?.('saved');
      setRetryState('saved');
      setMessageStatus(conversationId, userMessage.timestamp!, 'sent', '');
      // Keep settings even when the step fails or waits for server-idle.
      setMaxTokens(conversationId, options?.maxTokens);
      setTemperature(conversationId, options?.temperature);
      setTopP(conversationId, options?.topP);

      if (generationIsStale(epoch)) return;

      // Start generation. The message is already persisted, so a 409 here
      // (server still finalizing a previous step) must retry step() only —
      // re-sending the message would duplicate it in the log.
      for (let attempt = 0; ; attempt++) {
        try {
          checkRetryTurn();
          await stepForPrompt(
            userMessage.timestamp,
            options?.model,
            options?.stream,
            'main',
            options?.maxTokens,
            options?.temperature,
            options?.topP,
            options?.reasoningEffort
          );
          break;
        } catch (stepError) {
          const busy = ApiClientError.isApiError(stepError) && stepError.status === 409;
          if (!busy) throw stepError;
          if (attempt >= STEP_409_BACKOFF_MS.length) {
            // Retry window exhausted; message is already persisted, so re-sending
            // would duplicate it.  Instead, park the step call and fire it from
            // onStepComplete — the authoritative server-idle signal — so the
            // message is not falsely marked failed.
            const capturedEpoch = epoch;
            pendingStepRef.current = () => {
              if (generationIsStale(capturedEpoch)) return Promise.resolve();
              try {
                checkRetryTurn();
              } catch (error) {
                const description = (error as Error).message;
                setMessageStatus(conversationId, userMessage.timestamp!, 'failed', description);
                toast({ variant: 'destructive', title: 'Retry withheld', description });
                return Promise.resolve();
              }
              return stepForPrompt(
                userMessage.timestamp,
                options?.model,
                options?.stream,
                'main',
                options?.maxTokens,
                options?.temperature,
                options?.topP,
                options?.reasoningEffort
              );
            };
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, STEP_409_BACKOFF_MS[attempt]));
          if (generationIsStale(epoch)) return;
        }
      }
    } catch (error) {
      // These responses are returned before append. Timeouts, aborts, proxy
      // errors and 5xx responses are ambiguous and must never blindly repost.
      const rejected =
        ApiClientError.isApiError(error) &&
        [400, 401, 403, 404, 409, 413, 422].includes(error.status ?? 0);
      options?.onPersistenceOutcome?.(messageSent ? 'saved' : rejected ? 'rejected' : 'unknown');
      console.error('Error sending message:', error);
      // 409 before the message was persisted: remove the optimistic copy and
      // re-throw so the caller (e.g. ChatInput queue flush) can requeue it.
      // Once persisted, a 409 falls through to the failed-status path below —
      // a requeue would re-send and duplicate the message.
      if (
        !messageSent &&
        requeueOnBusy &&
        ApiClientError.isApiError(error) &&
        error.status === 409
      ) {
        removeMessage(conversationId, userMessage.timestamp!);
        throw error;
      }
      if (!messageSent) setRetryState(rejected ? 'rejected' : 'unknown');
      const { title, description } = getApiErrorPresentation(error, {
        fallbackTitle: 'Failed to send',
        fallbackDescription: 'Failed to send message',
      });
      setMessageStatus(conversationId, userMessage.timestamp!, 'failed', description);
      toast({
        variant: 'destructive',
        title,
        description,
      });
    }
  };

  const confirmTool = async (
    action: 'confirm' | 'edit' | 'skip' | 'auto',
    options?: { content?: string; count?: number }
  ) => {
    const pendingTool = conversation$?.pendingTool.get();
    if (!pendingTool) {
      console.warn('No pending tool to confirm');
      return;
    }

    // Skip has no tool_executing event. Reserve the continuation before clearing
    // pendingTool so ChatInput cannot flush its queue in the HTTP/SSE gap.
    // Completion/error events release isGenerating, even before HTTP returns.
    if (action === 'skip') {
      setGenerating(conversationId, true);
      setPendingTool(conversationId, null, null);
    }
    const confirmedToolId = pendingTool.id;
    const confirmOnce = async (): Promise<void> => {
      try {
        const alreadyHandled = await api.confirmTool(
          conversationId,
          confirmedToolId,
          action,
          options
        );
        // Normally onToolExecuting owns the transition. A duplicate may arrive
        // after completion, with no new event forthcoming; clear only that ID.
        if (
          (action === 'skip' || alreadyHandled) &&
          conversation$?.pendingTool.get()?.id === confirmedToolId
        ) {
          setPendingTool(conversationId, null, null);
        }
        if (action === 'skip' && alreadyHandled) {
          // No continuation was dispatched for this duplicate. Reconcile with
          // the server instead of waiting for an event that may already have
          // passed, or declaring an actually running peer continuation idle.
          const revision = conversation$?.generationRevision.peek();
          const sessions = await api.getSessions();
          if (conversation$?.generationRevision.peek() === revision) {
            setGenerating(
              conversationId,
              sessions.some(
                (session) => session.conversation_id === conversationId && session.generating
              )
            );
          }
        }
      } catch (error) {
        console.error('Error confirming tool:', error);

        // A rejected Skip never started its continuation; release its reservation
        // and restore the confirmation UI. The server still holds the pending
        // tool, so dropping it locally would strand the tool until a reload.
        if (action === 'skip') {
          setGenerating(conversationId, false);
          if (conversation$?.pendingTool.get()?.id !== confirmedToolId) {
            setPendingTool(conversationId, pendingTool.id, pendingTool.tooluse);
          }
        }

        const { title, description } = getApiErrorPresentation(error, {
          fallbackTitle: 'Error',
          fallbackDescription: 'Failed to confirm tool',
        });
        toast({
          variant: 'destructive',
          title,
          description,
        });

        throw error;
      }
    };

    await confirmOnce();
  };

  const interruptGeneration = async () => {
    // Unconditional local cancel. Stop must hide immediately even if
    // onConnected already consumed needsInitialStep and step() is in flight
    // without a session id yet. The API interrupt is best-effort and no-ops
    // until a session exists.
    stopRequestedRef.current = true;
    conversation$?.disconnectedGenerationPrompt.set(undefined);
    conversation$?.generationRevision.set((value) => (value ?? 0) + 1);
    conversation$?.generationPrompt.set(undefined);
    conversation$?.pendingGenerationPrompt.set(undefined);
    clearInitialStepState(conversationId);
    setGenerating(conversationId, false);

    try {
      await api.interruptGeneration(conversationId);
    } catch (error) {
      console.error('Error interrupting generation:', error);
      toast({
        variant: 'destructive',
        title: 'Error',
        description: 'Failed to interrupt generation',
      });
    }
  };

  const retryMessage = async (failedMessage: Message) => {
    if (!conversation$) return;
    const retry = failedMessage._retry;
    if (!retry || retry.persistence === 'unknown') {
      toast({
        variant: 'destructive',
        title: 'Message save unconfirmed',
        description:
          'Refresh and check conversation history before sending again. Retry was withheld to avoid a duplicate message.',
      });
      return;
    }
    await runGeneration(async (epoch) => {
      // Reuse the optimistic entry, uploaded paths, and original generation settings.
      // Another Retry may have completed while this one waited on the chain.
      const current = conversation$?.data.log
        .get()
        .find((message) => message.timestamp === failedMessage.timestamp);
      if (
        current?._status !== 'failed' ||
        !current._retry ||
        current._retry.persistence === 'unknown'
      )
        return;
      // Snapshot the refusal diagnostic before clearing the observable error.
      const retryMessage = { ...current };
      setMessageStatus(conversationId, failedMessage.timestamp!, 'pending', '');
      await sendOrResumeMessage(
        retryMessage,
        current._retry.options,
        epoch,
        current._retry.persistence === 'saved',
        false
      );
    });
  };

  // Server mutations use log positions. An unsaved/uncertain local entry can
  // shift every later index, so withhold positional operations until Retry or
  // a history refresh restores a fully persisted log. Never guess an index.
  const positionalMutationBlocked = () => {
    const log = conversations$.get(conversationId)?.data.log.get() ?? [];
    if (
      !conversations$.get(conversationId)?.historyOrderUncertain.get() &&
      !log.some(
        (message) =>
          message._status === 'pending' ||
          (message._retry && message._retry.persistence !== 'saved')
      )
    ) {
      return false;
    }
    toast({
      variant: 'destructive',
      title: 'Message history not synchronized',
      description: 'Retry the unsaved message or refresh history before editing messages.',
    });
    return true;
  };

  const prepareHistoryGeneration = () => {
    const log$ = conversation$?.data.log;
    const log = log$?.peek() ?? [];
    let index = log.length - 1;
    while (index >= 0 && log[index].role !== 'user') index--;
    const prompt = log[index];
    if (prompt && log$) {
      log$[index]._retry.set({
        persistence: 'saved',
        options: { stream: true, maxTokens, temperature, topP },
      });
    }
    return prompt?.timestamp;
  };

  const stepFromHistory = () =>
    stepForPrompt(
      prepareHistoryGeneration(),
      undefined,
      true,
      'main',
      maxTokens,
      temperature,
      topP
    );

  const editMessage = async (
    index: number,
    content: string,
    truncate: boolean = false,
    files?: string[],
    pendingFiles?: File[]
  ) => {
    if (positionalMutationBlocked()) return false;
    let saved = false;
    const applyEdit = async (epoch?: number) => {
      try {
        // Upload any new files first, then merge with existing file paths
        let allFiles = files;
        if (pendingFiles?.length) {
          const uploadResult = await api.uploadFiles(conversationId, pendingFiles);
          const newPaths = uploadResult.files.map((f) => f.path);
          allFiles = [...(files || []), ...newPaths];
        }
        if (positionalMutationBlocked()) return;
        const result = await api.editMessage(conversationId, index, content, truncate, allFiles);
        saved = true;
        // Use API response directly (SSE event may also arrive, but this is immediate)
        replaceLog(conversationId, result.log);
        if (result.branches) {
          updateBranches(conversationId, result.branches);
        }

        // After truncation, trigger re-generation unless Stop landed while
        // the edit request was in flight, or a newer action superseded it.
        if (truncate) {
          if (epoch === undefined || generationIsStale(epoch)) return;
          await stepFromHistory();
        }
      } catch (error) {
        console.error('Error editing message:', error);
        const errorMsg = error instanceof Error ? error.message : 'Failed to edit message';
        toast({ variant: 'destructive', title: 'Edit failed', description: errorMsg });
      }
    };

    // A truncating edit re-generates, so it owns the stop flag from here on.
    if (truncate) {
      await runGeneration(applyEdit);
    } else {
      await applyEdit();
    }
    return saved;
  };

  const deleteMessage = async (index: number) => {
    if (positionalMutationBlocked()) return;
    try {
      const result = await api.deleteMessage(conversationId, index);
      replaceLog(conversationId, result.log);
      if (result.branches) {
        updateBranches(conversationId, result.branches);
      }
    } catch (error) {
      console.error('Error deleting message:', error);
      const errorMsg = error instanceof Error ? error.message : 'Failed to delete message';
      toast({ variant: 'destructive', title: 'Delete failed', description: errorMsg });
    }
  };

  const rerunFromMessage = async (index: number) => {
    if (!conversation$ || positionalMutationBlocked()) return;
    const conv$ = conversation$;

    await runGeneration(async (epoch) => {
      if (positionalMutationBlocked()) return;
      const log = conv$.data.log.get();
      const localIndex = index - conv$.logOffset.get();
      const isLastMessage = localIndex === log.length - 1;

      try {
        if (!isLastMessage) {
          // Truncate after this message (creates backup branch)
          const result = await api.editMessage(conversationId, index, undefined, true);
          replaceLog(conversationId, result.log);
          if (result.branches) {
            updateBranches(conversationId, result.branches);
          }
        }
        if (generationIsStale(epoch)) return;
        // Re-run tools from the (now last) assistant message
        // This parses tool uses and sets them as pending, without calling the LLM.
        // Auto-confirm tools can start executing on the server before this
        // request returns, so a Stop that lands in-flight may interrupt nothing.
        const previousOwner = conv$.generationPrompt.peek();
        const revision = conv$.generationRevision.peek();
        const rerunOwner = prepareHistoryGeneration();
        try {
          conv$.disconnectedGenerationPrompt.set(undefined);
          conv$.generationPrompt.set(rerunOwner);
          await api.rerunTools(conversationId);
          if (generationIsStale(epoch)) {
            setGenerating(conversationId, false);
            try {
              await api.interruptGeneration(conversationId);
            } catch (error) {
              console.error('Error interrupting generation after Stop raced with rerun:', error);
            }
            return;
          }
        } catch {
          // Undo the speculative rerun owner before stepForPrompt captures its
          // rollback state. Never restore an owner released by a terminal event.
          if (
            conv$.generationRevision.peek() === revision &&
            conv$.generationPrompt.peek() === rerunOwner
          ) {
            conv$.generationPrompt.set(previousOwner);
          }
          // No tools found — fall back to step() (regenerate)
          if (generationIsStale(epoch)) return;
          await stepFromHistory();
        }
      } catch (error) {
        console.error('Error re-running from message:', error);
        const errorMsg = error instanceof Error ? error.message : 'Failed to re-run';
        toast({ variant: 'destructive', title: 'Re-run failed', description: errorMsg });
      }
    });
  };

  const regenerateMessage = async (index: number) => {
    if (!conversation$ || positionalMutationBlocked()) return;
    // Remove the assistant message and everything after, then step to regenerate
    // Truncate at the message BEFORE this one (the user message)
    const prevIndex = index - 1;
    if (prevIndex < 0) return;

    await runGeneration(async (epoch) => {
      try {
        if (positionalMutationBlocked()) return;
        const result = await api.editMessage(conversationId, prevIndex, undefined, true);
        replaceLog(conversationId, result.log);
        if (result.branches) {
          updateBranches(conversationId, result.branches);
        }
        if (generationIsStale(epoch)) return;
        await stepFromHistory();
      } catch (error) {
        console.error('Error regenerating message:', error);
        const errorMsg = error instanceof Error ? error.message : 'Failed to regenerate';
        toast({ variant: 'destructive', title: 'Regenerate failed', description: errorMsg });
      }
    });
  };

  const forkConversation = async (index: number) => {
    if (positionalMutationBlocked()) return null;
    try {
      const branch = conversation$?.currentBranch.get() ?? 'main';
      return await api.forkConversation(conversationId, index, branch);
    } catch (error) {
      console.error('Error forking conversation:', error);
      const errorMsg = error instanceof Error ? error.message : 'Failed to fork conversation';
      toast({ variant: 'destructive', title: 'Fork failed', description: errorMsg });
      return null;
    }
  };

  const switchBranch = (branchName: string) => {
    setCurrentBranch(conversationId, branchName);
  };

  // Re-attempt loading the conversation after a load failure. Bumping the nonce
  // re-runs the load+connect effect, which both re-fetches data AND re-subscribes
  // to the SSE event stream (the data-only retry left the conversation
  // permanently disconnected).
  const retryLoad = useCallback(() => {
    updateConversation(conversationId, { loadError: null });
    setRetryNonce((n) => n + 1);
  }, [conversationId]);

  const loadOlderMessages = async () => {
    const conv$ = conversations$.get(conversationId);
    if (!conv$) return;
    const logOffset = conv$.logOffset.peek();
    if (logOffset <= 0) return;
    if (loadingOlderMessagesRef.current) return;
    loadingOlderMessagesRef.current = true;
    isLoadingOlderMessages$.set(true);
    try {
      const data = await api.getConversation(conversationId, {
        limit: 50,
        before: logOffset,
      });
      prependLogPage(
        conversationId,
        data.log,
        data.has_more ? (data.before ?? 0) : 0,
        data.has_more ?? false
      );
    } catch (error) {
      console.warn('[useConversation] Failed to load older messages:', error);
      const { title, description } = getApiErrorPresentation(error, {
        fallbackTitle: 'Failed to load older messages',
        fallbackDescription: 'Failed to load older messages',
      });
      toast({
        variant: 'destructive',
        title,
        description,
      });
    } finally {
      loadingOlderMessagesRef.current = false;
      isLoadingOlderMessages$.set(false);
    }
  };

  return {
    conversation$,
    retryLoad,
    sendMessage,
    retryMessage,
    editMessage,
    deleteMessage,
    rerunFromMessage,
    regenerateMessage,
    forkConversation,
    switchBranch,
    confirmTool,
    interruptGeneration,
    isLoadingOlderMessages,
    loadOlderMessages,
  };
}
