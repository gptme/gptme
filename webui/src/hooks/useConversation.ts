import { useEffect, useRef } from 'react';
import { useApi } from '@/contexts/ApiContext';
import { useToast } from '@/components/ui/use-toast';
import type { Message, StreamingMessage } from '@/types/conversation';
import type { ChatOptions } from '@/components/ChatInput';
import { demoConversations, getDemoMessages } from '@/democonversations';
import { use$ } from '@legendapp/state/react';
import {
  conversations$,
  updateConversation,
  setGenerating,
  setConnected,
  setPendingTool,
  setExecutingTool,
  addMessage,
  setMessageStatus,
  removeMessage,
  replaceLog,
  updateBranches,
  setCurrentBranch,
  initConversation,
  selectedConversation$,
  updateConversationName,
  setNeedsInitialStep,
} from '@/stores/conversations';
import { playChime } from '@/utils/audio';
import { findLatestAssistantIndexForError } from '@/utils/conversationErrorHandling';
import { notifyGenerationComplete, notifyToolConfirmation } from '@/utils/notifications';
import { ApiClientError } from '@/utils/api';

const MAX_CONNECTED_CONVERSATIONS = 3;

export function useConversation(conversationId: string, serverId?: string) {
  const { getClient, isConnected$ } = useApi();
  // Use the client for the specific server, or primary if no serverId
  const api = getClient(serverId);
  const { toast } = useToast();
  const conversation$ = conversations$.get(conversationId);
  const isConnected = use$(isConnected$);

  const messageJustCompleted = useRef(false);

  // Initialize conversation in store if needed
  useEffect(() => {
    if (!conversation$) {
      console.log(`[useConversation] Initializing conversation ${conversationId}`);
      initConversation(conversationId);
    }
  }, [conversationId, conversation$]);

  // Load conversation data and connect to event stream
  useEffect(() => {
    if (!isConnected || conversation$?.isConnected.get()) {
      return;
    }

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
            },
          });
          return;
        }

        // Check if conversation already has data (e.g., from placeholder)
        const hasExistingMessages = conversation$?.data.log.peek()?.length > 0;
        console.log('[useConversation] Loading conversation', {
          conversationId,
          hasExistingMessages,
        });
        if (!hasExistingMessages) {
          // Only load from API if we don't already have conversation data
          try {
            const data = await api.getConversation(conversationId);

            // Also load the chat config
            try {
              const chatConfig = await api.getChatConfig(conversationId);
              updateConversation(conversationId, { data, chatConfig });
              console.log(`[useConversation] Loaded conversation and config for ${conversationId}`);
            } catch (error) {
              console.warn(
                `[useConversation] Failed to load chat config for ${conversationId}:`,
                error
              );
              // Still update with conversation data even if config fails
              updateConversation(conversationId, { data });
            }
          } catch (error) {
            console.warn(
              `[useConversation] Failed to load conversation ${conversationId} from API:`,
              error
            );
            // Don't overwrite existing placeholder data if API call fails
          }
        }

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

          console.log(`[useConversation] Disconnecting old conversations:`, toDisconnect);
          for (const id of toDisconnect) {
            api.closeEventStream(id);
            setConnected(id, false);
          }
        }

        // Connect to event stream
        console.log(`[useConversation] Connecting to ${conversationId}`);
        api
          .subscribeToEvents(conversationId, {
            onMessageStart: () => {
              console.log('[useConversation] Generation started');
              setGenerating(conversationId, true);
              setExecutingTool(conversationId, null, null); // Clear executing tool when starting new generation
              messageJustCompleted.current = false;

              // Add empty message placeholder if needed
              const messages$ = conversation$?.data.log;
              const lastMessage$ = messages$?.[messages$.length - 1];
              console.log(
                '[useConversation] MessageStart - messages count:',
                messages$?.length,
                'lastMessage role:',
                lastMessage$?.role?.get(),
                'content:',
                JSON.stringify(lastMessage$?.content?.get())
              );
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
                console.log('[useConversation] Adding streaming message placeholder');
                addMessage(conversationId, streamingMessage);
                console.log(
                  '[useConversation] Placeholder added, new messages count:',
                  conversation$?.data.log.length
                );
              } else {
                console.log('[useConversation] Reusing existing empty assistant message');
              }
            },
            onToken: (token) => {
              const messages$ = conversation$?.data.log;
              const lastMessage$ = messages$?.[messages$.length - 1];
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
              console.log('[useConversation] Generation complete');
              messageJustCompleted.current = true;

              // Update the last message with final content and metadata
              const messages$ = conversation$?.data.log;
              const lastMessage$ = messages$?.[messages$.length - 1];
              if (lastMessage$?.role.get() === 'assistant') {
                lastMessage$.content.set(message.content);
                if (message.metadata) {
                  lastMessage$.metadata.set(message.metadata);
                }
                if (message.timestamp) {
                  lastMessage$.timestamp.set(message.timestamp);
                }
                if ('isComplete' in lastMessage$) {
                  lastMessage$.isComplete.set(true);
                }
              }

              // Use setTimeout with 100ms delay to allow potential onToolPending to fire first
              // Increased from 0ms to give API events more breathing room
              setTimeout(() => {
                if (messageJustCompleted.current) {
                  setGenerating(conversationId, false);
                  playChime().catch((error) => {
                    console.warn('Failed to play completion chime:', error);
                  });
                  notifyGenerationComplete(conversation$?.data.name.get()).catch((error) => {
                    console.warn('Failed to show completion notification:', error);
                  });
                }
              }, 100);
            },
            onMessageAdded: (message) => {
              console.log('[useConversation] Message added:', message);
              // Check if this message already exists (ignoring timestamp)
              const messages$ = conversation$?.data.log;
              const lastMessage$ = messages$?.[messages$.length - 1];
              if (
                lastMessage$?.role.get() === message.role &&
                lastMessage$?.content.get() === message.content
              ) {
                console.log('[useConversation] Skipping duplicate message');
                return;
              }
              addMessage(conversationId, message);
            },
            onToolPending: (toolId, tooluse, auto_confirm) => {
              console.log('[useConversation] Tool pending:', { toolId, tooluse, auto_confirm });

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
              } else {
                api.confirmTool(conversationId, toolId, 'confirm').catch((error) => {
                  console.error('[useConversation] Error auto-confirming tool:', error);
                });
              }
            },
            onToolExecuting: (toolId) => {
              console.log('[useConversation] Tool executing:', { toolId });

              // Get the pending tool to move it to executing state
              const pendingTool = conversation$?.pendingTool.get();
              if (pendingTool && pendingTool.id === toolId) {
                // Move from pending to executing (generating stays false - tools can't be interrupted)
                setPendingTool(conversationId, null, null);
                setExecutingTool(conversationId, toolId, pendingTool.tooluse);
              } else {
                console.warn(
                  '[useConversation] No matching pending tool found for executing tool:',
                  toolId
                );
              }
            },
            onInterrupted: () => {
              console.log('[useConversation] Generation interrupted');
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

              toast({
                variant: 'destructive',
                title: 'Error',
                description: error,
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
              setConnected(conversationId, true);

              // Check if this conversation needs initial step (was created from WelcomeView)
              // This fixes the race condition where step() was called before subscription
              const needsStep = conversation$?.needsInitialStep?.get();
              if (needsStep) {
                console.log('[useConversation] Triggering initial step after subscription');
                setNeedsInitialStep(conversationId, false);
                api.step(conversationId).catch((error) => {
                  console.error('[useConversation] Error triggering initial step:', error);
                });
              }
            },
            onReconnectState: (state) => {
              console.log('[useConversation] Restoring state on reconnect:', state);
              if (state.generating) {
                setGenerating(conversationId, true);
              }
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
              console.log('[useConversation] Conversation edited:', data);
              updateBranches(conversationId, data.branches);
              // Reset to main branch (setCurrentBranch also replaces data.log)
              setCurrentBranch(conversationId, 'main');
            },
          })
          .catch((err) => {
            console.error('[useConversation] Failed to subscribe to events:', err);
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
      if (document.hidden) {
        console.log(`[useConversation] Page hidden, disconnecting from ${conversationId}`);
        api.closeEventStream(conversationId);
        setConnected(conversationId, false);
      }
    };
  }, [conversationId, isConnected, api, conversation$, toast]);

  const sendMessage = async ({ message, options }: { message: string; options?: ChatOptions }) => {
    if (!conversation$) {
      throw new Error('Conversation not initialized');
    }

    console.log('[useConversation] Sending message:', { message, options });

    // Clear any pending or executing tool when sending a new message
    const pendingTool = conversation$?.pendingTool.get();
    const executingTool = conversation$?.executingTool.get();
    if (pendingTool) {
      console.log('[useConversation] Clearing pending tool due to new message');
      setPendingTool(conversationId, null, null);
    }
    if (executingTool) {
      console.log('[useConversation] Clearing executing tool due to new message');
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
          description: 'Failed to upload attached files',
        });
        // Continue sending the message without files
      }
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

    try {
      // Send the message
      await api.sendMessage(conversationId, userMessage);
      setMessageStatus(conversationId, userMessage.timestamp!, 'sent');

      // Start generation
      await api.step(conversationId, options?.model, options?.stream);
    } catch (error) {
      console.error('Error sending message:', error);
      const errorMsg = error instanceof Error ? error.message : 'Failed to send message';
      setMessageStatus(conversationId, userMessage.timestamp!, 'failed', errorMsg);
      toast({
        variant: 'destructive',
        title: 'Failed to send',
        description: errorMsg,
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

    const confirmWithRetry = async (attempt: number): Promise<void> => {
      try {
        await api.confirmTool(conversationId, pendingTool.id, action, options);
        // Only clear pending tool state for 'skip' action
        // For 'confirm', 'edit', and 'auto', let onToolExecuting handle the transition
        if (action === 'skip') {
          setPendingTool(conversationId, null, null);
        }
      } catch (error) {
        console.error(`Error confirming tool (attempt ${attempt}):`, error);

        // Check if it's a 404 error (either "Tool not found" or "Session ID not found")
        // Session ID may not be available yet during initial SSE connection
        // ApiClientError has status property, not in message string
        const is404Error =
          ApiClientError.isApiError(error) &&
          error.status === 404 &&
          (error.message.includes('Tool not found') ||
            error.message.includes('Session ID not found'));

        if (is404Error && attempt < 3) {
          console.log(`Retrying tool confirmation in 500ms (attempt ${attempt + 1}/3)`);
          // Small delay to let any server-side initialization complete
          await new Promise((resolve) => setTimeout(resolve, 500));
          return confirmWithRetry(attempt + 1);
        }

        // Clear pending tool state on final failure, but only for actions that won't execute
        if (action === 'skip') {
          setPendingTool(conversationId, null, null);
        }

        toast({
          variant: 'destructive',
          title: 'Error',
          description:
            attempt > 1
              ? `Failed to confirm tool after ${attempt} attempts`
              : 'Failed to confirm tool',
        });

        throw error;
      }
    };

    await confirmWithRetry(1);
  };

  const interruptGeneration = async () => {
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
    // Remove the failed message and re-send
    removeMessage(conversationId, failedMessage.timestamp!);
    await sendMessage({
      message: failedMessage.content,
      options: failedMessage.files?.length ? { files: failedMessage.files } : undefined,
    });
  };

  const editMessage = async (
    index: number,
    content: string,
    truncate: boolean = false,
    files?: string[],
    pendingFiles?: File[]
  ) => {
    try {
      // Upload any new files first, then merge with existing file paths
      let allFiles = files;
      if (pendingFiles?.length) {
        const uploadResult = await api.uploadFiles(conversationId, pendingFiles);
        const newPaths = uploadResult.files.map((f) => f.path);
        allFiles = [...(files || []), ...newPaths];
      }
      const result = await api.editMessage(conversationId, index, content, truncate, allFiles);
      // Use API response directly (SSE event may also arrive, but this is immediate)
      replaceLog(conversationId, result.log);
      if (result.branches) {
        updateBranches(conversationId, result.branches);
      }

      // After truncation, trigger re-generation
      if (truncate) {
        await api.step(conversationId);
      }
    } catch (error) {
      console.error('Error editing message:', error);
      const errorMsg = error instanceof Error ? error.message : 'Failed to edit message';
      toast({ variant: 'destructive', title: 'Edit failed', description: errorMsg });
    }
  };

  const deleteMessage = async (index: number) => {
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
    if (!conversation$) return;
    const log = conversation$.data.log.get();
    const isLastMessage = index === log.length - 1;

    try {
      if (!isLastMessage) {
        // Truncate after this message (creates backup branch)
        const result = await api.editMessage(conversationId, index, undefined, true);
        replaceLog(conversationId, result.log);
        if (result.branches) {
          updateBranches(conversationId, result.branches);
        }
      }
      // Re-run tools from the (now last) assistant message
      // This parses tool uses and sets them as pending, without calling the LLM
      try {
        await api.rerunTools(conversationId);
      } catch {
        // No tools found — fall back to step() (regenerate)
        await api.step(conversationId);
      }
    } catch (error) {
      console.error('Error re-running from message:', error);
      const errorMsg = error instanceof Error ? error.message : 'Failed to re-run';
      toast({ variant: 'destructive', title: 'Re-run failed', description: errorMsg });
    }
  };

  const regenerateMessage = async (index: number) => {
    if (!conversation$) return;
    // Remove the assistant message and everything after, then step to regenerate
    // Truncate at the message BEFORE this one (the user message)
    const prevIndex = index - 1;
    if (prevIndex < 0) return;

    try {
      const result = await api.editMessage(conversationId, prevIndex, undefined, true);
      replaceLog(conversationId, result.log);
      if (result.branches) {
        updateBranches(conversationId, result.branches);
      }
      await api.step(conversationId);
    } catch (error) {
      console.error('Error regenerating message:', error);
      const errorMsg = error instanceof Error ? error.message : 'Failed to regenerate';
      toast({ variant: 'destructive', title: 'Regenerate failed', description: errorMsg });
    }
  };

  const switchBranch = (branchName: string) => {
    setCurrentBranch(conversationId, branchName);
  };

  return {
    conversation$,
    sendMessage,
    retryMessage,
    editMessage,
    deleteMessage,
    rerunFromMessage,
    regenerateMessage,
    switchBranch,
    confirmTool,
    interruptGeneration,
  };
}
