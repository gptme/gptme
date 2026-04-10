import type { FileType, FilePreview } from '@/types/workspace';
import type { WorkspaceRoot } from '@/stores/workspaceExplorer';
import { useApi } from '@/contexts/ApiContext';
import { useMemo } from 'react';

export function useWorkspaceApi() {
  const { api } = useApi();

  return useMemo(() => {
    async function listWorkspace(
      conversationId: string,
      path?: string,
      showHidden = false,
      root: WorkspaceRoot = 'workspace'
    ): Promise<FileType[]> {
      const params = new URLSearchParams({ show_hidden: String(showHidden) });
      if (root !== 'workspace') params.set('root', root);
      const pathSegment = path ? '/' + path.split('/').map(encodeURIComponent).join('/') : '';
      const url = `${api.baseUrl}/api/v2/conversations/${conversationId}/workspace${pathSegment}?${params}`;

      const response = await fetch(url, {
        headers: api.authHeader ? { Authorization: api.authHeader } : undefined,
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Failed to list workspace');
      }

      return response.json();
    }

    async function previewFile(
      conversationId: string,
      path: string,
      root: WorkspaceRoot = 'workspace'
    ): Promise<FilePreview> {
      const params = new URLSearchParams();
      if (root !== 'workspace') params.set('root', root);
      const query = params.toString() ? `?${params}` : '';
      const pathSegment = path.split('/').map(encodeURIComponent).join('/');
      const url = `${api.baseUrl}/api/v2/conversations/${conversationId}/workspace/${pathSegment}/preview${query}`;

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

    async function downloadFile(
      conversationId: string,
      path: string,
      root: WorkspaceRoot = 'workspace'
    ): Promise<void> {
      const params = new URLSearchParams();
      if (root !== 'workspace') params.set('root', root);
      const query = params.toString() ? `?${params}` : '';
      const pathSegment = path.split('/').map(encodeURIComponent).join('/');
      const url = `${api.baseUrl}/api/v2/conversations/${conversationId}/workspace/${pathSegment}/download${query}`;

      const response = await fetch(url, {
        headers: api.authHeader ? { Authorization: api.authHeader } : undefined,
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Failed to download file');
      }

      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = path.split('/').pop() || 'download';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
    }

    return {
      listWorkspace,
      previewFile,
      downloadFile,
    };
  }, [api.baseUrl, api.authHeader]);
}
