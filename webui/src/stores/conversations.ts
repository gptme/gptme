import { batch, mergeIntoObservable, observable } from '@legendapp/state';
import type { ChatConfig, ConversationResponse } from '@/types/api';
import type { Message, StreamingMessage, ToolUse } from '@/types/conversation';
import { demoConversations, getDemoMessages } from '@/democonversations';

export interface PendingTool {
  id: string;
  tooluse: ToolUse;
}

export interface ExecutingTool {
  id: string;
  tooluse: ToolUse;
  startedAt: number;
  partialOutput: string;
}

export type ConversationConnectionStatus =
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'disconnected';

export interface ConversationState {
  // The conversation data
  data: ConversationResponse;
  // Whether this conversation is currently generating
  isGenerating: boolean;
  // Whether this conversation has an active event stream
  isConnected: boolean;
  connectionStatus: ConversationConnectionStatus;
  reconnectAttempt: number | null;
  reconnectMaxAttempts: number | null;
  reconnectRetryInMs: number | null;
  reconnectRetryStartedAt: number | null;
  connectionError: string | null;
  // Error message if loading the conversation from the API failed
  loadError: string | null;
  // Any pending tool
  pendingTool: PendingTool | null;
  // Any executing tool
  executingTool: ExecutingTool | null;
  // Duration of the most recently completed tool (cleared when next tool starts)
  lastCompletedTool: {
    toolName: string;
    durationMs: number;
    completedAt: number;
    success: boolean;
  } | null;
  // Last received message
  lastMessage?: Message;
  // Whether to show the initial system message
  showInitialSystem: boolean;
  // The chat config
  chatConfig: ChatConfig | null;
  // Whether this conversation needs initial step after connecting
  // Used to fix race condition where step() was called before event subscription
  needsInitialStep: boolean;
  // Stream setting captured when creating a placeholder conversation.
  // The initial step starts before chatConfig is always loaded into the store.
  initialStepStream?: boolean;
  // Currently displayed branch name
  currentBranch: string;
  // Max tokens setting for model responses, persisted across operations.
  // Set by ChatInput when sending a message; read by all step() call sites.
  maxTokens?: number;
  // Sampling temperature, persisted across operations. Undefined = provider default.
  temperature?: number;
  // Nucleus sampling top_p, persisted across operations. Undefined = provider default.
  topP?: number;
  // Window metadata: tracks which slice of the full conversation is loaded.
  // Absolute index of log[0] in the full conversation; 0 = full log from start.
  logOffset: number;
  // True when there are older messages that exist before logOffset.
  hasMoreBefore: boolean;
  // False for placeholder/prefetch entries that have not been properly hydrated
  // from the API. Replaces the fragile hasExistingMessages check.
  isWindowHydrated: boolean;
}

// Central store for all conversations
export const conversations$ = observable(new Map<string, ConversationState>());

// Currently selected conversation
export const selectedConversation$ = observable<string>(demoConversations[0].id);

// Helper functions
export function updateConversation(id: string, update: Partial<ConversationState>) {
  // Note: conversations$.get(id) returns a truthy lazy node even for missing
  // keys, so check the actual value via peek() to detect non-existent entries.
  if (!conversations$.get(id)?.peek()) {
    // Initialize with defaults if conversation doesn't exist
    conversations$.set(id, {
      data: { id, name: '', log: [], logfile: id, branches: {}, workspace: '/default/workspace' },
      isGenerating: false,
      isConnected: false,
      connectionStatus: 'disconnected',
      reconnectAttempt: null,
      reconnectMaxAttempts: null,
      reconnectRetryInMs: null,
      connectionError: null,
      reconnectRetryStartedAt: null,
      loadError: null,
      pendingTool: null,
      executingTool: null,
      lastCompletedTool: null,
      showInitialSystem: false,
      chatConfig: null,
      needsInitialStep: false,
      initialStepStream: undefined,
      currentBranch: 'main',
      logOffset: 0,
      hasMoreBefore: false,
      isWindowHydrated: false,
    });
  }
  // mergeIntoObservable treats an undefined value as "delete this key". `data`
  // is required, so a stray `{ data: undefined }` (e.g. an API call resolving
  // to undefined) would wipe it and leave a dataless entry that crashes every
  // reader. Drop it from the merge so existing/default data is preserved.
  if ('data' in update && update.data === undefined) {
    const { data: _ignored, ...rest } = update;
    mergeIntoObservable(conversations$.get(id), rest);
  } else {
    mergeIntoObservable(conversations$.get(id), update);
  }
}

export function addMessage(id: string, message: Message | StreamingMessage) {
  const conv = conversations$.get(id);
  if (conv) {
    conv.data.log.push(message);
  }
}

