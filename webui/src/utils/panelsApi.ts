import { useApi } from '@/contexts/ApiContext';
import { withLocalAddressSpace } from '@/utils/addressSpace';
import type { PanelEntry, PanelListResponse } from '@/types/panels';
import { useMemo } from 'react';

export function usePanelsApi() {
  const { api } = useApi();

  return useMemo(() => {
    async function listPanels(conversationId: string, signal?: AbortSignal): Promise<PanelEntry[]> {
      const url = `${api.baseUrl}/api/v2/conversations/${encodeURIComponent(conversationId)}/panels`;

      const response = await fetch(
        url,
        withLocalAddressSpace(url, {
          headers: api.authHeader ? { Authorization: api.authHeader } : undefined,
          signal,
        })
      );

      if (!response.ok) {
        throw new Error(`Failed to fetch panels (${response.status})`);
      }
      const data = (await response.json()) as PanelListResponse;
      return data.panels;
    }

    return { listPanels };
  }, [api.baseUrl, api.authHeader]);
}
