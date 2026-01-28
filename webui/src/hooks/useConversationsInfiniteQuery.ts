import { useInfiniteQuery } from '@tanstack/react-query';
import { useApi } from '@/contexts/ApiContext';
import { use$ } from '@legendapp/state/react';
import type { ConversationSummary } from '@/types/conversation';

export function useConversationsInfiniteQuery(enabled: boolean = true) {
  const { api, connectionConfig } = useApi();
  const isConnected = use$(api.isConnected$);

  return useInfiniteQuery({
    queryKey: ['conversations', connectionConfig.baseUrl, isConnected],
    queryFn: async ({ pageParam }: { pageParam: number }) => {
      try {
        const result = await api.getConversationsPaginated(pageParam, 50);
        console.log('Fetched conversations page:', result);
        return result;
      } catch (err) {
        console.error('Failed to fetch conversations:', err);
        throw err;
      }
    },
    initialPageParam: 0,
    getNextPageParam: (lastPage: {
      conversations: ConversationSummary[];
      nextCursor: number | undefined;
    }) => lastPage.nextCursor,
    enabled: isConnected && enabled,
    staleTime: 0,
    gcTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}
