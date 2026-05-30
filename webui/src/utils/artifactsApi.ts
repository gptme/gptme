import type { ApiError } from '@/types/api';
import type { Artifact, ArtifactListResponse } from '@/types/artifact';
import { useApi } from '@/contexts/ApiContext';
import { useMemo } from 'react';

async function readApiError(response: Response, fallback: string): Promise<string> {
  try {
    const error = (await response.json()) as ApiError;
    if (typeof error.error === 'string') {
      return error.error;
    }
    if (typeof error.error?.message === 'string') {
      return error.error.message;
    }
  } catch {
    // Fall through to the generic message when the error body is not JSON.
  }

  return `${fallback} (${response.status})`;
}

export function useArtifactsApi() {
  const { api } = useApi();

  return useMemo(() => {
    async function listArtifacts(conversationId: string): Promise<Artifact[]> {
      const url = `${api.baseUrl}/api/v2/conversations/${conversationId}/artifacts`;

      const response = await fetch(url, {
        headers: api.authHeader ? { Authorization: api.authHeader } : undefined,
      });

      if (!response.ok) {
        throw new Error(await readApiError(response, 'Failed to load artifacts'));
      }

      const data = (await response.json()) as ArtifactListResponse;
      return data.artifacts;
    }

    return {
      listArtifacts,
    };
  }, [api.baseUrl, api.authHeader]);
}
