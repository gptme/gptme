/**
 * Offline demo ApiClient.
 *
 * `createDemoApiClient` returns an {@link IApiClient} that needs no live gptme
 * backend, no auth, and makes no network calls. It powers sales demos,
 * onboarding flows, and trade-show presentations from a static bundle (see the
 * `?demo=1` flag wired in `connectionConfig.isDemoMode`).
 *
 * Slice 1 added the structural seam (IApiClient + stub methods).
 * Slice 2 (this file) backs the key read paths with recorded fixtures so a
 * visitor can open the demo, see a real conversation, and browse history
 * without any backend. Write/stream paths remain stubs until slice 3.
 */
import { observable } from '@legendapp/state';
import type { ConnectionProbeResult, IApiClient } from '@/utils/api';
import type { UserInfo, ConversationResponse, ChatConfig, ServerHealth } from '@/types/api';
import type { Message, ConversationSummary } from '@/types/conversation';
import { ToolFormat } from '@/types/api';

/** Thrown by demo-client paths that have no recorded fixture yet. */
export class DemoModeError extends Error {
  constructor(method: string) {
    super(`Demo mode: ${method}() is not available without a live backend`);
    this.name = 'DemoModeError';
  }
}

const DEMO_BASE_URL = 'demo://offline';
const DEMO_CONV_ID = 'demo/gptme-intro';

/** Pre-recorded fixture conversation shown in demo mode. */
const DEMO_MESSAGES: Message[] = [
  {
    role: 'system',
    content:
      'You are gptme, a personal AI assistant that runs in the terminal. ' +
      'You have access to shell, Python, and browser tools. ' +
      'You are helpful, direct, and concise.',
    timestamp: '2026-01-01T00:00:00Z',
  },
  {
    role: 'user',
    content: 'Write a Python function to compute the Fibonacci sequence and run it',
    timestamp: '2026-01-01T00:00:01Z',
  },
  {
    role: 'assistant',
    content:
      "Here's a simple iterative Fibonacci function:\n\n" +
      '```python\ndef fibonacci(n: int) -> list[int]:\n' +
      '    """Return the first n Fibonacci numbers."""\n' +
      '    if n <= 0:\n        return []\n' +
      '    if n == 1:\n        return [0]\n' +
      '    seq = [0, 1]\n' +
      '    while len(seq) < n:\n' +
      '        seq.append(seq[-1] + seq[-2])\n' +
      '    return seq\n' +
      '\nprint(fibonacci(10))\n```',
    timestamp: '2026-01-01T00:00:02Z',
  },
  {
    role: 'tool',
    content: '```stdout\n[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]\n```',
    timestamp: '2026-01-01T00:00:03Z',
  },
  {
    role: 'assistant',
    content:
      'The first 10 Fibonacci numbers: `[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]` ✓\n\n' +
      'Try asking me to modify it — add memoization, extend to 20 terms, or compute in reverse.',
    timestamp: '2026-01-01T00:00:04Z',
  },
];

const DEMO_CONV_SUMMARY: ConversationSummary = {
  id: DEMO_CONV_ID,
  name: 'gptme demo — Fibonacci sequence',
  modified: 1751328004, // 2026-01-01T00:00:04Z as Unix seconds
  messages: DEMO_MESSAGES.length,
  last_message_role: 'assistant',
  last_message_preview: 'The first 10 Fibonacci numbers: [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]',
  readonly: true,
};

const DEMO_CONV_RESPONSE: ConversationResponse = {
  id: DEMO_CONV_ID,
  name: DEMO_CONV_SUMMARY.name,
  log: DEMO_MESSAGES,
  logfile: DEMO_CONV_ID,
  branches: { main: DEMO_MESSAGES },
  workspace: '/demo',
};

const DEMO_USER_INFO: UserInfo = {
  name: 'Demo User',
};

const DEMO_CHAT_CONFIG: ChatConfig = {
  chat: {
    name: null,
    model: 'gptme-demo',
    tools: null,
    tool_format: ToolFormat.MARKDOWN,
    stream: false,
    interactive: false,
    workspace: '/demo',
  },
  env: {},
  mcp: { enabled: false, auto_start: false, servers: [] },
};

/**
 * Build an offline demo client. Parameter types are inferred from the
 * `IApiClient` contract via contextual typing, so the giant streaming-callback
 * signatures never need to be hand-copied here.
 */
