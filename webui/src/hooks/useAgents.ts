import { useMemo } from 'react';
import { use$ } from '@legendapp/state/react';
import { serverRegistry$ } from '@/stores/servers';
import { useConversationsInfiniteQuery } from './useConversationsInfiniteQuery';
import { extractAgentsFromConversations, type Agent } from '@/utils/workspaceUtils';

export function useAgents(enabled = true): {
  agents: Agent[];
  isLoading: boolean;
  error: Error | null;
} {
  const { data: infiniteData, isLoading, error } = useConversationsInfiniteQuery(enabled);

  const activeServerId = use$(serverRegistry$.activeServerId);
  const agents = useMemo(() => {
    const conversationSummaries = infiniteData?.pages?.flatMap((page) => page.conversations) || [];
    return extractAgentsFromConversations(
      conversationSummaries.map((conversation) => ({
        ...conversation,
        serverId: activeServerId,
      }))
    );
  }, [infiniteData, activeServerId]);

  return { agents, isLoading, error };
}
