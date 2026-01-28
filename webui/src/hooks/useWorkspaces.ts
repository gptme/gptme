import { useMemo, useState, useCallback } from 'react';
import { useConversationsInfiniteQuery } from './useConversationsInfiniteQuery';
import { extractWorkspacesFromConversations } from '@/utils/workspaceUtils';
import type { WorkspaceProject } from '@/utils/workspaceUtils';

export function useWorkspaces(enabled = true): {
  workspaces: WorkspaceProject[];
  isLoading: boolean;
  error: Error | null;
  addCustomWorkspace: (path: string) => void;
  removeCustomWorkspace: (path: string) => void;
} {
  const { data: infiniteData, isLoading, error } = useConversationsInfiniteQuery(enabled);
  const [customWorkspaces, setCustomWorkspaces] = useState<WorkspaceProject[]>([]);

  const addCustomWorkspace = useCallback((path: string) => {
    const trimmedPath = path.trim();
    if (!trimmedPath) return;

    setCustomWorkspaces((prev) => {
      // Check if workspace already exists
      if (prev.some((ws) => ws.path === trimmedPath)) {
        return prev;
      }

      // Create new workspace entry
      const name = trimmedPath.split('/').pop() || trimmedPath;
      const newWorkspace: WorkspaceProject = {
        path: trimmedPath,
        name,
        conversationCount: 0,
        lastUsed: new Date().toISOString(),
      };

      return [newWorkspace, ...prev];
    });
  }, []);

  const removeCustomWorkspace = useCallback((path: string) => {
    setCustomWorkspaces((prev) => prev.filter((ws) => ws.path !== path));
  }, []);

  const workspaces = useMemo(() => {
    const conversationSummaries = infiniteData?.pages?.flatMap((page) => page.conversations) || [];

    console.log(
      '[useWorkspaces] Extracted workspaces from cache:',
      conversationSummaries.length,
      'conversations'
    );

    const conversationWorkspaces = extractWorkspacesFromConversations(conversationSummaries);

    // Combine custom workspaces with conversation workspaces, removing duplicates
    const conversationPaths = new Set(conversationWorkspaces.map((ws) => ws.path));
    const uniqueCustomWorkspaces = customWorkspaces.filter((ws) => !conversationPaths.has(ws.path));

    // Sort by last used date, with custom workspaces (recent) appearing first
    const combined = [...uniqueCustomWorkspaces, ...conversationWorkspaces];

    return combined.sort((a, b) => new Date(b.lastUsed).getTime() - new Date(a.lastUsed).getTime());
  }, [infiniteData, customWorkspaces]);

  return {
    workspaces,
    isLoading,
    error,
    addCustomWorkspace,
    removeCustomWorkspace,
  };
}
