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

  const registry = use$(serverRegistry$);
  const activeServerId = registry.activeServerId;
  const serverName = registry.servers.find((server) => server.id === activeServerId)?.name;
  const agents = useMemo(() => {
    const conversationSummaries = infiniteData?.pages?.flatMap((page) => page.conversations) || [];
    return extractAgentsFromConversations(
      conversationSummaries.map((conversation) => ({
        ...conversation,
        serverId: activeServerId,
        serverName,
      }))
    );
  }, [infiniteData, activeServerId, serverName]);

  return { agents, isLoading, error };
}
