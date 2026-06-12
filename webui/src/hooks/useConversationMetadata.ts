import { useCallback } from 'react';
import { useApi } from '@/contexts/ApiContext';

/**
 * Hook for managing conversation metadata (starred, description, tags) via
 * the server-side metadata.toml sidecar API.
 *
 * Unlike the old localStorage approach, metadata persists across devices,
 * browsers, and conversation file migration.
 */
export function useConversationMetadata() {
  const { api } = useApi();

  const toggleStar = useCallback(
    async (conversationId: string, currentlyStarred: boolean): Promise<boolean> => {
      try {
        await api.patchConversationMetadata(conversationId, {
          starred: !currentlyStarred,
        });
        return !currentlyStarred;
      } catch (err) {
        console.error('Failed to toggle star:', err);
        return currentlyStarred; // revert on error
      }
    },
    [api]
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