/** Update the _status of the last message with the given timestamp */
export function setMessageStatus(
  id: string,
  timestamp: string,
  status: 'pending' | 'sent' | 'failed',
  error?: string
) {
  const conv = conversations$.get(id);
  if (!conv) return;
  const log = conv.data.log.get();
  // Find the message by timestamp (searching from end since it's usually the latest)
  for (let i = log.length - 1; i >= 0; i--) {
    if (log[i].timestamp === timestamp) {
      conv.data.log[i]._status.set(status);
      if (error !== undefined) {
        conv.data.log[i]._error.set(error);
      }
      break;
    }
  }
}

/** Remove a message by timestamp */
export function removeMessage(id: string, timestamp: string) {
  const conv = conversations$.get(id);
  if (!conv) return;
  const log = conv.data.log.get();
  const idx = log.findIndex((m) => m.timestamp === timestamp);
  if (idx !== -1) {
    conv.data.log.splice(idx, 1);
  }
}

export function setGenerating(id: string, isGenerating: boolean) {
  updateConversation(id, { isGenerating });
}

export function setConnected(id: string, isConnected: boolean) {
  updateConversation(id, {
    isConnected,
    connectionStatus: isConnected ? 'connected' : 'disconnected',
    reconnectAttempt: null,
    reconnectMaxAttempts: null,
    reconnectRetryInMs: null,
    connectionError: null,
  });
}

export function setConnectionStatus(
  id: string,
  status: ConversationConnectionStatus,
  options?: {
    attempt?: number;
    maxAttempts?: number;
    retryInMs?: number;
    error?: string | null;
  }
) {
  updateConversation(id, {
    isConnected: status === 'connected',
    connectionStatus: status,
    reconnectAttempt: options?.attempt ?? null,
    reconnectMaxAttempts: options?.maxAttempts ?? null,
    reconnectRetryInMs: options?.retryInMs ?? null,
    reconnectRetryStartedAt: status === 'reconnecting' && options?.retryInMs ? Date.now() : null,
    connectionError: options?.error ?? null,
  });
}

export function setPendingTool(id: string, toolId: string | null, tooluse: ToolUse | null) {
  updateConversation(id, {
    pendingTool: toolId && tooluse ? { id: toolId, tooluse } : null,
  });
}

export function setExecutingTool(
  id: string,
  toolId: string | null,
  tooluse: ToolUse | null,
  startedAt?: number
) {
  const update: Partial<ConversationState> = {
    executingTool:
      toolId && tooluse
        ? { id: toolId, tooluse, startedAt: startedAt ?? Date.now(), partialOutput: '' }
        : null,
  };
  if (toolId) {
    // Clear the completion badge when a new tool starts executing
    update.lastCompletedTool = null;
  }
  updateConversation(id, update);
}

export function setToolOutput(id: string, output: string) {
  const conversation = conversations$.get(id);
  if (!conversation) return;
  const executing = conversation.executingTool.get();
  if (!executing) return;
  conversation.executingTool.set({
    ...executing,
    partialOutput: (executing.partialOutput || '') + output,
  });
}

export function setToolComplete(
  id: string,
  toolName: string,
  durationMs: number,
  success: boolean
) {
  updateConversation(id, {
    executingTool: null,
    lastCompletedTool: { toolName, durationMs, completedAt: Date.now(), success },
  });
}

// Initialize a new conversation in the store
export function initConversation(
  id: string,
  data?: ConversationResponse,
  options?: { needsInitialStep?: boolean; initialStepStream?: boolean }
) {
  const initial: ConversationState = {
    data: data || {
      id,
      name: '',
      log: [],
      logfile: id,
      branches: {},
      workspace: '/default/workspace',
    },
    isGenerating: false,
    isConnected: false,
    connectionStatus: 'disconnected',
    reconnectAttempt: null,
    reconnectMaxAttempts: null,
    reconnectRetryInMs: null,
    connectionError: null,
    reconnectRetryStartedAt: null,
    loadError: null,
    pendingTool: null,
    executingTool: null,
    lastCompletedTool: null,
    showInitialSystem: false,
    chatConfig: null,
    needsInitialStep: options?.needsInitialStep ?? false,
    initialStepStream: options?.initialStepStream,
    currentBranch: 'main',
    logOffset: 0,
    hasMoreBefore: false,
    // Treat pre-supplied data (e.g. demo conversations) as hydrated;
    // placeholder conversations created without data are NOT hydrated.
    isWindowHydrated: data !== undefined,
  };
  conversations$.set(id, initial);
}

export function setNeedsInitialStep(id: string, needsInitialStep: boolean) {
  updateConversation(id, { needsInitialStep });
}

export function clearInitialStepState(id: string) {
  const conversation = conversations$.get(id);
  if (!conversation) return;

  batch(() => {
    conversation.needsInitialStep.set(false);
    conversation.initialStepStream.delete();
  });
}

export function setMaxTokens(id: string, maxTokens: number | undefined) {
  updateConversation(id, { maxTokens });
}

