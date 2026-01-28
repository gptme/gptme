import { mergeIntoObservable, observable } from '@legendapp/state';
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
}

export interface ConversationState {
  // The conversation data
  data: ConversationResponse;
  // Whether this conversation is currently generating
  isGenerating: boolean;
  // Whether this conversation has an active event stream
  isConnected: boolean;
  // Any pending tool
  pendingTool: PendingTool | null;
  // Any executing tool
  executingTool: ExecutingTool | null;
  // Last received message
  lastMessage?: Message;
  // Whether to show the initial system message
  showInitialSystem: boolean;
  // The chat config
  chatConfig: ChatConfig | null;
  // Whether this conversation needs initial step after connecting
  // Used to fix race condition where step() was called before event subscription
  needsInitialStep: boolean;
}

// Central store for all conversations
export const conversations$ = observable(new Map<string, ConversationState>());

// Currently selected conversation
export const selectedConversation$ = observable<string>(demoConversations[0].id);

// Helper functions
export function updateConversation(id: string, update: Partial<ConversationState>) {
  if (!conversations$.get(id)) {
    // Initialize with defaults if conversation doesn't exist
    conversations$.set(id, {
      data: { id, name: '', log: [], logfile: id, branches: {}, workspace: '/default/workspace' },
      isGenerating: false,
      isConnected: false,
      pendingTool: null,
      executingTool: null,
      showInitialSystem: false,
      chatConfig: null,
      needsInitialStep: false,
    });
  }
  mergeIntoObservable(conversations$.get(id), update);
}

export function addMessage(id: string, message: Message | StreamingMessage) {
  const conv = conversations$.get(id);
  if (conv) {
    conv.data.log.push(message);
  }
}

export function setGenerating(id: string, isGenerating: boolean) {
  updateConversation(id, { isGenerating });
}

export function setConnected(id: string, isConnected: boolean) {
  updateConversation(id, { isConnected });
}

export function setPendingTool(id: string, toolId: string | null, tooluse: ToolUse | null) {
  updateConversation(id, {
    pendingTool: toolId && tooluse ? { id: toolId, tooluse } : null,
  });
}

export function setExecutingTool(id: string, toolId: string | null, tooluse: ToolUse | null) {
  updateConversation(id, {
    executingTool: toolId && tooluse ? { id: toolId, tooluse } : null,
  });
}

// Initialize a new conversation in the store
export function initConversation(
  id: string,
  data?: ConversationResponse,
  options?: { needsInitialStep?: boolean }
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
    pendingTool: null,
    executingTool: null,
    showInitialSystem: false,
    chatConfig: null,
    needsInitialStep: options?.needsInitialStep ?? false,
  };
  conversations$.set(id, initial);
}

export function setNeedsInitialStep(id: string, needsInitialStep: boolean) {
  updateConversation(id, { needsInitialStep });
}

// Update conversation data in the store
export function updateConversationData(id: string, data: ConversationResponse) {
  conversations$.get(id)?.data.set(data);
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
