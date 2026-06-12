import { useInfiniteQuery } from '@tanstack/react-query';
import { useApi } from '@/contexts/ApiContext';
import { use$ } from '@legendapp/state/react';
import type { ConversationSummary } from '@/types/conversation';

export function useConversationsInfiniteQuery(enabled: boolean = true) {
  const { api, connectionConfig } = useApi();
  const isConnected = use$(api.isConnected$);

  return useInfiniteQuery({
    // Remove isConnected from queryKey to avoid a second query identity when
    // isConnected flips from false→true on auto-connect. The `enabled` flag
    // already controls when the query fires. staleTime=30s prevents redundant
    // refetches within a fresh window (common during auto-connect handshake).
    queryKey: ['conversations', connectionConfig.baseUrl],
    queryFn: async ({ pageParam }: { pageParam: number | undefined }) => {
      try {
        const result = await api.getConversationsPaginated(pageParam, 50);
        console.log('Fetched conversations page:', result);
        return result;
      } catch (err) {
        console.error('Failed to fetch conversations:', err);
        throw err;
      }
    },
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage: {
      conversations: ConversationSummary[];
      nextCursor: number | undefined;
    }) => lastPage.nextCursor,
    enabled: isConnected && enabled,
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}
