import { useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useApi } from '@/contexts/ApiContext';
import { conversationsQueryKey } from '@/hooks/useConversationsInfiniteQuery';
import { getClientForServer } from '@/stores/serverClients';
import { serverRegistry$ } from '@/stores/servers';
import type { IApiClient } from '@/utils/api';

/**
 * Hook for managing conversation metadata (starred, description, tags) via
 * the server-side metadata.toml sidecar API.
 *
 * Unlike the old localStorage approach, metadata persists across devices,
 * browsers, and conversation file migration.
 *
 * Mutations accept an optional serverId so a sidebar row that came from a
 * secondary server is patched through that server's pooled client. Without a
 * serverId the primary client is the owner (ordinary single-server flow).
 */
export function useConversationMetadata() {
  const { api } = useApi();
  const queryClient = useQueryClient();

  const resolveOwner = useCallback(
    (serverId?: string): IApiClient | null => {
      if (!serverId) return api;
      return getClientForServer(serverId);
    },
    [api]
  );

  const invalidateOwner = useCallback(
    async (serverId: string | undefined, client: IApiClient) => {
      // Secondary rows live in their own per-server query. Invalidate only the
      // owning query: never the primary list for a secondary mutation.
      const isPrimary = !serverId || serverId === serverRegistry$.peek().activeServerId;
      const queryKey = isPrimary
        ? conversationsQueryKey(client.baseUrl)
        : ['secondary-conversations', serverId];
      await queryClient.invalidateQueries({ queryKey });
    },
    [queryClient]
  );

  const toggleStar = useCallback(
    async (
      conversationId: string,
      currentlyStarred: boolean,
      serverId?: string
    ): Promise<boolean> => {
      const client = resolveOwner(serverId);
      if (!client) {
        // The owner is gone: fail closed rather than mutating the primary server.
        console.error('Failed to toggle star: owning server is no longer available');
        return currentlyStarred;
      }
      try {
        await client.patchConversationMetadata(conversationId, {
          starred: !currentlyStarred,
        });
        await invalidateOwner(serverId, client);
        return !currentlyStarred;
      } catch (err) {
        console.error('Failed to toggle star:', err);
        return currentlyStarred; // revert on error
      }
    },
    [invalidateOwner, resolveOwner]
  );

  const updateDescription = useCallback(
    async (conversationId: string, description: string | null): Promise<void> => {
      try {
        await api.patchConversationMetadata(conversationId, { description });
      } catch (err) {
        console.error('Failed to update description:', err);
      }
    },
    [api]
  );

  const updateTags = useCallback(
    async (conversationId: string, tags: string[] | null): Promise<void> => {
      try {
        await api.patchConversationMetadata(conversationId, { tags });
      } catch (err) {
        console.error('Failed to update tags:', err);
      }
    },
    [api]
  );

  return { toggleStar, updateDescription, updateTags };
}
