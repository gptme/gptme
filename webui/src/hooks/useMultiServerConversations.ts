import { useQueries } from '@tanstack/react-query';
import { createApiClient } from '@/utils/api';
import { use$ } from '@legendapp/state/react';
import { serverRegistry$ } from '@/stores/servers';
import type { ConversationSummary } from '@/types/conversation';
import { useMemo, useRef } from 'react';

/**
 * Fetches conversation lists from all connected servers (except the primary/active one,
 * which is handled by the existing useConversationsInfiniteQuery).
 *
 * Returns conversations tagged with serverId/serverName for the unified view.
 */
export function useSecondaryServerConversations() {
  const registry = use$(serverRegistry$);
  const clientsRef = useRef<Map<string, ReturnType<typeof createApiClient>>>(new Map());

  // Determine which servers are secondary (connected but not the active/primary)
  const secondaryServers = useMemo(() => {
    return registry.servers.filter(
      (s) => registry.connectedServerIds.includes(s.id) && s.id !== registry.activeServerId
    );
  }, [registry]);

  // Lazily create/reuse API clients for secondary servers
  const getClient = (serverId: string) => {
    const server = registry.servers.find((s) => s.id === serverId);
    if (!server) return null;

    const authHeader =
      server.useAuthToken && server.authToken ? `Bearer ${server.authToken}` : null;

    const existing = clientsRef.current.get(serverId);
    if (existing && existing.baseUrl === server.baseUrl && existing.authHeader === authHeader) {
      return existing;
    }

    const client = createApiClient(server.baseUrl, authHeader);
    clientsRef.current.set(serverId, client);
    return client;
  };

  // Clean up clients for servers that are no longer secondary
  const activeSecondaryIds = new Set(secondaryServers.map((s) => s.id));
  for (const [id] of clientsRef.current) {
    if (!activeSecondaryIds.has(id)) {
      clientsRef.current.delete(id);
    }
  }

  const queries = useQueries({
    queries: secondaryServers.map((server) => ({
      queryKey: ['secondary-conversations', server.id, server.baseUrl, server.authToken ?? ''],
      queryFn: async (): Promise<ConversationSummary[]> => {
        const client = getClient(server.id);
        if (!client) return [];

        try {
          const result = await client.getConversationsPaginated(0, 50);
          // Tag each conversation with server info
          return result.conversations.map((conv) => ({
            ...conv,
            serverId: server.id,
            serverName: server.name,
          }));
        } catch (error) {
          console.warn(`[MultiServer] Failed to fetch from "${server.name}":`, error);
          return [];
        }
      },
      enabled: true,
      staleTime: 30_000, // 30s — secondary servers don't need real-time freshness
      gcTime: 5 * 60 * 1000,
      refetchOnWindowFocus: false,
      retry: 1,
    })),
  });

  const secondaryConversations = useMemo(() => {
    return queries.flatMap((q) => q.data ?? []);
  }, [queries]);

  const isAnyLoading = queries.some((q) => q.isLoading);

  return {
    secondaryConversations,
    isLoading: isAnyLoading,
    /** Number of servers that are connected (including primary) */
    connectedServerCount: registry.connectedServerIds.length,
  };
}
