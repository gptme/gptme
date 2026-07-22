/**
 * Offline demo ApiClient.
 *
 * `createDemoApiClient` returns an {@link IApiClient} that needs no live gptme
 * backend, no auth, and makes no network calls. It powers sales demos,
 * onboarding flows, and trade-show presentations from a static bundle (see the
 * `?demo=1` flag wired in `connectionConfig.isDemoMode`).
 *
 * Slice 1 added the structural seam (IApiClient + stub methods).
 * Slice 2 backed the key read paths with recorded fixtures so a visitor can
 * open the demo, see a real conversation, and browse history without any
 * backend. Slice 3 replays a deterministic chat/tool-call flow for new demo
 * conversations.
 */
import { observable } from '@legendapp/state';
import type { ApiCompatibilityWarning, ConnectionProbeResult, IApiClient } from '@/utils/api';
import type {
  UserInfo,
  ConversationResponse,
  ChatConfig,
  ServerHealth,
  SkillListResponse,
} from '@/types/api';
import type { Message, ConversationSummary, ToolUse } from '@/types/conversation';
import { ToolFormat } from '@/types/api';
import { initConversation, setMaxTokens, setTemperature, setTopP } from '@/stores/conversations';

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
  modified: 1767225604, // 2026-01-01T00:00:04Z as Unix seconds
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

type DemoEventCallbacks = Parameters<IApiClient['subscribeToEvents']>[1];

const DEMO_TOOL_ID = 'demo-python-fibonacci';
const DEMO_TOOL_USE: ToolUse = {
  tool: 'shell',
  args: ['python3', '-c', 'print([0, 1, 1, 2, 3, 5, 8, 13])'],
  content: "python3 -c 'print([0, 1, 1, 2, 3, 5, 8, 13])'",
};

const DEMO_TOOL_OUTPUT = '```stdout\n[0, 1, 1, 2, 3, 5, 8, 13]\n```';

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

function makeDemoAssistantMessage(content: string): Message {
  return {
    role: 'assistant',
    content,
    timestamp: new Date().toISOString(),
    metadata: { model: 'gptme-demo' },
  };
}

function makeDemoToolMessage(): Message {
  return {
    role: 'tool',
    content: DEMO_TOOL_OUTPUT,
    timestamp: new Date().toISOString(),
  };
}