export function createDemoApiClient(baseUrl: string = DEMO_BASE_URL): IApiClient {
  const isConnected$ = observable(true);
  const lastConnectionResult$ = observable<ConnectionProbeResult | null>({
    ok: true,
    url: baseUrl,
  });
  const sessions$ = observable(new Map<string, string>());
  const userInfo$ = observable<UserInfo | null>(DEMO_USER_INFO);

  // In-memory store for conversations created during the demo session.
  const localConversations = new Map<string, ConversationResponse>();

  const notImpl = (method: string): never => {
    throw new DemoModeError(method);
  };

  const client: IApiClient = {
    baseUrl,
    authHeader: null,
    isConnected$,
    lastConnectionResult$,
    sessions$,
    userInfo$,

    // Connection lifecycle — the demo is permanently "connected".
    checkConnection: async () => true,
    setConnected: (connected) => {
      isConnected$.set(connected);
    },
    cancelPendingRequests: async () => {},

    // Streaming — no SSE in the static bundle; slice 3 will replay fixtures.
    subscribeToEvents: async () => {},
    closeEventStream: () => {},
    interruptGeneration: async () => {},

    // User / server identity — serve demo stubs.
    getUserInfo: async () => DEMO_USER_INFO,
    getServerInfo: async () => ({ version: 'demo' }),
    getServerHealth: async (): Promise<ServerHealth> => ({
      session_count: 0,
      generating_count: 0,
      idle_count: 0,
      health: 'green',
      slots: [],
    }),

    // Conversation lists — include the fixture demo conversation.
    getConversations: async () => [DEMO_CONV_SUMMARY],
    searchConversations: async (query) => {
      const q = query.toLowerCase();
      if (DEMO_CONV_SUMMARY.name.toLowerCase().includes(q)) {
        return [DEMO_CONV_SUMMARY];
      }
      for (const conv of localConversations.values()) {
        if (conv.name.toLowerCase().includes(q)) {
          const summary: ConversationSummary = {
            id: conv.id,
            name: conv.name,
            modified: Date.now() / 1000,
            messages: conv.log.length,
          };
          return [summary];
        }
      }
      return [];
    },
    getConversationsPaginated: async () => ({
      conversations: [DEMO_CONV_SUMMARY],
      nextCursor: undefined,
    }),
    getExternalSessions: async () => [],
    getSessions: async () => [],

    // Conversation detail — serve the fixture or local in-memory conversations.
    getConversation: async (logfile) => {
      if (logfile === DEMO_CONV_ID) return DEMO_CONV_RESPONSE;
      const local = localConversations.get(logfile);
      if (local) return local;
      throw new DemoModeError(`getConversation(${logfile})`);
    },

    // Config — return a minimal demo config.
    getChatConfig: async () => DEMO_CHAT_CONFIG,
    updateChatConfig: async () => {},

    // Conversation creation — store in-memory so the UI can navigate to them.
    createConversation: async (logfile, messages) => {
      const existing = localConversations.get(logfile);
      const conv: ConversationResponse = {
        id: logfile,
        name: logfile.split('/').pop() ?? logfile,
        log: messages,
        logfile,
        branches: { main: messages },
        workspace: '/demo',
      };
      if (!existing) {
        localConversations.set(logfile, conv);
      }
      return { status: 'ok', session_id: logfile };
    },
    createConversationWithPlaceholder: async (userMessage, _opts) => {
      const logfile = `demo/conv-${Date.now()}`;
      const message: Message = {
        role: 'user',
        content: userMessage,
        timestamp: new Date().toISOString(),
      };
      const conv: ConversationResponse = {
        id: logfile,
        name: userMessage.slice(0, 40),
        log: [message],
        logfile,
        branches: { main: [message] },
        workspace: '/demo',
      };
      localConversations.set(logfile, conv);
      return logfile;
    },

    // Write / mutation paths — not fixture-backed until slice 3.
    sendMessage: async () => notImpl('sendMessage'),
    editMessage: async () => notImpl('editMessage'),
    deleteMessage: async () => notImpl('deleteMessage'),
    rerunTools: async () => notImpl('rerunTools'),
    uploadFiles: async () => notImpl('uploadFiles'),
    step: async () => notImpl('step'),
    confirmTool: async () => notImpl('confirmTool'),
    deleteConversation: async () => {},
    createAgent: async () => notImpl('createAgent'),
    deleteSession: async () => {},
    getExternalSession: async () => notImpl('getExternalSession'),
  };

  return client;
}
