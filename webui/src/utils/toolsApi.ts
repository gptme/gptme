import type { Tool, ToolListResponse } from '@/types/tool';
import { useApi } from '@/contexts/ApiContext';
import { withLocalAddressSpace } from '@/utils/addressSpace';
import { useMemo } from 'react';

export function useToolsApi() {
  const { api } = useApi();

  return useMemo(() => {
    async function listTools(signal?: AbortSignal): Promise<Tool[]> {
      const url = `${api.baseUrl}/api/v2/tools`;

      const response = await fetch(
        url,
        withLocalAddressSpace(url, {
          headers: api.authHeader ? { Authorization: api.authHeader } : undefined,
          signal,
        })
      );

      if (!response.ok) {
        throw new Error(`Failed to load tools (${response.status})`);
      }

      const data = (await response.json()) as ToolListResponse;
      return data.tools;
    }

    return { listTools };
  }, [api.baseUrl, api.authHeader]);
}