function streamAssistant(callbacks: DemoEventCallbacks | undefined, message: Message) {
  callbacks?.onMessageStart();
  for (const token of message.content.match(/.{1,24}/gs) ?? [message.content]) {
    callbacks?.onToken(token);
  }
  callbacks?.onMessageComplete(message);
}

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
  const compatibilityWarning$ = observable<ApiCompatibilityWarning | null>(null);
  const sessions$ = observable(new Map<string, string>());
  const userInfo$ = observable<UserInfo | null>(DEMO_USER_INFO);

  // In-memory store for conversations created during the demo session.
  const localConversations = new Map<string, ConversationResponse>();
  const eventCallbacks = new Map<string, DemoEventCallbacks>();

  const localSummary = (conv: ConversationResponse): ConversationSummary => ({
    id: conv.id,
    name: conv.name,
    modified: Date.now() / 1000,
    messages: conv.log.length,
    last_message_role: conv.log.at(-1)?.role === 'user' ? 'user' : 'assistant',
    last_message_preview: conv.log.at(-1)?.content.slice(0, 96),
  });

  const getLocalConversation = (logfile: string): ConversationResponse => {
    const existing = localConversations.get(logfile);
    if (existing) return existing;

    const conv: ConversationResponse = {
      id: logfile,
      name: logfile.split('/').pop() ?? logfile,
      log: [],
      logfile,
      branches: { main: [] },
      workspace: '/demo',
    };
    localConversations.set(logfile, conv);
    return conv;
  };

  const appendLocalMessage = (logfile: string, message: Message) => {
    const conv = getLocalConversation(logfile);
    const cleanMessage = clone(message);
    delete cleanMessage._status;
    delete cleanMessage._error;
    conv.log.push(cleanMessage);
    conv.branches = { ...conv.branches, main: conv.log };
  };

  const notImpl = (method: string): never => {
    throw new DemoModeError(method);
  };

  const client: IApiClient = {
    baseUrl,
    authHeader: null,
    isConnected$,
    lastConnectionResult$,
    compatibilityWarning$,
    sessions$,
    userInfo$,

    // Connection lifecycle — the demo is permanently "connected".
    checkConnection: async () => true,
    setConnected: (connected) => {
      isConnected$.set(connected);
    },
    cancelPendingRequests: async () => {},

    // Streaming — replay a deterministic in-memory fixture instead of opening SSE.
    subscribeToEvents: async (conversationId, callbacks) => {
      eventCallbacks.set(conversationId, callbacks);
      const sessionId = `demo-session-${conversationId}`;
      sessions$.set(conversationId, sessionId);
      callbacks.onConnectionState?.({ status: 'connected' });
      callbacks.onConnected?.();
    },
    closeEventStream: (conversationId) => {
      eventCallbacks.delete(conversationId);
    },
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

    // Conversation lists — include the fixture demo conversation plus any in-session creations.
    getConversations: async () => [
      DEMO_CONV_SUMMARY,
      ...Array.from(localConversations.values()).map(localSummary),
    ],
    searchConversations: async (query) => {
      const q = query.toLowerCase();
      const results: ConversationSummary[] = [];
      if (DEMO_CONV_SUMMARY.name.toLowerCase().includes(q)) {
        results.push(DEMO_CONV_SUMMARY);
      }
      for (const conv of localConversations.values()) {
        if (conv.name.toLowerCase().includes(q)) {
          results.push(localSummary(conv));
        }
      }
      return results;
    },
    getConversationsPaginated: async () => ({
      conversations: [
        DEMO_CONV_SUMMARY,
        ...Array.from(localConversations.values()).map(localSummary),
      ],
      nextCursor: undefined,
    }),
    getExternalSessions: async () => [],
    getSkills: async (): Promise<SkillListResponse> => ({ skills: [] }),
    getSessions: async () => [],

    // Conversation detail — serve the fixture or local in-memory conversations.
    getConversation: async (logfile) => {
      if (logfile === DEMO_CONV_ID) return clone(DEMO_CONV_RESPONSE);
      const local = localConversations.get(logfile);
      if (local) return clone(local);
      throw new DemoModeError(`getConversation(${logfile})`);
    },
    forkConversation: async (logfile, afterMessage, branch = 'main') => {
      const source =
        logfile === DEMO_CONV_ID
          ? clone(DEMO_CONV_RESPONSE)
          : clone(localConversations.get(logfile));
      if (!source) {
        throw new DemoModeError(`forkConversation(${logfile})`);
      }
      const branchLog = clone(source.branches[branch] ?? source.log);
      const forkLog = branchLog.slice(0, afterMessage + 1);
      const forkId = `demo/conv-${Date.now()}-fork`;
      const forked: ConversationResponse = {
        id: forkId,
        name: `Fork of ${source.name || source.id} @ msg ${afterMessage + 1}`,
        log: forkLog,
        logfile: forkId,
        branches: { main: clone(forkLog) },
        workspace: source.workspace,
      };
      localConversations.set(forkId, forked);
      sessions$.set(forkId, `demo-session-${forkId}`);
      return forkId;
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
        log: clone(messages),
        logfile,
        branches: { main: clone(messages) },
        workspace: '/demo',
      };
      if (!existing) {
        localConversations.set(logfile, conv);
      }
      sessions$.set(logfile, `demo-session-${logfile}`);
      return { status: 'ok', session_id: logfile };
    },
    createConversationWithPlaceholder: async (userMessage, opts) => {
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
      sessions$.set(logfile, `demo-session-${logfile}`);
      initConversation(logfile, clone(conv), {
        needsInitialStep: true,
        initialStepStream: opts?.stream,
      });
      if (opts?.maxTokens !== undefined) {
        setMaxTokens(logfile, opts.maxTokens);
      }
      if (opts?.temperature !== undefined) {
        setTemperature(logfile, opts.temperature);
      }
      if (opts?.topP !== undefined) {
        setTopP(logfile, opts.topP);
      }
      return logfile;
    },

    // Chat replay paths — enough for the core static demo flow.
    sendMessage: async (logfile, message) => {
      appendLocalMessage(logfile, message);
    },
    editMessage: async () => notImpl('editMessage'),
    deleteMessage: async () => notImpl('deleteMessage'),
    rerunTools: async () => notImpl('rerunTools'),
    uploadFiles: async () => notImpl('uploadFiles'),
    transcribeAudio: async () => notImpl('transcribeAudio'),
    step: async (logfile) => {
      const callbacks = eventCallbacks.get(logfile);
      const intro = makeDemoAssistantMessage(
        'I can show the shape of this without a live backend. First I will run a small local-style Fibonacci check.'
      );
      streamAssistant(callbacks, intro);
      appendLocalMessage(logfile, intro);

      callbacks?.onToolPending(DEMO_TOOL_ID, DEMO_TOOL_USE, true);
      callbacks?.onToolExecuting(DEMO_TOOL_ID);
      callbacks?.onToolOutput?.(DEMO_TOOL_ID, DEMO_TOOL_OUTPUT);
      callbacks?.onToolComplete?.(DEMO_TOOL_ID, 420, true);
      const toolMessage = makeDemoToolMessage();
      callbacks?.onMessageAdded(toolMessage);
      appendLocalMessage(logfile, toolMessage);

      const final = makeDemoAssistantMessage(
        'The demo client replayed a tool call from static fixtures and kept the conversation history in memory. A real gptme server is still needed for arbitrary tools, but this path is enough for credential-free product demos.'
      );
      streamAssistant(callbacks, final);
      appendLocalMessage(logfile, final);
    },
    confirmTool: async () => {},
    deleteConversation: async () => {},
    patchConversationMetadata: async () => {},
    getConversationMetadata: async () => ({ starred: false }),
    createAgent: async () => notImpl('createAgent'),
    deleteSession: async () => {},
    getExternalSession: async () => notImpl('getExternalSession'),
    steerExternalSession: async () => notImpl('steerExternalSession'),
  };

  return client;
}
