/**
 * Offline demo ApiClient.
 *
 * `createDemoApiClient` returns an {@link IApiClient} that needs no live gptme
 * backend, no auth, and makes no network calls. It powers sales demos,
 * onboarding flows, and trade-show presentations from a static bundle (see the
 * `?demo=1` flag wired in `connectionConfig.isDemoMode`).
 *
 * Slice 1 (this file) is the *seam*: a structurally-complete client that
 * reports "connected", serves empty collections for the read paths, and throws
 * a clear {@link DemoModeError} for the write/stream paths that later slices
 * back with recorded fixtures. Because it is typed against `IApiClient` (the
 * public surface of `ApiClient` with the private brand stripped), the compiler
 * guarantees every public method is accounted for — adding a method to
 * `ApiClient` will fail this file until it is handled here too.
 */
import { observable } from '@legendapp/state';
import type { ConnectionProbeResult, IApiClient } from '@/utils/api';
import type { ServerHealth, UserInfo } from '@/types/api';

/** Thrown by demo-client paths that have no recorded fixture yet (slice 1). */
export class DemoModeError extends Error {
  constructor(method: string) {
    super(`Demo mode: ${method}() is not available without a live backend`);
    this.name = 'DemoModeError';
  }
}

const DEMO_BASE_URL = 'demo://offline';

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
  const userInfo$ = observable<UserInfo | null>(null);

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

    // Streaming — no SSE in the static bundle; later slices replay fixtures.
    subscribeToEvents: async () => {},
    closeEventStream: () => {},
    interruptGeneration: async () => {},

    // Read paths — serve empty collections so the UI renders cleanly.
    getServerInfo: async () => ({ version: 'demo' }),
    getConversations: async () => [],
    searchConversations: async () => [],
    getConversationsPaginated: async () => ({ conversations: [], nextCursor: undefined }),
    getExternalSessions: async () => [],
    getSessions: async () => [],
    getServerHealth: async (): Promise<ServerHealth> => ({
      session_count: 0,
      generating_count: 0,
      idle_count: 0,
      health: 'green',
      slots: [],
    }),

    // Read paths — not yet fixture-backed; later slices will provide recorded data.
    getUserInfo: async () => notImpl('getUserInfo'),
    getConversation: async () => notImpl('getConversation'),
    getChatConfig: async () => notImpl('getChatConfig'),
    getExternalSession: async () => notImpl('getExternalSession'),

    // Write / mutation paths — fixture-backed in later slices.
    createConversation: async () => notImpl('createConversation'),
    createConversationWithPlaceholder: async () => notImpl('createConversationWithPlaceholder'),
    sendMessage: async () => notImpl('sendMessage'),
    editMessage: async () => notImpl('editMessage'),
    deleteMessage: async () => notImpl('deleteMessage'),
    rerunTools: async () => notImpl('rerunTools'),
    uploadFiles: async () => notImpl('uploadFiles'),
    step: async () => notImpl('step'),
    confirmTool: async () => notImpl('confirmTool'),
    updateChatConfig: async () => {},
    deleteConversation: async () => {},
    createAgent: async () => notImpl('createAgent'),
    deleteSession: async () => {},
  };

  return client;
}