export function setTemperature(id: string, temperature: number | undefined) {
  updateConversation(id, { temperature });
}

export function setTopP(id: string, topP: number | undefined) {
  updateConversation(id, { topP });
}

// Index translation helpers — all server-bound ops and index-keyed maps use absolute indices.
export function toAbsoluteIndex(logOffset: number, localIndex: number): number {
  return logOffset + localIndex;
}
export function toLocalIndex(logOffset: number, absoluteIndex: number): number {
  return absoluteIndex - logOffset;
}

// Update conversation data in the store, extracting window metadata from the response.
// 'before' in a paged response = absolute index of log[0] when has_more=true.
export function updateConversationData(id: string, data: ConversationResponse) {
  const conv = conversations$.get(id);
  if (!conv) return;
  // Derive offset: server sets 'before = start' only when has_more (start > 0).
  const logOffset = data.has_more ? (data.before ?? 0) : 0;
  conv.data.set(data);
  conv.logOffset.set(logOffset);
  conv.hasMoreBefore.set(data.has_more ?? false);
  conv.isWindowHydrated.set(true);
}

/** Switch to a different branch, updating the displayed log */
export function setCurrentBranch(id: string, branch: string) {
  const conv = conversations$.get(id);
  if (!conv) return;
  const branches = conv.data.branches?.get();
  if (!branches || !branches[branch]) return;
  conv.currentBranch.set(branch);
  conv.data.log.set(branches[branch]);
}

/** Replace the entire log (used after server-side edit).
 *  Server mutations (edit/delete/rerun) return the full log, so window metadata
 *  is reset to a full-log state (offset=0, no older messages). */
export function replaceLog(id: string, log: Message[]) {
  const conv = conversations$.get(id);
  if (!conv) return;
  conv.data.log.set(log);
  conv.logOffset.set(0);
  conv.hasMoreBefore.set(false);
  conv.isWindowHydrated.set(true);
}

/** Prepend an older page of messages (for "load older" UX in Slice 2).
 *  Updates logOffset and hasMoreBefore to reflect the newly extended window. */
export function prependLogPage(
  id: string,
  olderLog: Message[],
  olderOffset: number,
  hasMoreBefore: boolean
) {
  const conv = conversations$.get(id);
  if (!conv) return;
  const currentLog = conv.data.log.get() as Message[];
  conv.data.log.set([...olderLog, ...currentLog]);
  conv.logOffset.set(olderOffset);
  conv.hasMoreBefore.set(hasMoreBefore);
}

/** Update branch data */
export function updateBranches(id: string, branches: Record<string, Message[]>) {
  const conv = conversations$.get(id);
  if (!conv) return;
  conv.data.branches.set(branches);
}

// Update conversation name in the store
export function updateConversationName(id: string, name: string) {
  const conv = conversations$.get(id);
  if (conv?.data) {
    conv.data.name.set(name);
    console.log(`[conversations] Updated conversation name: ${id} -> "${name}"`);
  }
}

// Initialize conversations with their data
export async function initializeConversations(
  api: { getConversation: (id: string) => Promise<ConversationResponse> },
  conversationIds: string[],
  limit: number = 10
) {
  // Initialize all conversations in store first
  conversationIds.forEach((id) => {
    if (!conversations$.get(id)) {
      // Check if this is a demo conversation
      const demoConv = demoConversations.find((conv) => conv.id === id);
      if (demoConv) {
        const messages = getDemoMessages(demoConv.id);
        initConversation(id, {
          id: demoConv.id,
          name: demoConv.name,
          log: messages,
          logfile: id,
          branches: {},
          workspace: demoConv.workspace || '/demo/workspace',
        });
        return;
      }
      initConversation(id);
    }
  });

  // Then load data for the first N non-demo conversations
  const toLoad = conversationIds
    .filter((id) => !demoConversations.some((conv) => conv.id === id))
    .slice(0, limit);

  if (toLoad.length === 0) {
    console.log('[conversations] No non-demo conversations to load');
    return;
  }

  console.log(`[conversations] Loading ${toLoad.length} conversations into store`);

  // Load conversations in parallel
  const results = await Promise.allSettled(
    toLoad.map(async (id) => {
      try {
        const data = await api.getConversation(id);
        updateConversationData(id, data);
        return id;
      } catch (error) {
        console.error(`Failed to load conversation ${id}:`, error);
        throw error;
      }
    })
  );

  // Log results
  const succeeded = results.filter(
    (r): r is PromiseFulfilledResult<string> => r.status === 'fulfilled'
  );
  const failed = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected');

  if (succeeded.length) {
    console.log(
      `[conversations] Loaded ${succeeded.length} conversations:`,
      succeeded.map((r) => r.value)
    );
  }
  if (failed.length) {
    console.warn(
      `[conversations] Failed to load ${failed.length} conversations:`,
      failed.map((r) => r.reason)
    );
  }
}
