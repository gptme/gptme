import { useInfiniteQuery } from '@tanstack/react-query';
import { useApi } from '@/contexts/ApiContext';
import { use$ } from '@legendapp/state/react';
import type { ConversationSummary } from '@/types/conversation';
import { shouldFetchDetailedConversationSummaries } from '@/utils/conversationSummaryDetail';

// Jest cannot parse import.meta — use Function() so it throws and we fall
// back to process.env, which jest.setup.ts populates.
function getViteEmbeddedMode(): string | undefined {
  try {
    return Function('return import.meta.env.VITE_EMBEDDED_MODE')() as string | undefined;
  } catch {
    return process.env['VITE_EMBEDDED_MODE'];
  }
}

export function useConversationsInfiniteQuery(enabled: boolean = true) {
  const { api, connectionConfig } = useApi();
  const isConnected = use$(api.isConnected$);
  const includeDetail = shouldFetchDetailedConversationSummaries({
    VITE_EMBEDDED_MODE: getViteEmbeddedMode(),
  });

  return useInfiniteQuery({
    // Remove isConnected from queryKey to avoid a second query identity when
    // isConnected flips from false→true on auto-connect. The `enabled` flag
    // already controls when the query fires. staleTime=30s prevents redundant
    // refetches within a fresh window (common during auto-connect handshake).
    queryKey: ['conversations', connectionConfig.baseUrl, includeDetail ? 'detail' : 'summary'],
    queryFn: async ({ pageParam }: { pageParam: string | undefined }) => {
      try {
        return await api.getConversationsPaginated(pageParam, 50, includeDetail);
      } catch (err) {
        console.error('Failed to fetch conversations:', err);
        throw err;
      }
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage: {
      conversations: ConversationSummary[];
      nextCursor: string | undefined;
    }) => lastPage.nextCursor,
    enabled: isConnected && enabled,
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}
