import { useQueries } from '@tanstack/react-query';
import { use$ } from '@legendapp/state/react';
import { serverRegistry$ } from '@/stores/servers';
import { getClientForServer } from '@/stores/serverClients';
import type { ConversationSummary } from '@/types/conversation';
import { useMemo } from 'react';

/**
 * Fetches conversation lists from all connected servers (except the primary/active one,
 * which is handled by the existing useConversationsInfiniteQuery).
 *
 * Returns conversations tagged with serverId/serverName for the unified view.
 */
export function useSecondaryServerConversations() {
  const registry = use$(serverRegistry$);

  // Determine which servers are secondary (connected but not the active/primary)
  const secondaryServers = useMemo(() => {
    return registry.servers.filter(
      (s) => registry.connectedServerIds.includes(s.id) && s.id !== registry.activeServerId
    );
  }, [registry]);

  const queries = useQueries({
    queries: secondaryServers.map((server) => ({
      queryKey: ['secondary-conversations', server.id, server.baseUrl, server.authToken ?? ''],
      queryFn: async (): Promise<ConversationSummary[]> => {
        const client = getClientForServer(server.id);
        if (!client) return [];

        try {
          const result = await client.getConversationsPaginated(0, 50);
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
      staleTime: 30_000,
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
    connectedServerCount: registry.connectedServerIds.length,
  };
}
