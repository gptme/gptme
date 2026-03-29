import { useMemo } from 'react';
import { useConversationsInfiniteQuery } from './useConversationsInfiniteQuery';
import { extractAgentsFromConversations, type Agent } from '@/utils/workspaceUtils';

export function useAgents(enabled = true): {
  agents: Agent[];
  isLoading: boolean;
  error: Error | null;
} {
  const { data: infiniteData, isLoading, error } = useConversationsInfiniteQuery(enabled);

  const agents = useMemo(() => {
    const conversationSummaries = infiniteData?.pages?.flatMap((page) => page.conversations) || [];
    return extractAgentsFromConversations(conversationSummaries);
  }, [infiniteData]);

  return { agents, isLoading, error };
}
