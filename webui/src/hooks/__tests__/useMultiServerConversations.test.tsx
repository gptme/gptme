import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { serverRegistry$ } from '@/stores/servers';
import { DEFAULT_SERVER_CONFIG } from '@/types/servers';
import { useSecondaryServerConversations } from '../useMultiServerConversations';

describe('useSecondaryServerConversations', () => {
  beforeEach(() => {
    serverRegistry$.set({
      servers: [
        {
          ...DEFAULT_SERVER_CONFIG,
          id: 'primary',
          createdAt: 0,
          lastUsedAt: 0,
        },
      ],
      activeServerId: 'primary',
      connectedServerIds: ['primary'],
    });
  });

  it('keeps unchanged combined query results referentially stable', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    const { result, rerender } = renderHook(() => useSecondaryServerConversations(), {
      wrapper,
    });
    const initialConversations = result.current.secondaryConversations;

    rerender();

    expect(result.current.secondaryConversations).toBe(initialConversations);
  });
});
