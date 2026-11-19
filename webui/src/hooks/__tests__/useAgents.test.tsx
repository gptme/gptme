import { renderHook } from '@testing-library/react';
import { useAgents } from '../useAgents';

jest.mock('@/hooks/useConversationsInfiniteQuery', () => ({
  useConversationsInfiniteQuery: () => ({
    data: {
      pages: [
        {
          conversations: [
            { id: 'a', name: 'Chat', modified: 100, agent_path: '/agents/bob', agent_name: 'Bob' },
          ],
        },
      ],
    },
    isLoading: false,
    error: null,
  }),
}));
jest.mock('@/stores/servers', () => {
  const { observable } = jest.requireActual('@legendapp/state');
  return { serverRegistry$: observable({ activeServerId: 'primary' }) };
});

it('tags agents from the primary-only API query with its owning server', () => {
  const { result } = renderHook(() => useAgents());
  expect(result.current.agents[0]).toMatchObject({ path: '/agents/bob', serverId: 'primary' });
});
