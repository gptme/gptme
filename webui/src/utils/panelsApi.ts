import { useApi } from '@/contexts/ApiContext';
import { withLocalAddressSpace } from '@/utils/addressSpace';
import type { IframePanelEntry, PanelListResponse } from '@/types/panels';
import { useMemo } from 'react';

export function usePanelsApi() {
  const { api } = useApi();

  return useMemo(() => {
    async function listPanels(
      conversationId: string,
      signal?: AbortSignal
    ): Promise<IframePanelEntry[]> {
      const baseUrl = withLocalAddressSpace(api.baseUrl);
      const response = await fetch(
        `${baseUrl}/api/v2/conversations/${encodeURIComponent(conversationId)}/panels`,
        { signal }
      );
      if (!response.ok) {
        throw new Error(`Failed to fetch panels (${response.status})`);
      }
      const data = (await response.json()) as PanelListResponse;
      return data.panels;
    }

    return { listPanels };
  }, [api.baseUrl]);
}
