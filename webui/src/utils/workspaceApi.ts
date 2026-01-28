import type { FileType, FilePreview } from '@/types/workspace';
import { useApi } from '@/contexts/ApiContext';
import { useMemo } from 'react';

export function useWorkspaceApi() {
  const { api } = useApi();

  return useMemo(() => {
    async function listWorkspace(
      conversationId: string,
      path?: string,
      showHidden = false
    ): Promise<FileType[]> {
      const url = `${api.baseUrl}/api/v2/conversations/${conversationId}/workspace${path ? `/${encodeURIComponent(path)}` : ''}?show_hidden=${showHidden}`;

      const response = await fetch(url, {
        headers: api.authHeader ? { Authorization: api.authHeader } : undefined,
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Failed to list workspace');
      }

      return response.json();
    }

    async function previewFile(conversationId: string, path: string): Promise<FilePreview> {
      const url = `${api.baseUrl}/api/v2/conversations/${conversationId}/workspace/${encodeURIComponent(path)}/preview`;

      const response = await fetch(url, {
        headers: api.authHeader ? { Authorization: api.authHeader } : undefined,
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Failed to preview file');
      }

      const contentType = response.headers.get('Content-Type');
      if (contentType?.startsWith('image/')) {
        return {
          type: 'image',
          content: URL.createObjectURL(await response.blob()),
        };
      }

      return response.json();
    }

    return {
      listWorkspace,
      previewFile,
    };
  }, [api.baseUrl, api.authHeader]);
}
