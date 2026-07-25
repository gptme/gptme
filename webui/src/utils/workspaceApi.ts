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
      root: WorkspaceRoot = 'workspace',
      signal?: AbortSignal
    ): Promise<FilePreview> {
      const params = new URLSearchParams();
      if (root !== 'workspace') params.set('root', root);
      const query = params.toString() ? `?${params}` : '';
      const pathSegment = path.split('/').map(encodeURIComponent).join('/');
      const url = `${api.baseUrl}/api/v2/conversations/${conversationId}/workspace/${pathSegment}/preview${query}`;

      const response = await fetch(url, {
        headers: api.authHeader ? { Authorization: api.authHeader } : undefined,
        signal,
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Failed to preview file');
      }

      const contentType = response.headers.get('Content-Type') ?? '';
      if (contentType.startsWith('image/')) {
        return {
          type: 'image',
          content: URL.createObjectURL(await response.blob()),
        };
      }
      if (contentType.startsWith('model/')) {
        // GLB and USDZ are self-contained binary formats — no external relative
        // references. Fetch via the authenticated client and return a blob URL,
        // exactly as images are handled. This ensures bearer-authenticated
        // deployments work correctly.
        const isSelfContained = /\.(glb|usdz)$/i.test(path);
        if (isSelfContained) {
          return {
            type: 'model3d',
            content: URL.createObjectURL(await response.blob()),
            mime_type: contentType,
          };
        }
        // glTF (.gltf) files reference sibling buffers and textures via relative
        // URIs. A blob URL strips the workspace directory as the resolution base,
        // breaking those references. Return the direct workspace URL instead.
        // On bearer-authenticated deployments, set an auth cookie via
        // POST /api/v2/auth/cookie so that model-viewer's sub-requests are
        // authenticated automatically.
        //
        // TODO: <model-viewer> fetches sibling resources as plain browser
        // requests and cannot inject the Authorization header. Bearer-auth
        // deployments will fail to load buffers/textures until either:
        //   a) a /api/v2/auth/cookie endpoint is added (server sets a session
        //      cookie that browsers include automatically), or
        //   b) we pre-fetch all assets client-side and bundle into a data-URI.
        //
        // TODO: when root=attachments, the query param (?root=attachments) is
        // included in the model URL but model-viewer resolves sibling URIs
        // relative to the model path, dropping the query string. Those sibling
        // requests therefore resolve against the workspace root instead of the
        // attachments root and will 404. Fix requires the browse endpoint to
        // infer root context from the parent path or a sticky session param.
        const workspaceUrl = `${api.baseUrl}/api/v2/conversations/${conversationId}/workspace/${pathSegment}${query}`;
        return { type: 'model3d', content: workspaceUrl, mime_type: contentType };
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
